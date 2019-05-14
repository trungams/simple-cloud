#!/usr/bin/env python

"""This is a simple cloud program running docker containers. The program provides
an interactive shell to view and monitor services running in the cloud.

Core components:
- User-defined Docker bridge network
- Service registry (consul) container
- Service discovery container (registrator)
- TCP proxy (HAProxy) container, configured dynamically using consul-template
- Gateway: optional, used for communication between docker networks
- Additional web servers: can be created or scaled manually
"""

import docker
import pprint


docker_client = docker.from_env()


def _check_alive_container(container):
    container.reload()
    return container.status == 'running'


def _stop_container(container):
    try:
        container.kill()
    except docker.errors.APIError:
        pass


class MyCloudService:
    def __init__(self, image, name, network, port, init_scale=1):
        self.image = image
        self.name = name
        self.port = port
        self.network = network
        self.containers = []
        self.idx = 1
        # start the current services with a number of running containers
        self.start(init_scale)

    @property
    def size(self):
        self.reload()
        return len(self.containers)

    def _run_container(self):
        container = docker_client.containers.run(
            image=self.image,
            network=self.network,
            remove=True,
            detach=True,
            environment={
                "SERVICE_NAME": self.name,
                "SERVICE_ID": "%s-%02d" % (self.name, self.idx)
            },
            ports={self.port: None}
        )
        self.idx += 1
        return container

    def info(self):
        """TODO: display information about the current service"""
        pass

    def start(self, scale):
        for _ in range(scale):
            try:
                container = self._run_container()
                self.containers.append(container)
            except Exception as e:
                print e

    def reload(self):
        """Refresh the docker client for up-to-date containers status"""
        self.containers = filter(_check_alive_container, self.containers)

    def scale(self, new_size):
        if new_size < 1:
            raise ValueError("Size has to be a positive integer")
        cur_size = self.size
        if new_size == cur_size:
            return
        elif new_size < cur_size:
            # stop some running containers
            map(_stop_container, self.containers[new_size:])
            self.reload()
        else:
            # start new containers
            for _ in range(new_size - cur_size):
                try:
                    container = self._run_container()
                    self.containers.append(container)
                except Exception as e:
                    print e

    def stop(self):
        map(_stop_container, self.containers)
        self.reload()

    def __str__(self):
        return "Service: %s" % self.name


class MyCloud:
    def __init__(self, subnet, network_name, proxy_ip=None, gateway_ip=None,
                 initial_services=None, *args, **kwargs):
        # reserve ip addresses and create a network
        self.proxy_ip = proxy_ip
        self.gateway_ip = gateway_ip
        self.reserved_addresses = {}
        self.network = None

        # create variables for important containers
        self.registry = None
        self.registrator = None
        self.proxy = None
        self.services = []

        self.reserve_ips()
        start_network(subnet, network_name, self.reserved_addresses)

        self.start_registry()
        self.start_registrator()
        self.start_proxy()
        self.initialize_services(initial_services)

    def reserve_ips(self):
        if self.proxy_ip:
            self.reserved_addresses["proxy"] = self.proxy_ip
        if self.gateway_ip:
            self.reserved_addresses["gateway"] = self.gateway_ip

    def start_network(self, subnet, network_name, reserved_ips):
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

    def start_registry(self):
        self.registry = docker_client.containers.run(
            image="gliderlabs/consul-server:latest",
            command=["-bootstrap"],
            name="service-registry",
            restart_policy={
                "Name": "on-failure",
                "MaximumRetryCount": 3
            },
            network=self.network.name,
            remove=True,
            detach=True
        )

    def start_registrator(self):
        # block this action until consul registry is up
        while not _check_alive_container(self.registry):
            pass
        self.registrator = docker_client.containers.run(
            image="gliderlabs/registrator:latest",
            command=["-internal", "consul://consul:8500"],
            name="service-registrator",
            restart_policy={
                "Name": "on-failure",
                "MaximumRetryCount": 3
            },
            volumes=[
                "/var/run/docker.sock:/tmp/docker.sock"
            ],
            network=self.network.name,
            remove=True,
            detach=True
        )

    def start_proxy(self):
        # block this action until registrator is up
        while not _check_alive_container(self.registrator):
            pass
        self.proxy = docker_client.containers.run(
            image="turtle144/cloud-consul-template-haproxy",
            command=[
                "consul-template",
                "-config=/tmp/haproxy.conf",
                "-consul=consul:8500",
                "-log-level=debug"
            ],
            name="proxy",
            privileged=True,
            remove=True,
            detach=True,
            restart_policy={
                "Name": "on-failure",
                "MaximumRetryCount": 3
            }
        )

        self.network.connect(
            self.proxy,
            ipv4_address=self.proxy_ip
        )

    def start_service(self, service_name):
        pass

    def initialize_services(self, services_list):
        pass

    def stop_service(self, service_name):
        pass

    def list_services(self):
        pass

    def show_service(self, service_name):
        pass

    def scale_service(self, service_name, size):
        pass

    def _update(self):
        pass

    def cleanup(self):
        pass

