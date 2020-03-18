FROM openwrtorg/sdk

COPY hapt /home/build/openwrt/package/hapt
RUN make defconfig
CMD make package/hapt/compile V=sc
