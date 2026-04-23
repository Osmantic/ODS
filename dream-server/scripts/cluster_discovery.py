#!/usr/bin/env python3
"""Dream Server — Cluster auto-discovery via UDP broadcast.

Provides two primitives:
  - ClusterBeacon: broadcasts controller presence on the LAN every 5s
  - discover_controller(): listens for a beacon and returns the controller address

Both support an optional bind_ip parameter to restrict to a specific
network interface (e.g. "192.168.1.10" to use only the LAN interface).

Protocol: UDP broadcast to port 50053
  Unsigned (legacy): b"DREAM1" + JSON {"service":"dream-cluster","version":1,
                                        "controller_ip":"...","setup_port":50051}
  Signed (preferred): b"DREAM2" + JSON {"service":"dream-cluster","version":2,
                                         "controller_ip":"...","setup_port":50051,
                                         "ts":<unix>, "mac":"<hex-hmac-sha256>"}

Signed beacons protect against a LAN attacker injecting a fake beacon
that redirects workers at an attacker-controlled setup port (see H1 of
round-2 review). Workers that know the shared cluster token verify the
MAC before connecting; unsigned beacons are still accepted for unit
tests / CLI debugging that don't pass a token.

Usage from other scripts:
  from cluster_discovery import ClusterBeacon, discover_controller

  # Controller side — start beacon as daemon thread (signed)
  beacon = ClusterBeacon(controller_ip="192.168.1.10", setup_port=50051,
                          token="dream_abc...")
  beacon.start()

  # Worker side — wait for a beacon and require a valid MAC
  ip, port = discover_controller(timeout=30, expected_token="dream_abc...")

  # Restrict to specific interface
  beacon = ClusterBeacon(controller_ip="192.168.1.10", bind_ip="192.168.1.10",
                          token="dream_abc...")
  ip, port = discover_controller(timeout=30, bind_ip="192.168.1.50",
                                  expected_token="dream_abc...")
"""

import hmac
import hashlib
import json
import socket
import sys
import threading
import time

DISCOVERY_PORT = 50053
BEACON_INTERVAL = 5.0
MAGIC = b"DREAM1"          # legacy unsigned beacon (kept for CLI `discover`)
MAGIC_SIGNED = b"DREAM2"   # signed beacon (HMAC-SHA256 over canonical JSON)

# Beacon timestamp staleness window. A signed beacon whose `ts` differs
# from the worker's clock by more than this is rejected — bounds replay
# attacks on recorded beacons. Five minutes tolerates typical NTP drift
# on unmanaged LAN hosts without accepting arbitrarily old captures.
BEACON_MAX_SKEW_SECONDS = 300

# The /24 broadcast computation assumes a typical home/SMB LAN
# (255.255.255.0 netmask). For /16, /23, or subnetted-beyond-/24 networks,
# beacons sent to the computed /24 broadcast may never reach workers on a
# different subnet. Workarounds:
#   - Operators can pass the explicit controller IP to `dream cluster join`
#     and skip discovery entirely.
#   - Operators on non-/24 LANs should run the controller on the same
#     subnet as workers, or use a VPN (Tailscale/WireGuard) where all nodes
#     share a single /24 overlay.
# We do NOT try to autodetect the real netmask — Python has no stdlib for
# that without platform-specific ioctls, and getting it wrong is worse
# than the current behaviour.


def _compute_broadcast(ip):
    """Compute /24 broadcast address from an IP. Falls back to 255.255.255.255."""
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
    return "255.255.255.255"


