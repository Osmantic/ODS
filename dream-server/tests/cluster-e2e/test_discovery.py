"""UDP broadcast beacon tests.

Exercises cluster_discovery.ClusterBeacon + discover_controller() against
a real controller running in a peer container. The controller's setup
listener spawns a ClusterBeacon on port 50053 on startup.
"""
import json
import socket
import threading
import time

import pytest

from cluster_discovery import (
    DISCOVERY_PORT,
    MAGIC,
    ClusterBeacon,
    discover_controller,
    _compute_broadcast,
)


def test_discover_real_controller(controller_ip):
    ip, port = discover_controller(timeout=15)
    assert ip == controller_ip
    assert port == 50051


def test_beacon_payload_shape(controller_ip):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", DISCOVERY_PORT))
    sock.settimeout(10)
    data, addr = sock.recvfrom(4096)
    sock.close()

    assert data.startswith(MAGIC), f"missing magic, got {data[:8]!r}"
    msg = json.loads(data[len(MAGIC):])
    assert msg["service"] == "dream-cluster"
    assert msg["version"] == 1
    assert msg["controller_ip"] == controller_ip
    assert msg["setup_port"] == 50051
    assert addr[0] == controller_ip


def test_beacon_sent_to_subnet_broadcast(controller_ip, broadcast_addr):
    """Binding to the subnet broadcast address (172.30.10.255) proves the
    packet was addressed to the broadcast IP, not unicast to this container.

    On Linux a UDP socket bound to a specific address only receives packets
    whose destination matches that address — so if this receive succeeds,
    the beacon is genuinely using broadcast.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((broadcast_addr, DISCOVERY_PORT))
    sock.settimeout(10)
    data, addr = sock.recvfrom(4096)
    sock.close()
    assert data.startswith(MAGIC), f"missing magic, got {data[:8]!r}"
    assert addr[0] == controller_ip


def test_beacon_period_under_six_seconds(controller_ip):
    """Two beacons must arrive inside BEACON_INTERVAL + slack."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", DISCOVERY_PORT))
    sock.settimeout(12)
    sock.recvfrom(4096)
    t0 = time.monotonic()
    sock.recvfrom(4096)
    elapsed = time.monotonic() - t0
    sock.close()
    assert 1.0 <= elapsed <= 7.0, f"beacon interval out of range: {elapsed:.2f}s"


def test_local_beacon_roundtrip():
    """ClusterBeacon → discover_controller on the same container via broadcast."""
    beacon = ClusterBeacon(controller_ip="10.99.99.99", setup_port=55551)
    beacon.start()
    try:
        time.sleep(0.5)
        # Cannot collide with real DISCOVERY_PORT (controller beacon already bound).
        # Use a raw socket bound broadly and filter by payload.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
        sock.settimeout(10)
        deadline = time.monotonic() + 10
        found = None
        while time.monotonic() < deadline:
            data, _ = sock.recvfrom(4096)
            if not data.startswith(MAGIC):
                continue
            msg = json.loads(data[len(MAGIC):])
            if msg.get("controller_ip") == "10.99.99.99":
                found = msg
                break
        sock.close()
        assert found is not None, "local beacon payload never received"
        assert found["setup_port"] == 55551
    finally:
        beacon.stop()


def test_discover_timeout_when_no_beacon():
    with pytest.raises(TimeoutError):
        # Bind to a non-existent IP so nothing can arrive.
        discover_controller(timeout=2, bind_ip="127.0.0.1")


def test_compute_broadcast_ipv4():
    assert _compute_broadcast("172.30.10.10") == "172.30.10.255"
    assert _compute_broadcast("192.168.1.50") == "192.168.1.255"


def test_compute_broadcast_fallback():
    assert _compute_broadcast("not-an-ip") == "255.255.255.255"


def test_malformed_payload_ignored():
    """Foreign UDP traffic on the discovery port must not confuse discover()."""
    stop = threading.Event()

    def spam():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while not stop.is_set():
            s.sendto(b"GARBAGE-NOT-DREAM", ("172.30.10.255", DISCOVERY_PORT))
            time.sleep(0.2)
        s.close()

    t = threading.Thread(target=spam, daemon=True)
    t.start()
    try:
        ip, port = discover_controller(timeout=10)
        assert port == 50051
    finally:
        stop.set()
        t.join(timeout=1)
