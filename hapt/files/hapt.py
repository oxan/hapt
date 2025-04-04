#!/usr/bin/micropython

# Dependencies: micropython, micropython-lib (for requests module), micropython-lib-unix or micropython-lib-unix-src (for os, signal module)

# micropython-lib-unix installs its modules into a unix subfolder, but the modules expect to be able to find
import sys
sys.path.append('/usr/lib/micropython/unix')

import ffi
import select
import socket
import struct
import time
import ujson as json  # use built-in module instead of the library module, which is buggy
import urequests as requests  # not yet stripped of its u prefix in OpenWRT's micropython-lib version
from unix import signal
from unix import os

### Low-level wrappers around the OS interface
class FFI:
	libc = ffi.open('libc.so.6')

	lchown = libc.func('i', 'lchown', 'sII')

	IN_CREATE = 0x00000100
	IN_DELETE = 0x00000200

	inotify_init = libc.func('i', 'inotify_init', '')
	inotify_add_watch = libc.func('i', 'inotify_add_watch', 'isI')
	inotify_rm_watch = libc.func('i', 'inotify_rm_watch', 'ii')

def decode_inotify_event(event):
	# struct inotify_event as defined in inotify(7).
	wd, mask, cookie, length = struct.unpack('iIII', event)
	name = event[struct.calcsize('iIII'):].split(b'\0', 1)[0].decode('utf-8')
	return wd, mask, name

def encode_socket_address(path):
	# The socket.bind() functions takes struct sockaddr_un as parameter, which is defined by (see unix(7)):
	# struct sockaddr_un {
	#    sa_family_t sun_family;    // sa_family_t is an unsigned short
	#    char        sun_path[108];
	# };
	return struct.pack('H108s', socket.AF_UNIX, path)

def listdir(path):
	return [item[0] for item in os.ilistdir(path) if item[0] not in ('.', '..')]

def subprocess(command):
	with os.popen(command, 'r') as stream:
		result = stream.read()
	os.waitpid(-1, None)
	return result

### OpenWRT interface
def ubus_call(path, method, message=None):
	# it might be cleaner to use libubus and FFI, but that seems quite complicated
	if message is not None:
		command = "ubus call %s %s '%s'" % (path, method, json.dumps(message))
	else:
		command = "ubus call %s %s" % (path, method)
	response = subprocess(command)
	try:
		return json.loads(response)
	except ValueError:
		raise ValueError("Failed to parse response of command '%s' as JSON: '%s'" % (command, response))

def curl_call(path, headers, payload):
	# ussl borks on my SSL certificate
	command = "curl -sf '%s' -X POST --data-binary '%s' " % (path, payload)
	for key, value in headers.items():
		command += "-H '%s: %s' " % (key, value)
	return subprocess(command)

def get_config(config, section):
	config = ubus_call('uci', 'get', {'config': config, 'type': section})
	return next(iter(config['values'].values()))

def get_connected_clients(interface):
	try:
		response = ubus_call('hostapd.%s' % interface, 'get_clients')
		return response['clients'].keys()
	except ValueError as e:
		# This can happen if the interface isn't fully up yet, it's fine to continue - we assume nothing is connected
		print("Failed to get connected clients: %s" % e)
		return []

def connect_hostapd_socket(interface):
	remote_address = '/var/run/hostapd/%s' % interface
	local_address  = '/var/run/hapt-%s-%d' % (interface, time.time() % 86400)
	sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
	sock.bind(encode_socket_address(local_address))
	# Make our local socket owned by the `network` group, so that hostapd (running as non-root) is allowed to send messages to it
	# See: https://github.com/openwrt/openwrt/blob/master/package/network/services/hostapd/patches/610-hostapd_cli_ujail_permission.patch
	FFI.lchown(local_address, 101, 101)
	sock.connect(encode_socket_address(remote_address))
	sock.send('ATTACH')
	response = sock.recv(1024)
	if response != b'OK\n':
		raise ValueError('Received invalid response on ATTACH from hostapd: %s' % response)

	return sock

