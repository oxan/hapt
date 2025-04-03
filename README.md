# HAPT

Home Assistant Presence Tracker (HAPT) is an event-driven device presence tracker for [Home Assistant][homeassistant] on
an OpenWRT router or access point.

## Description

HAPT listens on association and disassociation events to wireless networks, using the hostapd control interface. It
keeps track of which device is connected to which networks. When a device connects to its first network, or disconnects
from its last network, a service call to Home Assistant is performed to mark the device as home or away. By tracking
active device connections, HAPT ensures that a device switching between different networks (e.g. the 2.4 GHz and 5 GHz
bands) is not marked as away.

## Usage

### Installation
Download a package from the [releases][releases] page, and install it either by uploading it in LuCi (System > Software)
or running `opkg install <file>` from a shell.

### Configuration
Once the package is installed, you must update the configuration in `/etc/config/hapt`. At minimum the `host` option
should be set to the URL of your Home Assistant installation (including the scheme, e.g. `http://homeassistant:8123`),
and the `token` option to a Home Assistant [long-lived access token][token] (these can be generated in the Home
Assistant web interface, on the Security page of your user profile).

The `consider_home_connect` and `consider_home_disconnect` settings can be used to configure for how long (in seconds)
after the first association and last disassociation event the device should be considered home. Since Home Assistant
does not support marking a device as away (on disconnects), this is implemented by marking the device as home for a
negligible amount of time, after which Home Assistant will mark the device as away. The default values should be fine
here.

With the `wifi_interfaces` option, it is possible to specify the wireless interfaces that must be monitored. This can be
used (for example) to ignore devices on a guest network.

By listing MAC addresses in the `track_mac_address` option, it is possible to whitelist MAC addresses which are tracked.
This prevents uninteresting devices from being synchronized with Home Assistant and cluttering the entity registry.

### Running
After modifying the configuration, you must restart the service by `service hapt restart`. This can also be done from
the LuCi interface (System > Startup). HAPT prints log messages to the system log, so that you can verify it is working
as expected.

It is possible to synchronize just the currently connected devices with Home Assistant by running `hapt` from the
command line. This can be especially useful to debug the connection with Home Assistant, as this will also print any
errors that occur.

## Development
You can build a custom package by running the `makepkg.sh` script, which will run the package build in a Docker
container and place the compiled package in the `build/bin` directory.

## Acknowledgments

This project has been inspired by the [openwrt_hass_devicetracker][hasstracker] package.

[homeassistant]: https://www.home-assistant.io/
[hasstracker]: https://github.com/mueslo/openwrt_hass_devicetracker
[releases]: https://github.com/oxan/hapt/releases
[token]: https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token
