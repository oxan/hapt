include $(TOPDIR)/rules.mk

PKG_NAME       := hapt
PKG_VERSION    := 0.2
PKG_RELEASE    := 1
PKG_LICENSE    := GPL-3.0+
PKG_MAINTAINER := Oxan van Leeuwen <oxan@oxanvanleeuwen.nl>

include $(INCLUDE_DIR)/package.mk

define Package/hapt
	SECTION  := net
	CATEGORY := Network
	TITLE    := Wireless device presence tracker integrated with Home Assistant
	DEPENDS  := +micropython +micropython-lib +micropython-lib-unix-src
	PKGARCH  := all
endef

define Package/hapt/description
	Home Assistant Presence Tracker (hapt) tracks the presence of wireless devices on your network and synchronizes with Home Assistant.
endef

define Package/hapt/conffiles
/etc/config/hapt
endef

define Build/Prepare
endef

define Build/Configure
endef

define Build/Compile
endef

define Package/hapt/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) ./files/hapt.py $(1)/usr/bin/hapt

	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) ./files/hapt.init $(1)/etc/init.d/hapt

	$(INSTALL_DIR) $(1)/etc/config
	$(INSTALL_CONF) ./files/hapt.config $(1)/etc/config/hapt
endef

$(eval $(call BuildPackage,hapt))