def disconnect_hostapd_socket(sock):
	sock.send('DETACH')
	response = sock.recv(1024)
	if response != b'OK\n':
		raise ValueError('Received invalid response on DETACH from hostapd: %s' % response)
	sock.close()

def get_lease_details(leasefile, mac):
	try:
		with open(leasefile, 'r') as handle:
			for line in handle.readlines():
				parts = line.split()
				if parts[1] == mac:
					return parts[2], parts[3]
	except OSError:
		# lease-file does not exist or is not readable, skip reading it
		pass

	return None, None


### Low-level interface monitor
class InterfaceWatcher:
	def __init__(self, handler, include_interfaces=None):
		self.handler = handler
		self.include_interfaces = include_interfaces

		self.fds  = {}
		self.poll = select.poll()

	def add_interface(self, interface):
		if self.include_interfaces and interface not in self.include_interfaces:
			return

		sock = connect_hostapd_socket(interface)
		print("Connected to hostapd on interface %s" % interface)
		self.fds[sock.fileno()] = ('hostapd', interface, sock)
		self.poll.register(sock.fileno(), select.POLLIN)

	def remove_interface(self, interface):
		for fd, desc in self.fds.items():
			if desc[0] == 'hostapd' and desc[1] == interface:
				self.remove_interface_fd(fd)

	def remove_interface_fd(self, fd):
		try:
			disconnect_hostapd_socket(self.fds[fd][2])
			print("Disconnected from hostapd interface %s" % self.fds[fd][1])
		except Exception as e:
			print("Failed to disconnect from hostapd on interface %s due to %s (%s)" % (self.fds[fd][1], type(e), e))
		self.poll.unregister(fd)
		del self.fds[fd]


	def handle_inotify(self, msg):
		wd, mask, name = decode_inotify_event(msg)
		if wd == self.inotify_wd_parent and name == 'hostapd':
			if mask & FFI.IN_CREATE:
				self.inotify_wd_control = FFI.inotify_add_watch(self.inotify_fd, '/var/run/hostapd', FFI.IN_CREATE | FFI.IN_DELETE)
			if mask & FFI.IN_DELETE:
				FFI.inotify_rm_watch(self.inotify_fd, self.inotify_wd_control)
		elif wd == self.inotify_wd_control:
			if mask & FFI.IN_CREATE:
				self.add_interface(name)
			if mask & FFI.IN_DELETE:
				self.remove_interface(name)

	def setup(self):
		self.inotify_fd = FFI.inotify_init()
		self.inotify_wd_parent = FFI.inotify_add_watch(self.inotify_fd, '/var/run', FFI.IN_CREATE | FFI.IN_DELETE)
		self.inotify_wd_control = FFI.inotify_add_watch(self.inotify_fd, '/var/run/hostapd', FFI.IN_CREATE | FFI.IN_DELETE)

		self.fds[self.inotify_fd] = ('inotify', None, None)
		self.poll.register(self.inotify_fd, select.POLLIN)

		for interface in listdir('/var/run/hostapd'):
			if interface != 'global':  # ignore the global control interface
				self.add_interface(interface)

	def run(self):
		print("Monitoring for events...")
		try:
			while True:
				for fd, event in self.poll.poll():
					if fd not in self.fds:
						continue

					fd_type, fd_name, fd_obj = self.fds[fd]
					if event & (select.POLLHUP | select.POLLERR):
						print("Poll returned error for file descriptor %d (%s/%s), removing" % (fd, fd_type, fd_name))
						self.remove_interface_fd(fd)

					if fd_type == 'hostapd':
						self.handler(fd_name, fd_obj.recv(1024).decode('utf-8'))
					elif fd_type == 'inotify':
						self.handle_inotify(os.read(fd, 256))
		except Exception as e:
			print("Event loop encountered following exception, quitting")
			sys.print_exception(e)

	def teardown(self):
		for fd, desc in self.fds.items():
			if desc[0] == 'hostapd':
				self.remove_interface_fd(fd)


