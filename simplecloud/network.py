#!/usr/bin/env python3.7
# -*- coding: utf-8 -*-


from abc import ABC, abstractmethod
import docker
import ipaddress
from simplecloud import docker_client, docker_api_client, logger
from sortedcontainers import SortedSet


class BaseNetwork(ABC):
    @abstractmethod
    def add_container(self, container):
        ...

    @abstractmethod
    def remove_container(self, container):
        ...

    @abstractmethod
    def listen(self):
        ...

    @abstractmethod
    def reload(self):
        ...

    @abstractmethod
    def remove(self):
        ...


class BridgeNetwork(BaseNetwork):
    def __init__(self, network_name, subnet, reserved_ips=None):
        self.network = None
        self.subnet = ipaddress.ip_network(subnet)
        self.address_pool = SortedSet(self.subnet.hosts())
        self.containers = dict()

        self.reservations = reserved_ips

        if not self.attach_to_existing_network(network_name):
            self.create_network(network_name)
        self.name = self.network.name

        self._handlers = {
            ('network', 'connect'): self.handler_network_connect,
            ('network', 'disconnect'): self.handler_network_disconnect
        }

    def attach_to_existing_network(self, name):
        network_list = docker_client.networks.list(names=[name])

        if len(network_list) > 0:
            logger.info('Existing Docker network found, attaching...')

            self.network = network_list[0]

            network_gateway = self.network.attrs['IPAM']['Config'][0].get('Gateway')

            if network_gateway:
                self._remove_ip(network_gateway)
            else:
                self.reservations['_'] = self._get_next_address()

            for cid, specs in self.network.attrs['Containers'].items():
                addr = specs['IPv4Address'][:-3]
                self.add_container(cid, addr)

            return True
        else:
            return False

    def create_network(self, name):
        ipam_pool = docker.types.IPAMPool(
            subnet=self.subnet.with_prefixlen,
        )

        ipam_config = docker.types.IPAMConfig(
            driver='default',
            pool_configs=[ipam_pool]
        )

        self.network = docker_client.networks.create(
            name=name,
            driver='bridge',
            ipam=ipam_config,
            attachable=True
        )

        # reserve one IP for the default gateway
        for _, ip in self.reservations.items():
            self._remove_ip(ip)
        self.reservations['_'] = self._get_next_address()

    def _get_next_address(self):
        if self.address_pool:
            next_addr = self.address_pool[0]
            self.address_pool.pop(0)

            logger.debug(f'Lending IP address {str(next_addr)}')
            return str(next_addr)
        else:
            logger.debug('No available IP address in the address pool')
            return None

    def get_available_ip(self):
        return self._get_next_address()

    def _remove_ip(self, ip):
        addr = ipaddress.ip_address(ip)
        try:
            self.address_pool.remove(addr)
        except KeyError:
            pass
        finally:
            logger.info(f'Address {ip} is now in use')
            return addr

    def _add_ip(self, ip):
        addr = ipaddress.ip_address(ip)
        self.address_pool.add(addr)
        logger.info(f'Address {ip} is now available in the address pool')
        return addr

    def add_container(self, container, addr=None, reservation=None):
        reserved_ip = self.reservations.get(reservation)

        if reserved_ip:
            ipaddr = ipaddress.ip_address(reserved_ip)
        elif addr:
            ipaddr = self._remove_ip(addr)
        else:
            ipaddr = self._get_next_address()

        logger.info(f'Connect container {container.id[:12]} to network')

        # docker network connect
        docker_api_client.connect_container_to_network(
            container.id, self.network.id, ipv4_address=str(ipaddr)
        )

        self.containers[container.id] = ipaddr

    def remove_container(self, cid):
        logger.info(f'Disconnect container {cid[:12]} from network')

        try:
            docker_api_client.disconnect_container_from_network(
                cid, self.network.id, force=True
            )
        except docker.errors.APIError:
            pass

        ipaddr = self.containers.pop(cid, None)
        if ipaddr:
            self._add_ip(ipaddr)

    def handler_network_connect(self, event):
        if event['Actor']['ID'] == self.network.id:
            cid = event['Actor']['Attributes']['container']

            if cid in self.containers:
                return

            container = docker_client.containers.get(cid)
            networks = container.attrs['NetworkSettings']['Networks']
            ipaddr = networks[self.network.name]['IPAddress']

            logger.info(f'Network connect event with container {cid[:12]}')
            self.add_container(container, ipaddr)

    def handler_network_disconnect(self, event):
        if event['Actor']['ID'] == self.network.id:
            cid = event['Actor']['Attributes']['container']

            if cid not in self.containers:
                return

            logger.info(f'Network disconnect event with container {cid[:12]}')
            self.remove_container(cid)

    def listen(self):
        logger.info('Listening to Docker events...')

        self.network.reload()

        for event in docker_client.events(decode=True):
            func = self._handlers.get((event['Type'], event['Action']))
            if func:
                func(event)

    def reload(self):
        self.network.reload()

    def remove(self):
        self.network.remove()


class OpenVSwitchNetwork(BaseNetwork):
    def __init__(self, network_name, subnet, reserved_ips=None):
        self.bridge_name = network_name
        self.subnet = subnet

    def add_container(self, container, ipaddr=None):
        pass

    def remove_container(self, container):
        pass

    def listen(self):
        pass
