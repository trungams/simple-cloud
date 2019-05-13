#!/bin/bash

# create a user-defined bridge network
# docker network create --subnet=192.168.10.0/24 newnet

# run consul server (publish/expose port if necessary?)
docker run -d --rm --name=consul --network=newnet gliderlabs/consul-server -bootstrap

# run registrator and connect it to the running consul server (docker auto dns)
docker run -d --rm \
    --name=registrator \
    --network=newnet \
    --volume=/var/run/docker.sock:/tmp/docker.sock \
    gliderlabs/registrator \
        -internal \
        consul://consul:8500

# run consul-template that manages haproxy
docker run -d --rm --privileged -it \
    --network=newnet \
    -e "SERVICE_NAME=consul-template" \
    -P --name=consul-template-haproxy \
    turtle144/cloud-haproxy-consul-template \
        consul-template -config=/tmp/haproxy.conf -consul=consul:8500 -log-level=debug

# run simple flask app to see registrator in action
docker run -d --rm --network=newnet \
    -e "SERVICE_NAME=flask" -e "SERVICE_ID=flask01" \
    -P --name=flask01 \
    turtle144/cloud-webserver

docker run -d --rm --network=newnet \
    -e "SERVICE_NAME=flask" -e "SERVICE_ID=flask02" \
    -P --name=flask02 \
    turtle144/cloud-webserver

# docker network rm newnet