def _sign_beacon(body, token):
    """Return HMAC-SHA256(hex) over `body` keyed on `token`.

    `body` is the bytes of the canonical JSON *without* the `mac` field;
    signing over canonical bytes (sort_keys, no whitespace) avoids
    cross-implementation disagreement over field ordering.
    """
    return hmac.new(token.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _build_signed_payload(controller_ip, setup_port, token):
    """Return the wire-format bytes for a signed beacon."""
    body = {
        "service": "dream-cluster",
        "version": 2,
        "controller_ip": controller_ip,
        "setup_port": setup_port,
        "ts": int(time.time()),
    }
    body_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body["mac"] = _sign_beacon(body_bytes, token)
    return MAGIC_SIGNED + json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _build_unsigned_payload(controller_ip, setup_port):
    """Legacy unsigned beacon — kept only for the CLI `discover` helper."""
    return MAGIC + json.dumps({
        "service": "dream-cluster",
        "version": 1,
        "controller_ip": controller_ip,
        "setup_port": setup_port,
    }).encode("utf-8")


def _verify_signed_beacon(msg, expected_token, now=None):
    """Return True if `msg` is a signed beacon with a valid MAC and ts.

    The MAC is recomputed over the canonical JSON *minus* the `mac` field;
    any whitespace / field-order difference on the sender's side changes
    the bytes, so both sides must use sort_keys + compact separators.
    """
    mac = msg.get("mac")
    if not isinstance(mac, str):
        return False
    ts = msg.get("ts")
    if not isinstance(ts, int) or isinstance(ts, bool):
        return False
    now_s = int(now if now is not None else time.time())
    if abs(now_s - ts) > BEACON_MAX_SKEW_SECONDS:
        return False
    body = {k: v for k, v in msg.items() if k != "mac"}
    body_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected_mac = _sign_beacon(body_bytes, expected_token)
    return hmac.compare_digest(mac, expected_mac)


class ClusterBeacon(threading.Thread):
    """Broadcasts controller presence via UDP every BEACON_INTERVAL seconds.

    Args:
        controller_ip: IP address to advertise in the beacon payload.
        setup_port: TCP port for the setup listener.
        bind_ip: If set, bind the socket to this IP and broadcast on its /24.
                 If empty/None, broadcast to 255.255.255.255 on all interfaces.
        token: If set, sign the beacon with HMAC-SHA256 so only workers that
               hold the same token will trust it. If None, falls back to the
               unsigned DREAM1 format (for the stand-alone CLI debugging
               path; production call sites always pass the token).
    """

    def __init__(self, controller_ip, setup_port=50051, bind_ip=None, token=None):
        super().__init__(daemon=True)
        self._controller_ip = controller_ip
        self._setup_port = setup_port
        self._bind_ip = bind_ip or ""
        self._token = token
        self._stop_event = threading.Event()

    def _build_payload(self):
        """Re-build on every tick so `ts` advances (signed beacons only)."""
        if self._token:
            return _build_signed_payload(
                self._controller_ip, self._setup_port, self._token
            )
        return _build_unsigned_payload(self._controller_ip, self._setup_port)

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1.0)

        if self._bind_ip:
            sock.bind((self._bind_ip, 0))
            broadcast_addr = _compute_broadcast(self._bind_ip)
        else:
            broadcast_addr = "<broadcast>"

        # Track transient failures: log first and every Nth after so we
        # don't flood logs, but operators can still see when discovery is
        # silently broken (e.g. firewall drops, interface flap).
        consecutive_failures = 0
        while not self._stop_event.is_set():
            # Rebuild each tick so signed beacons carry a fresh timestamp;
            # unsigned beacons produce the same bytes every time but the
            # extra work is negligible (one JSON encode per 5s).
            payload = self._build_payload()
            try:
                sock.sendto(payload, (broadcast_addr, DISCOVERY_PORT))
                if consecutive_failures:
                    print(
                        f"[BEACON] Broadcast recovered after {consecutive_failures} failure(s)",
                        file=sys.stderr,
                    )
                consecutive_failures = 0
            except OSError as e:
                consecutive_failures += 1
                # Log on 1st, 10th, 100th, ... so persistent failures
                # surface without spamming journald every 5 seconds.
                if consecutive_failures in (1, 10) or consecutive_failures % 100 == 0:
                    print(
                        f"[BEACON] Broadcast send failed "
                        f"({broadcast_addr}:{DISCOVERY_PORT}): {e} "
                        f"[consecutive_failures={consecutive_failures}]",
                        file=sys.stderr,
                    )
            self._stop_event.wait(BEACON_INTERVAL)

        sock.close()

    def stop(self):
        self._stop_event.set()


