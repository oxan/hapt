#!/usr/bin/micropython

# Dependencies: micropython, micropython-lib (for os module), curl (to make API call)

import os
import sys
import ujson
import uos
import urequests
import uselect
import usocket
import ustruct
import utime

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

def get_wireless_interfaces():
	network_status = ubus_call('network.wireless', 'status')
	interfaces = []
	for _, radio in network_status.items():
		for interface in radio['interfaces']:
			interfaces.append(interface['ifname'])
	return interfaces

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

class WirelessDevicesTracker:
	def __init__(self):
		self.config = get_config('hapt', 'hapt')
		self.interfaces = get_wireless_interfaces()
		self.clients = {}

		dnsmasq_config = get_config('dhcp', 'dnsmasq')
		self.dnsmasq_leasefile = dnsmasq_config['leasefile'] if 'leasefile' in dnsmasq_config else '/tmp/dhcp.leases'
		self.dnsmasq_domain = dnsmasq_config['domain'] if 'domain' in dnsmasq_config else None

	def handle_message(self, interface, message):
		components = message[message.find('>')+1:].split(' ')
		if components[0] == 'AP-STA-CONNECTED':
			self.on_connect(interface, components[1])
		elif components[0] == 'AP-STA-DISCONNECTED':
			self.on_disconnect(interface, components[1])

	def call_home_assistant(self, mac, consider_home):
		ip, name = get_lease_details(self.dnsmasq_leasefile, mac)
		hostname = '%s.%s' % (name, self.dnsmasq_domain) if self.dnsmasq_domain else name
		dev_id = name if name else mac.replace(':', '_')
		if 'device_id_prefix' in self.config:
			dev_id = '%s_%s' % (self.config['device_id_prefix'], dev_id)

		url = '%s/api/services/device_tracker/see' % self.config['host']
		headers = {'Authorization': 'Bearer %s' % self.config['token'], 'Content-Type': 'application/json'}
		message = {'mac': mac, 'dev_id': dev_id, 'host_name': hostname, 'source_type': 'router', 'consider_home': consider_home}
		print("Calling Home Assistant for device %s (id %s, hostname %s) with home time of %d" % (mac, dev_id, hostname, consider_home))

		curl_call(url, headers, ujson.dumps(message))

	def on_connect(self, interface, mac):
		if mac not in self.clients:
			self.clients[mac] = []
		self.clients[mac].append(interface)
		print("Connect of %s on %s, now connected to: %s" % (mac, interface, self.clients[mac]))
		self.call_home_assistant(mac, int(self.config['consider_home_connect']))

	def on_disconnect(self, interface, mac):
		self.clients[mac].remove(interface)
		print("Disconnect of %s on %s, now connected to: %s" % (mac, interface, self.clients[mac]))
		if len(self.clients[mac]) == 0:
			print("Final disconnect, notifying Home Assistant")
			self.call_home_assistant(mac, int(self.config['consider_home_disconnect']))

	def oneshot(self):
		for interface in self.interfaces:
			for mac in get_connected_clients(interface):
				self.on_connect(interface, mac)

	def monitor(self):
		# connect to hostapd
		poll = uselect.poll()
		socket_map = {}
		for interface in self.interfaces:
			socket = connect_hostapd_socket(interface)
			socket_map[str(socket)] = interface
			poll.register(socket, uselect.POLLIN)
		print("Connected to hostapd on interfaces %s" % self.interfaces)

		# monitor events
		while True:
			available = poll.poll()
			for socket, event in available:
				# TODO handle errors
				message = socket.recv(1024)
				interface = socket_map[str(socket)]
				self.handle_message(interface, message.decode('ascii'))

tracker = WirelessDevicesTracker()
tracker.oneshot()
if len(sys.argv) > 1 and sys.argv[1] == "--monitor":
	tracker.monitor()
