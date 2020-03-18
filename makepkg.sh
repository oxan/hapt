#!/bin/sh

docker build -f build/Dockerfile -t hapt .
docker run --rm -v $(pwd)/build/bin:/home/build/openwrt/bin hapt
