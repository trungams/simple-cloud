#!/usr/bin/env python

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
import os


docker_client = docker.from_env()
docker_api_client = docker.APIClient(base_url="unix://var/run/docker.sock")


def _check_alive_container(container):
    try:
        container.reload()
        return container.status == 'running'
    except:
        return False


def _stop_container(container):
    try:
        container.remove(force=True)
    except:
        pass
    finally:
        return True


class MyCloudService:
    def __init__(self, image, name, network, port, init_scale=1, command=None):
        self.image = image
        self.name = name
        self.port = port
        self.network = network
        self.command = command
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
            name="%s_%02d" % (self.name, self.idx),
            command=self.command,
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
                {c.id: c.name} for c in self.containers
            ]
        }

        return _info

    def start(self, scale):
        """Start the service with an initial number of containers"""
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
        """Scale up or down the current service"""
        if new_size < 1:
            return False
        cur_size = self.size
        if new_size == cur_size:
            return True
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
        return True

    def stop(self):
        """Stop all containers"""
        map(_stop_container, self.containers)
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

        # create variables for important containers
        self.registry = None
        self.registrator = None
        self.proxy = None
        self.services = {}
        self.used_ports = set()

        try:
            # start the network
            self.reserve_ips()
            self.start_network(subnet, network_name, self.reserved_addresses)

            # start service registry, discovery, proxy and services
            # start proxy container first to reserve ip
            self.create_proxy()
            self.create_registry()
            self.create_registrator()

            print "Starting proxy, ",
            self.proxy.start()
            print "registry, ",
            self.registry.start()
            print "registrator...",
            self.registrator.start()
            print "OK"

            # run entrypoint script if there is one, after starting 3 core containers
            if entrypoint:
                os.system(entrypoint)

            self.initialize_services(initial_services)

        except Exception as e:
            print e
            self.cleanup()

    def reserve_ips(self):
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
            image="gliderlabs/consul-server:latest",
            command=["-bootstrap"],
            name="service-registry",
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
            image="gliderlabs/registrator:v7",
            command=["-internal",
                     "-retry-attempts=10",
                     "-retry-interval=1000",
                     "consul://service-registry:8500"],
            name="service-registrator",
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

        host_config = docker_api_client.create_host_config(
            restart_policy={
                "Name": "on-failure",
                "MaximumRetryCount": 10
            },
            privileged=True
        )

        container = docker_api_client.create_container(
            image="turtle144/cloud-consul-template-haproxy",
            command=[
                "consul-template",
                "-config=/tmp/haproxy.conf",
                "-consul=service-registry:8500",
                "-log-level=debug"
            ],
            name="proxy",
            host_config=host_config,
            networking_config=networking_config,
            detach=True
        )

        self.proxy = docker_client.containers.get(container)

    def start_service(self, image, name, port, scale=1, command=None):
        if name in self.services:
            print "Service %s already exists!" % name
            return
        if port in self.used_ports:
            print "Port %d has already been used!" % port
            return
        new_service = MyCloudService(image, name, self.network.name, port, scale, command)
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
            print "Removed service: %s" % old_service.name
            return True
        print "Service does not exist"
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
        print "Cleaning up..."
        for container in (self.registry, self.registrator, self.proxy):
            _stop_container(container)
        for service in self.services.values():
            service.stop()
        try:
            self.network.remove()
        except:
            pass
        print "Removed running services and docker network"
