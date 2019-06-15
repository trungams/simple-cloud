#!/usr/bin/env python3.7
# -*- coding: utf-8 -*-

from sortedcontainers import SortedSet
import ipaddress
from simplecloud import docker_client, logger

"""TODO
- start cloud
- pass network id to netmanager
- get all used IP addresses
- start listening to docker events, focus on containers in the same network only
to register/deregister ip addresses
- provides an api to get next ip address based on the data structure we're using
- use SortedSet, so can do things like get store[0], then remove store[0] (O(logn)).
Whenever an address becomes available again, we can re-add it to the IP pool
"""


class NetworkManager:
    def __init__(self, network_id):
        self.network = docker_client.networks.get(network_id)
        network_address = self.network.attrs['IPAM']['Config'][0]['Subnet']

        self.subnet = ipaddress.ip_network(network_address)
        self.address_pool = SortedSet(self.subnet.hosts())
        self.containers = dict()

        self._handlers = {
            ('network', 'connect'): self.network_connect,
            ('network', 'disconnect'): self.network_disconnect
        }

        # remove addresses in use from the pool
        network_gateway = self.network.attrs['IPAM']['Config'][0].get('Gateway')

        if network_gateway:
            self.remove(network_gateway)
        else:
            self.remove(self.get_next_address())

        for cid, specs in self.network.attrs['Containers'].items():
            addr = specs['IPv4Address'][:-3]
            self.container_start(cid, addr)

    def get_next_address(self):
        if self.address_pool:
            next_addr = self.address_pool[0]
            self.address_pool.pop(0)
            return str(next_addr)
        else:
            return None

    def remove(self, ip):
        addr = ipaddress.ip_address(ip)
        try:
            self.address_pool.remove(addr)
        except KeyError:
            pass
        finally:
            logger.info(f'Address {ip} is now in use')
            return addr

    def add(self, ip):
        addr = ipaddress.ip_address(ip)
        self.address_pool.add(addr)
        logger.info(f'Address {ip} is now available in the address pool')
        return addr

    def container_start(self, cid, addr):
        ipaddr = self.remove(addr)
        if ipaddr:
            self.containers[cid] = ipaddr

    def container_stop(self, cid):
        ipaddr = self.containers.get(cid)
        if ipaddr:
            self.add(ipaddr)
            del self.containers[cid]

    def network_connect(self, event):
        if event['Actor']['ID'] == self.network.id:
            cid = event['Actor']['Attributes']['container']
            container = docker_client.containers.get(cid)
            networks = container.attrs['NetworkSettings']['Networks']
            ipaddr = networks[self.network.name]['IPAddress']

            logger.info(f'Container {cid[:12]} is connected to the network.')

            self.container_start(cid, ipaddr)

    def network_disconnect(self, event):
        if event['Actor']['ID'] == self.network.id:
            cid = event['Actor']['Attributes']['container']

            logger.info(f'Container {cid[:12]} is disconnected from the network.')

            self.container_stop(cid)

    def listen(self):
        logger.debug('Listening to Docker events...')

        self.network.reload()

        for event in docker_client.events(decode=True):
            func = self._handlers.get((event['Type'], event['Action']))
            if func:
                func(event)
