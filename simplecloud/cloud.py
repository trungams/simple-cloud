#!/usr/bin/env python3.7
# -*- coding: utf-8 -*-

"""This is a simple cloud program running docker containers. The program provides
an API to view and monitor services running in the cloud.

Core components:
- User-defined Docker bridge network
- Service registry (consul) container
- Service discovery container (registrator)
- TCP proxy (HAProxy) container, configured dynamically using consul-template
- Gateway: optional, used for communication between docker networks
- Additional web servers: can be created or scaled manually
"""

import docker
from simplecloud import docker_client, docker_api_client, logger
import requests
import base64
import traceback


proxy_configs = {
    'mode': ('tcp', 'http'),
    'balance': (
        'roundrobin', 'static-rr', 'leastconn',
        'first', 'source', 'uri', 'url_param', 'rdp-cookie'
    )
}


def _check_alive_container(container):
    try:
        container.reload()
        return container.status == 'running'
    except:
        return False


def _stop_container(container):
    try:
        logger.info(f'Stopping container {container.id}')
        container.remove(force=True)
    except:
        pass
    finally:
        return True


class MyCloudService:
    def __init__(self, image, name, network, port,
                 init_scale=1, command=None, netmanager=None):
        self.image = image
        self.name = name
        self.port = port
        self.network = network
        self.netmanager = netmanager or None
        self.command = command
        self.containers = []
        self.idx = 1
        self.mode = None
        self.balance = None
        # start the current services with a number of running containers
        self.start(init_scale)

    @property
    def size(self):
        self.reload()
        return len(self.containers)

    def _create_container(self):
        pass

    def _run_container(self):
        container = docker_client.containers.run(
            image=self.image,
            name="%s_%02d_%s" % (self.name, self.idx, self.network),
            command=self.command,
            auto_remove=True,
            network=self.network,
            detach=True,
            environment={
                "SERVICE_NAME": self.name,
                "SERVICE_ID": "%s_%02d" % (self.name, self.idx)
            },
            ports={self.port: None}
        )
        self.idx += 1
        return container

    def info(self):
        _info = {
            "Image": self.image,
            "Service name": self.name,
            "Port": self.port,
            "Number of containers": self.size,
            "Containers": [
                {c.id[:12]: c.name} for c in self.containers
            ],
            "Mode": self.mode or 'tcp',
            "LB algorithm": self.balance or 'roundrobin'
        }

        return _info

    def start(self, scale):
        """Start the service with an initial number of containers"""
        for _ in range(scale):
            try:
                container = self._run_container()
                self.containers.append(container)
            except Exception as e:
                logger.error(e)

    def reload(self):
        """Refresh the docker client for up-to-date containers status"""
        self.containers = list(filter(_check_alive_container, self.containers))

    def scale(self, new_size):
        """Scale up or down the current service"""
        if new_size < 1:
            return False
        cur_size = self.size
        if new_size == cur_size:
            return True
        elif new_size < cur_size:
            # stop some running containers
            for container in self.containers[new_size:]:
                _stop_container(container)
            self.reload()
        else:
            # start new containers
            for _ in range(new_size - cur_size):
                try:
                    container = self._run_container()
                    self.containers.append(container)
                except Exception as e:
                    logger.error(e)
        return True

    def stop(self):
        """Stop all containers"""
        for container in self.containers:
            _stop_container(container)
        self.containers = []

    def __str__(self):
        return "Service: %s" % self.name


