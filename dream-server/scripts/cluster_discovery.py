#!/usr/bin/env python3
"""Dream Server — Cluster auto-discovery via UDP broadcast.

Provides two primitives:
  - ClusterBeacon: broadcasts controller presence on the LAN every 5s
  - discover_controller(): listens for a beacon and returns the controller address

Both support an optional bind_ip parameter to restrict to a specific
network interface (e.g. "192.168.1.10" to use only the LAN interface).

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

  # Restrict to specific interface
  beacon = ClusterBeacon(controller_ip="192.168.1.10", bind_ip="192.168.1.10")
  ip, port = discover_controller(timeout=30, bind_ip="192.168.1.50")
"""

import json
import socket
import sys
import threading
import time

DISCOVERY_PORT = 50053
BEACON_INTERVAL = 5.0
MAGIC = b"DREAM1"


def get_interface_ips():
    """Return list of (ip, name_hint) for all non-loopback IPv4 addresses."""
    results = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                results.append(ip)
    except socket.gaierror:
        pass
    # Fallback: parse /proc/net/if_inet6 alternative or use netifaces-like approach
    # Simpler: just try all common interface patterns
    if not results:
        try:
            import subprocess
            out = subprocess.check_output(["hostname", "-I"], text=True).strip()
            results = [ip for ip in out.split() if ":" not in ip and not ip.startswith("127.")]
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    return sorted(set(results))


def _compute_broadcast(ip):
    """Compute /24 broadcast address from an IP. Falls back to 255.255.255.255."""
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
    return "255.255.255.255"


class ClusterBeacon(threading.Thread):
    """Broadcasts controller presence via UDP every BEACON_INTERVAL seconds.

    Args:
        controller_ip: IP address to advertise in the beacon payload.
        setup_port: TCP port for the setup listener.
        bind_ip: If set, bind the socket to this IP and broadcast on its /24.
                 If empty/None, broadcast to 255.255.255.255 on all interfaces.
    """

    def __init__(self, controller_ip, setup_port=50051, bind_ip=None):
        super().__init__(daemon=True)
        self._controller_ip = controller_ip
        self._setup_port = setup_port
        self._bind_ip = bind_ip or ""
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

        if self._bind_ip:
            sock.bind((self._bind_ip, 0))
            broadcast_addr = _compute_broadcast(self._bind_ip)
        else:
            broadcast_addr = "<broadcast>"

        while not self._stop_event.is_set():
            try:
                sock.sendto(payload, (broadcast_addr, DISCOVERY_PORT))
            except OSError:
                pass  # transient network issue, retry next interval
            self._stop_event.wait(BEACON_INTERVAL)

        sock.close()

    def stop(self):
        self._stop_event.set()


def discover_controller(timeout=30, bind_ip=None):
    """Listen for a controller beacon.

    Args:
        timeout: seconds to wait.
        bind_ip: if set, only listen on this interface.

    Returns (controller_ip, setup_port) or raises TimeoutError.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except OSError:
        pass

    sock.bind((bind_ip or "", DISCOVERY_PORT))
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
        print(f"Usage: {sys.argv[0]} beacon <IP> [PORT] [BIND_IP] | discover [TIMEOUT] [BIND_IP]")
        sys.exit(1)

    if sys.argv[1] == "beacon":
        ip = sys.argv[2] if len(sys.argv) > 2 else "0.0.0.0"
        port = int(sys.argv[3]) if len(sys.argv) > 3 else 50051
        bind = sys.argv[4] if len(sys.argv) > 4 else None
        print(f"Broadcasting beacon: {ip}:{port} every {BEACON_INTERVAL}s on UDP {DISCOVERY_PORT}" +
              (f" (bound to {bind})" if bind else ""))
        b = ClusterBeacon(ip, port, bind_ip=bind)
        b.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            b.stop()
    else:
        timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        bind = sys.argv[3] if len(sys.argv) > 3 else None
        print(f"Listening for controller beacon (timeout {timeout}s)" +
              (f" on {bind}" if bind else "") + "...")
        try:
            ip, port = discover_controller(timeout, bind_ip=bind)
            print(f"Found controller: {ip}:{port}")
        except TimeoutError as e:
            print(str(e))
            sys.exit(1)


if __name__ == "__main__":
    main()