def discover_controller(timeout=30, bind_ip=None, expected_token=None):
    """Listen for a controller beacon.

    Args:
        timeout: seconds to wait.
        bind_ip: if set, only listen on this interface.
        expected_token: if set, reject any beacon that isn't a valid
            signed (DREAM2) beacon whose HMAC verifies against this token.
            Callers that hold the shared cluster token MUST pass it here
            — otherwise a LAN attacker can inject a fake unsigned beacon
            that redirects the worker at an attacker-controlled setup port
            (H1 of round-2 review).

    Returns (controller_ip, setup_port) or raises TimeoutError.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # SO_BROADCAST is only required for *sending* to the broadcast address;
    # this function only receives, so failure here is non-fatal but worth
    # surfacing (hints at sandboxing / seccomp) instead of silently ignoring.
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except OSError as e:
        print(f"[DISCOVERY] SO_BROADCAST not settable on listen socket: {e}",
              file=sys.stderr)

    sock.bind((bind_ip or "", DISCOVERY_PORT))
    sock.settimeout(min(timeout, 5.0))

    deadline = time.monotonic() + timeout
    rejected_unsigned = 0  # count dropped unsigned beacons when token required

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

            if data.startswith(MAGIC_SIGNED):
                try:
                    msg = json.loads(data[len(MAGIC_SIGNED):])
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("service") != "dream-cluster" or msg.get("version") != 2:
                    continue
                if expected_token is not None and not _verify_signed_beacon(msg, expected_token):
                    # Bad MAC or stale timestamp — treat as hostile, don't connect.
                    continue
                controller_ip = msg.get("controller_ip", addr[0])
                setup_port = msg.get("setup_port", 50051)
                if not isinstance(setup_port, int) or not (1 <= setup_port <= 65535):
                    continue
                return controller_ip, setup_port

            if data.startswith(MAGIC):
                # Legacy unsigned beacon. If the caller required a token,
                # refuse unsigned beacons outright (they can't be trusted).
                if expected_token is not None:
                    rejected_unsigned += 1
                    if rejected_unsigned in (1, 10) or rejected_unsigned % 100 == 0:
                        print(
                            f"[DISCOVERY] Ignoring unsigned beacon from {addr[0]} "
                            f"[rejected={rejected_unsigned}] — operator must upgrade "
                            f"the controller or pass --controller <ip>.",
                            file=sys.stderr,
                        )
                    continue
                try:
                    msg = json.loads(data[len(MAGIC):])
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("service") != "dream-cluster" or msg.get("version") != 1:
                    continue
                controller_ip = msg.get("controller_ip", addr[0])
                setup_port = msg.get("setup_port", 50051)
                if not isinstance(setup_port, int) or not (1 <= setup_port <= 65535):
                    continue
                return controller_ip, setup_port

            # Not a DREAM beacon — ignore.
    finally:
        sock.close()

    raise TimeoutError(f"No controller beacon received within {timeout}s")


def main():
    """CLI entry point for testing: discover or beacon mode.

    Token is read from CLUSTER_TOKEN env var in both modes. If set, the
    beacon is signed and the discover path requires a valid MAC. If
    unset, behavior falls back to the legacy unsigned protocol — only
    intended for local debugging, never for production use.
    """
    import os
    if len(sys.argv) < 2 or sys.argv[1] not in ("beacon", "discover"):
        print(f"Usage: {sys.argv[0]} beacon <IP> [PORT] [BIND_IP] | discover [TIMEOUT] [BIND_IP]")
        sys.exit(1)

    token = os.environ.get("CLUSTER_TOKEN", "").strip() or None

    if sys.argv[1] == "beacon":
        ip = sys.argv[2] if len(sys.argv) > 2 else "0.0.0.0"
        port = int(sys.argv[3]) if len(sys.argv) > 3 else 50051
        bind = sys.argv[4] if len(sys.argv) > 4 else None
        kind = "signed" if token else "unsigned (debug)"
        print(f"Broadcasting {kind} beacon: {ip}:{port} every {BEACON_INTERVAL}s on UDP {DISCOVERY_PORT}" +
              (f" (bound to {bind})" if bind else ""))
        b = ClusterBeacon(ip, port, bind_ip=bind, token=token)
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
              (f" on {bind}" if bind else "") +
              (" — requiring signed beacon" if token else " — accepting unsigned (debug)") + "...")
        try:
            ip, port = discover_controller(timeout, bind_ip=bind, expected_token=token)
            print(f"Found controller: {ip}:{port}")
        except TimeoutError as e:
            print(str(e))
            sys.exit(1)


if __name__ == "__main__":
    main()
