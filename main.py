#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import json
import argparse


class KeyNotFoundError(Exception):
    pass


def parse_file(config_path):
    if not os.path.isfile(config_path):
        raise IOError
    with open(config_path, "r") as f:
        try:
            configs = json.load(f, encoding="utf-8")
        except ValueError:
            raise ValueError("Configuration file is not in JSON format")

    # config file must contain subnet range
    if "Subnet" not in configs:
        raise KeyNotFoundError("You need to specify the subnet range")

    print(json.dumps(configs, indent=2, sort_keys=True))

    return configs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Simple cloud program with a TCP proxy, "
                    "service registry and auto-discovery."
    )

    # accepts config file to specify network address and containers IP

    config_group = parser.add_mutually_exclusive_group(required=True)
    config_group.add_argument("-f", "--config-file", type=str,
                              metavar="PATH", dest="config",
                              help="Specify path to config file")
    config_group.add_argument("-s", "--subnet", type=str, metavar="NETWORK_IP",
                              help="Specify a subnet range for the cloud")

    parser.add_argument("-n", "--net-name", type=str, metavar="NETWORK_NAME",
                        default="my_network", dest="net_name",
                        help="Name the user-defined Docker network")
    parser.add_argument("-p", "--proxy-ip", type=str,
                        metavar="PROXY_IP", dest="proxy_ip",
                        help="Reserve an IPv4 address for proxy container")
    parser.add_argument("-g", "--gateway-ip", type=str,
                        metavar="GATEWAY_IP", dest="gateway_ip",
                        help="Reserve an IPv4 address for the gateway")

    validate_group = parser.add_mutually_exclusive_group()
    validate_group.add_argument("--validate-ip", dest="validate", action="store_true",
                                help="Validate configurations after parsing")
    validate_group.add_argument("--no-validate-ip", dest="validate", action="store_false",
                                help="Skip validation of configurations")

    parsed = parser.parse_args()

    if parsed.config:
        kwargs = parse_file(parsed.config)
    else:
        pass
