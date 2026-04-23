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
    MAGIC_SIGNED,
    ClusterBeacon,
    discover_controller,
    _compute_broadcast,
    _verify_signed_beacon,
)


def test_discover_real_controller(controller_ip, token):
    """Signed beacon from the real controller must be accepted with the right token."""
    ip, port = discover_controller(timeout=15, expected_token=token)
    assert ip == controller_ip
    assert port == 50051


def test_beacon_payload_shape(controller_ip, token):
    """Controller emits a signed (DREAM2) beacon with valid MAC."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", DISCOVERY_PORT))
    sock.settimeout(10)
    data, addr = sock.recvfrom(4096)
    sock.close()

    assert data.startswith(MAGIC_SIGNED), f"expected signed magic, got {data[:8]!r}"
    msg = json.loads(data[len(MAGIC_SIGNED):])
    assert msg["service"] == "dream-cluster"
    assert msg["version"] == 2
    assert msg["controller_ip"] == controller_ip
    assert msg["setup_port"] == 50051
    assert "ts" in msg and isinstance(msg["ts"], int)
    assert "mac" in msg and isinstance(msg["mac"], str)
    assert addr[0] == controller_ip
    # MAC must verify with the real token.
    assert _verify_signed_beacon(msg, token) is True


def test_tampered_mac_rejected(controller_ip, token):
    """H1 / H9: discover_controller must reject a beacon whose MAC was altered.

    Sniff a real signed beacon, flip one nibble of the MAC, re-emit it on
    loopback, and assert discover_controller ignores it and eventually
    times out (no real controller is visible on 127.0.0.1).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", DISCOVERY_PORT))
    sock.settimeout(10)
    data, _ = sock.recvfrom(4096)
    sock.close()
    assert data.startswith(MAGIC_SIGNED)

    msg = json.loads(data[len(MAGIC_SIGNED):])
    orig_mac = msg["mac"]
    flipped = ("b" if orig_mac[0] != "b" else "a") + orig_mac[1:]
    msg["mac"] = flipped
    assert _verify_signed_beacon(msg, token) is False, "flipped MAC must not verify"
    # A fresh MAC over the tampered body must also fail with a different token.
    assert _verify_signed_beacon(msg, token + "x") is False


def test_stale_timestamp_rejected(token):
    """A beacon with ts far outside the skew window is treated as replay."""
    msg = {
        "service": "dream-cluster",
        "version": 2,
        "controller_ip": "10.0.0.1",
        "setup_port": 50051,
        "ts": int(time.time()) - 10_000,  # well past BEACON_MAX_SKEW_SECONDS
        "mac": "deadbeef" * 8,  # irrelevant — ts check fires first
    }
    assert _verify_signed_beacon(msg, token) is False


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
    assert data.startswith(MAGIC_SIGNED), f"expected signed magic, got {data[:8]!r}"
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
    """ClusterBeacon → discover_controller on the same container via broadcast.

    Uses an in-test token to exercise the signed path end-to-end without
    needing the real controller.
    """
    test_token = "local-test-token-roundtrip"
    beacon = ClusterBeacon(
        controller_ip="10.99.99.99", setup_port=55551, token=test_token
    )
    beacon.start()
    try:
        time.sleep(0.5)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
        sock.settimeout(10)
        deadline = time.monotonic() + 10
        found = None
        while time.monotonic() < deadline:
            data, _ = sock.recvfrom(4096)
            if not data.startswith(MAGIC_SIGNED):
                continue
            msg = json.loads(data[len(MAGIC_SIGNED):])
            if msg.get("controller_ip") == "10.99.99.99":
                assert _verify_signed_beacon(msg, test_token) is True
                found = msg
                break
        sock.close()
        assert found is not None, "local signed beacon payload never received"
        assert found["setup_port"] == 55551
    finally:
        beacon.stop()


def test_unsigned_beacon_rejected_when_token_required():
    """If the caller requires a token, legacy DREAM1 beacons must be ignored."""
    stop = threading.Event()

    def send_unsigned():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        unsigned = MAGIC + json.dumps({
            "service": "dream-cluster", "version": 1,
            "controller_ip": "10.0.0.99", "setup_port": 50051,
        }).encode()
        while not stop.is_set():
            s.sendto(unsigned, ("127.0.0.1", DISCOVERY_PORT))
            time.sleep(0.2)
        s.close()

    t = threading.Thread(target=send_unsigned, daemon=True)
    t.start()
    try:
        with pytest.raises(TimeoutError):
            discover_controller(timeout=2, bind_ip="127.0.0.1", expected_token="T")
    finally:
        stop.set()
        t.join(timeout=1)


def test_discover_timeout_when_no_beacon():
    with pytest.raises(TimeoutError):
        discover_controller(timeout=2, bind_ip="127.0.0.1")


def test_compute_broadcast_ipv4():
    assert _compute_broadcast("172.30.10.10") == "172.30.10.255"
    assert _compute_broadcast("192.168.1.50") == "192.168.1.255"


def test_compute_broadcast_fallback():
    assert _compute_broadcast("not-an-ip") == "255.255.255.255"


def test_malformed_payload_ignored(token):
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
        ip, port = discover_controller(timeout=10, expected_token=token)
        assert port == 50051
    finally:
        stop.set()
        t.join(timeout=1)
