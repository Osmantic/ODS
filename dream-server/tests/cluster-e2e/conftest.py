import os
import socket
import time

import pytest


@pytest.fixture(scope="session")
def controller_ip():
    return os.environ["CONTROLLER_IP"]


@pytest.fixture(scope="session")
def own_ip():
    return os.environ["OWN_IP"]


@pytest.fixture(scope="session")
def token():
    return os.environ["TOKEN"]


@pytest.fixture(scope="session")
def broadcast_addr():
    return os.environ["BROADCAST_ADDR"]


@pytest.fixture(scope="session")
def worker1_ip():
    return os.environ.get("WORKER1_IP", "172.30.10.21")


@pytest.fixture(scope="session")
def worker2_ip():
    return os.environ.get("WORKER2_IP", "172.30.10.22")


@pytest.fixture(scope="session")
def worker_rpc_port():
    return int(os.environ.get("WORKER_RPC_PORT", "50052"))


@pytest.fixture(scope="session")
def worker_status_port():
    return int(os.environ.get("WORKER_STATUS_PORT", "50054"))


def wait_tcp(host, port, timeout=10.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=1.0)
            s.close()
            return True
        except OSError as e:
            last = e
            time.sleep(0.2)
    raise AssertionError(f"TCP {host}:{port} not reachable after {timeout}s: {last}")


@pytest.fixture(scope="session", autouse=True)
def _wait_controller(controller_ip):
    wait_tcp(controller_ip, 50051, timeout=15.0)
