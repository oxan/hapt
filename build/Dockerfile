FROM ghcr.io/openwrt/sdk:x86_64-v24.10.0

COPY hapt ./package/hapt
CMD make defconfig && make package/hapt/compile V=sc
