#!/usr/bin/micropython

# Dependencies: micropython, micropython-lib (for os, signal module), curl (to make API call)

import ffi
import os
import signal
import sys
import ujson
import uos
import urequests
import uselect
import usocket
import ustruct
import utime

class FFI:
	libc = ffi.open("libc.so.6")

	IN_CREATE = 0x00000100
	IN_DELETE = 0x00000200

	inotify_init = libc.func("i", "inotify_init", "")
	inotify_add_watch = libc.func("i", "inotify_add_watch", "isI")
	inotify_rm_watch = libc.func("i", "inotify_rm_watch", "ii")

def subprocess(command):
	with os.popen(command, 'r') as stream:
		result = stream.read()
	os.waitpid(-1, None)
	return result

def ubus_call(path, method, message=None):
	# it might be cleaner to use libubus and FFI
	if message is not None:
		command = "ubus call %s %s '%s'" % (path, method, ujson.dumps(message))
	else:
		command = "ubus call %s %s" % (path, method)
	return ujson.loads(subprocess(command))

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
	response = ubus_call('hostapd.%s' % interface, 'get_clients')
	return response['clients'].keys()

def encode_socket_address(path):
	# The socket.bind() functions takes struct sockaddr_un as parameter, which is defined by (see unix(7)):
	# struct sockaddr_un {
	#    sa_family_t sun_family;    // sa_family_t is an unsigned short
	#    char        sun_path[108];
	# };
	return ustruct.pack('H108s', usocket.AF_UNIX, path)

def connect_hostapd_socket(interface):
	remote_address = '/var/run/hostapd/%s' % interface
	local_address  = '/var/run/hapt-%s-%d' % (interface, utime.time() % 86400)
	socket = usocket.socket(usocket.AF_UNIX, usocket.SOCK_DGRAM)
	socket.bind(encode_socket_address(local_address))
	socket.connect(encode_socket_address(remote_address))
	socket.send('ATTACH')
	response = socket.recv(1024)
	if response != b'OK\n':
		raise ValueError('Received invalid response on ATTACH from hostapd: %s' % response)

	return socket

def disconnect_hostapd_socket(socket):
	socket.send('DETACH')
	response = socket.recv(1024)
	if response != b'OK\n':
		raise ValueError('Received invalid response on DETACH from hostapd: %s' % response)
	socket.close()

def decode_inotify_event(event):
	wd, mask, cookie, length = ustruct.unpack("iIII", event)
	name = event[ustruct.calcsize("iIII"):].split(b'\0', 1)[0].decode('utf-8')
	return wd, mask, name

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

def listdir(path):
	return [item[0] for item in uos.ilistdir(path) if item[0] not in (".", "..")]


class InterfaceWatcher:
	def __init__(self, handler, include_interfaces=None):
		self.handler = handler
		self.include_interfaces = include_interfaces

		self.fds  = {}
		self.poll = uselect.poll()

	def add_interface(self, interface):
		if self.include_interfaces and interface not in self.include_interfaces:
			return

		socket = connect_hostapd_socket(interface)
		print("Connected to hostapd on interface %s" % interface)
		self.fds[socket.fileno()] = ('hostapd', interface, socket)
		self.poll.register(socket.fileno(), uselect.POLLIN)

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
		self.poll.register(self.inotify_fd, uselect.POLLIN)

		for interface in listdir('/var/run/hostapd'):
			self.add_interface(interface)

	def run(self):
		print("Monitoring for events...")
		try:
			while True:
				for fd, event in self.poll.poll():
					if fd not in self.fds:
						continue

					fd_type, fd_name, fd_obj = self.fds[fd]
					if event & (uselect.POLLHUP | uselect.POLLERR):
						print("Poll returned error for file descriptor %d (%s/%s), removing" % (fd, fd_type, fd_name))
						self.remove_interface_fd(fd)

					if fd_type == 'hostapd':
						self.handler(fd_name, fd_obj.recv(1024).decode('utf-8'))
					elif fd_type == 'inotify':
						self.handle_inotify(os.read(fd, 256))
		except Exception as e:
			print("Poll event loop encountered following exception, quitting")
			sys.print_exception(e)

	def teardown(self):
		for fd, desc in self.fds.items():
			if desc[0] == 'hostapd':
				self.remove_interface_fd(fd)


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

		curl_call(url, headers, ujson.dumps(message))

	def handle_message(self, interface, message):
		components = message[message.find('>')+1:].split(' ')
		if components[0] == 'AP-STA-CONNECTED':
			self.on_connect(interface, components[1])
		elif components[0] == 'AP-STA-DISCONNECTED':
			self.on_disconnect(interface, components[1])

	def on_connect(self, interface, mac):
		if mac not in self.clients:
			self.clients[mac] = []
		self.clients[mac].append(interface)
		print("Connect of %s on %s, now connected to: %s" % (mac, interface, ", ".join(self.clients[mac])))
		self.call_home_assistant(mac, int(self.config['consider_home_connect']))

	def on_disconnect(self, interface, mac):
		self.clients[mac].remove(interface)
		print("Disconnect of %s on %s, now connected to: %s" % (mac, interface, self.clients[mac]))
		if len(self.clients[mac]) == 0:
			print("Final disconnect, notifying Home Assistant")
			self.call_home_assistant(mac, int(self.config['consider_home_disconnect']))

	def oneshot(self):
		configured_interfaces = self.config.get('wifi_interfaces', None)
		for interface in listdir('/var/run/hostapd'):
			if not configured_interfaces or interface in configured_interfaces:
				for mac in get_connected_clients(interface):
					self.on_connect(interface, mac)

	def monitor(self):
		watcher = InterfaceWatcher(self.handle_message, self.config.get('wifi_interfaces', None))
		watcher.setup()
		watcher.run()
		watcher.teardown()

	def exit(self, signum):
		# the signal will cause poll() to raise OSError(EINTR), which in turn returns from watcher.run()
		pass

if __name__ == "__main__":
	tracker = WirelessDevicesTracker()
	signal.signal(signal.SIGINT, tracker.exit)

	tracker.oneshot()
	if len(sys.argv) > 1 and sys.argv[1] == "--monitor":
		tracker.monitor()
