#!/usr/bin/env python3
"""Dream Server — Cluster auto-discovery via UDP broadcast.

Provides two primitives:
  - ClusterBeacon: broadcasts controller presence on the LAN every 5s
  - discover_controller(): listens for a beacon and returns the controller address

Protocol: UDP broadcast to port 50053
  Payload: b"DREAM1" + JSON {"service":"dream-cluster","version":1,
                              "controller_ip":"...","setup_port":50051}

Usage from other scripts:
  from cluster_discovery import ClusterBeacon, discover_controller

  # Controller side — start beacon as daemon thread
  beacon = ClusterBeacon(controller_ip="192.168.1.10", setup_port=50051)
  beacon.start()

  # Worker side — wait for a beacon
  ip, port = discover_controller(timeout=30)
"""

import json
import socket
import struct
import sys
import threading
import time

DISCOVERY_PORT = 50053
BEACON_INTERVAL = 5.0
MAGIC = b"DREAM1"


class ClusterBeacon(threading.Thread):
    """Broadcasts controller presence via UDP every BEACON_INTERVAL seconds."""

    def __init__(self, controller_ip, setup_port=50051):
        super().__init__(daemon=True)
        self._controller_ip = controller_ip
        self._setup_port = setup_port
        self._stop_event = threading.Event()

    def run(self):
        payload = MAGIC + json.dumps({
            "service": "dream-cluster",
            "version": 1,
            "controller_ip": self._controller_ip,
            "setup_port": self._setup_port,
        }).encode()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1.0)

        while not self._stop_event.is_set():
            try:
                sock.sendto(payload, ("<broadcast>", DISCOVERY_PORT))
            except OSError:
                pass  # transient network issue, retry next interval
            self._stop_event.wait(BEACON_INTERVAL)

        sock.close()

    def stop(self):
        self._stop_event.set()


def discover_controller(timeout=30):
    """Listen for a controller beacon. Returns (controller_ip, setup_port) or raises TimeoutError."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Allow receiving broadcast on some systems
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except OSError:
        pass

    sock.bind(("", DISCOVERY_PORT))
    sock.settimeout(min(timeout, 5.0))

    deadline = time.monotonic() + timeout

    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(min(remaining, 5.0))
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue

            if not data.startswith(MAGIC):
                continue

            try:
                msg = json.loads(data[len(MAGIC):])
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            if msg.get("service") != "dream-cluster" or msg.get("version") != 1:
                continue

            controller_ip = msg.get("controller_ip", addr[0])
            setup_port = msg.get("setup_port", 50051)
            return controller_ip, setup_port
    finally:
        sock.close()

    raise TimeoutError(f"No controller beacon received within {timeout}s")


def main():
    """CLI entry point for testing: discover or beacon mode."""
    if len(sys.argv) < 2 or sys.argv[1] not in ("beacon", "discover"):
        print(f"Usage: {sys.argv[0]} beacon <IP> [PORT] | discover [TIMEOUT]")
        sys.exit(1)

    if sys.argv[1] == "beacon":
        ip = sys.argv[2] if len(sys.argv) > 2 else "0.0.0.0"
        port = int(sys.argv[3]) if len(sys.argv) > 3 else 50051
        print(f"Broadcasting beacon: {ip}:{port} every {BEACON_INTERVAL}s on UDP {DISCOVERY_PORT}")
        b = ClusterBeacon(ip, port)
        b.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            b.stop()
    else:
        timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        print(f"Listening for controller beacon (timeout {timeout}s)...")
        try:
            ip, port = discover_controller(timeout)
            print(f"Found controller: {ip}:{port}")
        except TimeoutError as e:
            print(str(e))
            sys.exit(1)


if __name__ == "__main__":
    main()