class MyCloud:
    def __init__(self, subnet=None, network_name=None, proxy_ip=None, gateway_ip=None,
                 initial_services=None, entrypoint=None, *args, **kwargs):
        # declare variables for network stuff
        self.proxy_ip = proxy_ip
        self.gateway_ip = gateway_ip
        self.reserved_addresses = {}
        self.network = None
        self.netmanager = None

        # create variables for important containers
        self.registry_name = "service-registry-%s" % network_name
        self.registrator_name = "service-registrator-%s" % network_name
        self.proxy_name = "proxy-%s" % network_name
        self.proxy_entrypoint = entrypoint
        self.registry = None
        self.registrator = None
        self.proxy = None
        self.services = {}
        self.used_ports = set()

        self.running = True

        try:
            # start the network
            self.reserve_ips()
            self.start_network(subnet, network_name, self.reserved_addresses)

            # start service registry, discovery, proxy and services
            # start proxy container first to reserve ip
            self.create_proxy()
            self.create_registry()
            self.create_registrator()

            self.proxy.start()
            logger.info("Proxy has been started")
            self.registry.start()
            logger.info("Service registry has been started")
            self.registrator.start()
            logger.info("Service registrator has been started")

            if initial_services:
                self.initialize_services(initial_services)

        except Exception as e:
            logger.error(''.join(
                traceback.format_exception(
                    type(e), e, e.__traceback__)))
            self.cleanup()

    def reserve_ips(self):
        if self.gateway_ip:
            self.reserved_addresses["gateway"] = self.gateway_ip

    def start_network(self, subnet, network_name, reserved_ips):
        network_list = docker_client.networks.list(names=[network_name])

        if len(network_list) > 0:
            self.network = network_list[0]
        else:
            ipam_pool = docker.types.IPAMPool(
                subnet=subnet,
                aux_addresses=reserved_ips
            )

            ipam_config = docker.types.IPAMConfig(
                driver="default",
                pool_configs=[ipam_pool]
            )

            self.network = docker_client.networks.create(
                name=network_name,
                driver="bridge",
                ipam=ipam_config,
                attachable=True
            )

    def create_registry(self):
        networking_config = docker_api_client.create_networking_config({
            self.network.name: docker_api_client.create_endpoint_config()
        })

        host_config = docker_api_client.create_host_config(
            restart_policy={
                "Name": "on-failure",
                "MaximumRetryCount": 10
            }
        )

        container = docker_api_client.create_container(
            image="citelab/consul-server:latest",
            command=["-bootstrap"],
            name=self.registry_name,
            host_config=host_config,
            networking_config=networking_config,
            detach=True
        )

        self.registry = docker_client.containers.get(container)

    def create_registrator(self):
        networking_config = docker_api_client.create_networking_config({
            self.network.name: docker_api_client.create_endpoint_config()
        })

        host_config = docker_api_client.create_host_config(
            restart_policy={
                "Name": "on-failure",
                "MaximumRetryCount": 10
            },
            binds=[
                "/var/run/docker.sock:/tmp/docker.sock"
            ]
        )

        container = docker_api_client.create_container(
            image="citelab/registrator:latest",
            command=["-internal",
                     "-network=%s" % self.network.name,
                     "-retry-attempts=10",
                     "-retry-interval=1000",
                     "consul://%s:8500" % self.registry_name],
            name=self.registrator_name,
            volumes=["/tmp/docker.sock"],
            host_config=host_config,
            networking_config=networking_config,
            detach=True
        )

        self.registrator = docker_client.containers.get(container)

    def create_proxy(self):
        networking_config = docker_api_client.create_networking_config({
            self.network.name: docker_api_client.create_endpoint_config(
                ipv4_address=self.proxy_ip
            )
        })

        if self.proxy_entrypoint:
            proxy_binds = ["%s:/root/entry/custom-entrypoint.sh" % self.proxy_entrypoint]
            proxy_volumes = ["/root/entry/custom-entrypoint.sh"]
            proxy_entrypoint = "/root/entry/custom-entrypoint.sh"
        else:
            proxy_binds = []
            proxy_volumes = []
            proxy_entrypoint = None

        host_config = docker_api_client.create_host_config(
            restart_policy={
                "Name": "on-failure",
                "MaximumRetryCount": 10
            },
            binds=proxy_binds,
            privileged=True
        )

        container = docker_api_client.create_container(
            image="citelab/haproxy:latest",
            entrypoint=proxy_entrypoint,
            command=[
                "consul-template",
                "-config=/tmp/haproxy.conf",
                "-consul=%s:8500" % self.registry_name,
                "-log-level=debug"
            ],
            volumes=proxy_volumes,
            name=self.proxy_name,
            host_config=host_config,
            networking_config=networking_config,
            detach=True
        )

        self.proxy = docker_client.containers.get(container)

    @property
    def registry_ip(self):
        info = docker_api_client.inspect_container(self.registry_name)
        registry_ip = info['NetworkSettings']['Networks'][self.network.name]['IPAddress']
        return registry_ip

    def registry_update(self, service, key, value=None, action='put'):
        if service not in self.services:
            return False
        if key not in proxy_configs or value not in proxy_configs[key]:
            return False

        # craft uri from arguments
        uri = 'http://%s:8500/v1/kv/service/%s/%s' % (self.registry_ip, service, key)
        if action == 'put' and value is not None:
            resp = requests.put(uri, data=value)
            if resp.json():    # success
                setattr(self.services[service], key, value)
                return True
            return False
        elif action == 'delete':
            resp = requests.delete(uri)
            if resp.json():
                setattr(self.services[service], key, None)
                return True
            return False
        else:
            return False

    def registry_get(self, service, key):
        if service not in self.services:
            return False
        if key not in proxy_configs:
            return False

        # craft uri from arguments
        uri = 'http://%s:8500/v1/kv/service/%s/%s'
        resp = requests.get(uri)

        # returns default values if key does not exists
        if resp.status_code == 404:
            return 'tcp' if key == 'mode' else 'roundrobin'
        else:
            value = resp.json()[0]['Value']
            return base64.b64decode(value)

    def start_service(self, image, name, port, scale=1, command=None):
        if name in self.services:
            logger.warning(f"Service {name} already exists")
            return
        if port in self.used_ports:
            logger.warning(f"Port {port} has already been used!")
            return
        new_service = MyCloudService(
            image, name, self.network.name,
            port, scale, command, self.netmanager)
        self.services[name] = new_service
        self.used_ports.add(port)

    def initialize_services(self, services_list):
        for service in services_list:
            self.start_service(**service)

    def stop_service(self, name):
        old_service = self.services.pop(name, None)
        if old_service:
            old_service.stop()
            self.used_ports.remove(old_service.port)
            logger.info(f"Removed service: {old_service.name}")
            return True
        logger.warning(f"Service {name} does not exist")
        return False

    def list_services(self):
        return self.services.keys()

    def info_service(self, name):
        if name in self.services:
            return self.services[name].info()
        else:
            return {}

    def scale_service(self, name, size):
        if name in self.services:
            return self.services[name].scale(size)
        else:
            return False

    def _update(self):
        self.network.reload()
        for container in (self.registry, self.registrator, self.proxy):
            container.reload()
        for service in self.services.values():
            service.reload()

    def cleanup(self):
        logger.debug("Cleaning up everything")
        for container in (self.registry, self.registrator, self.proxy):
            _stop_container(container)
        for service in self.services.values():
            service.stop()
        try:
            self.network.remove()
        except:
            pass

        self.running = False
        logger.debug("Removed running services and docker network")

    def register_netmanager(self, manager):
        self.netmanager = manager
        logger.debug('Network manager registered')
