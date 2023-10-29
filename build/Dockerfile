FROM ghcr.io/openwrt/sdk

COPY hapt ./package/hapt
CMD make defconfig && make package/hapt/compile V=sc