### High-level device tracker
class WirelessDevicesTracker:
	def __init__(self):
		self.config = get_config('hapt', 'hapt')
		self.clients = {}

		dnsmasq_config = get_config('dhcp', 'dnsmasq')
		self.dnsmasq_leasefile = dnsmasq_config['leasefile'] if 'leasefile' in dnsmasq_config else '/tmp/dhcp.leases'
		self.dnsmasq_domain = dnsmasq_config['domain'] if 'domain' in dnsmasq_config else None

	def call_home_assistant(self, mac, consider_home):
		ip, name = get_lease_details(self.dnsmasq_leasefile, mac)
		hostname = '%s.%s' % (name, self.dnsmasq_domain) if self.dnsmasq_domain and name else name
		dev_id = name if name else mac.replace(':', '_')
		if 'device_id_prefix' in self.config:
			dev_id = '%s_%s' % (self.config['device_id_prefix'], dev_id)

		url = '%s/api/services/device_tracker/see' % self.config['host']
		headers = {'Authorization': 'Bearer %s' % self.config['token'], 'Content-Type': 'application/json'}
		message = {'mac': mac, 'dev_id': dev_id, 'source_type': 'router', 'consider_home': consider_home}
		if hostname:
			message['host_name'] = hostname
		print("Calling Home Assistant for device %s (id %s, hostname %s) with home time of %d" % (mac, dev_id, hostname, consider_home))

		r = requests.post(url, headers=headers, data=json.dumps(message))
		if r.status_code != 200:
			print("Calling Home Assistant failed with status code %d and response '%s'" % (r.status_code, r.text))

	def handle_message(self, interface, message):
		components = message[message.find('>')+1:].split(' ')
		if components[0] == 'AP-STA-CONNECTED':
			self.on_connect(interface, components[1])
		elif components[0] == 'AP-STA-DISCONNECTED':
			self.on_disconnect(interface, components[1])

	def on_connect(self, interface, mac):
		if 'track_mac_address' in self.config and mac not in self.config['track_mac_address']:
			return
		if mac not in self.clients:
			self.clients[mac] = set()
		self.clients[mac].add(interface)
		print("Connect of %s on %s, now connected to: %s" % (mac, interface, ", ".join(self.clients[mac])))
		self.call_home_assistant(mac, int(self.config['consider_home_connect']))

	def on_disconnect(self, interface, mac):
		if 'track_mac_address' in self.config and mac not in self.config['track_mac_address']:
			return
		self.clients[mac].remove(interface)
		print("Disconnect of %s on %s, now connected to: %s" % (mac, interface, ", ".join(self.clients[mac])))
		if len(self.clients[mac]) == 0:
			print("Final disconnect, notifying Home Assistant")
			self.call_home_assistant(mac, int(self.config['consider_home_disconnect']))

	def oneshot(self):
		configured_interfaces = self.config.get('wifi_interfaces', None)
		configured_macs = self.config.get('track_mac_address', None)
		for interface in listdir('/var/run/hostapd'):
			if interface == 'global':  # ignore the global control interface
				continue
			if configured_interfaces and interface not in configured_interfaces:
				continue

			for mac in get_connected_clients(interface):
				if configured_macs and mac not in configured_macs:
					continue
				self.on_connect(interface, mac)

	def monitor(self):
		self.watcher = InterfaceWatcher(self.handle_message, self.config.get('wifi_interfaces', None))
		self.watcher.setup()
		self.watcher.run()
		self.watcher.teardown()

	def exit(self, signum):
		self.watcher.teardown()
		sys.exit()

if __name__ == '__main__':
	tracker = WirelessDevicesTracker()

	tracker.oneshot()
	if len(sys.argv) > 1 and sys.argv[1] == '--monitor':
		signal.signal(signal.SIGINT, tracker.exit)
		tracker.monitor()
