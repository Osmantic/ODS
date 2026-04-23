#!/usr/bin/env python3
"""Dream Server — Cluster setup listener.

Runs on the controller during 'dream cluster setup'. Listens on a TCP port
for incoming worker join requests, validates the token, displays worker info,
and asks the operator to accept or reject each worker.

Protocol (JSON over TCP, newline-delimited):
  Worker -> Controller:  {"action":"join", "token":"...", "gpu_backend":"...",
                          "gpus":[{"name":"...","vram_mb":N}], "rpc_port":N}
  Controller -> Worker:  {"status":"accepted"} or {"status":"rejected","reason":"..."}

Usage:
  python3 cluster-setup-listener.py --port 50051 --token TOKEN --config PATH
"""

import argparse
import hmac
import json
import os
import signal
import socket
import sys
import threading
import time

_stop_event = threading.Event()

# Auto-discovery beacon (same directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cluster_discovery import ClusterBeacon, DISCOVERY_PORT

# Hard cap on handshake payload. A LAN attacker (or a bugged worker) can
# otherwise stream bytes until the process OOMs, because the socket timeout
# only fires on idle periods between recvs — not on total bytes received.
MAX_HANDSHAKE_BYTES = 1_048_576  # 1 MiB

# Brute-force resistance: the 32-hex-char token has ~128 bits of entropy, so an
# online guessing attack is not the weakest link. hmac.compare_digest below
# blunts timing side-channels; we do not currently rate-limit connections.
VALID_ACTIONS = frozenset({"join"})
VALID_GPU_BACKENDS = frozenset({"cpu", "nvidia", "amd"})


class HandshakeTooLarge(Exception):
    """Controller received more handshake bytes than MAX_HANDSHAKE_BYTES."""


def parse_args():
    p = argparse.ArgumentParser(description="Cluster setup listener")
    p.add_argument("--port", type=int, default=50051)
    p.add_argument("--token", help="Shared join token (prefer --token-file or CLUSTER_TOKEN env)")
    p.add_argument("--token-file", help="Path to a file containing the join token (mode 0600)")
    p.add_argument("--config", required=True, help="Path to cluster.json")
    p.add_argument("--bind", default="",
                   help="IP to bind on (defaults to CLUSTER_INTERFACE env, "
                        "else 0.0.0.0 for LAN reachability)")
    return p.parse_args()


def recv_json(conn, timeout=30, max_bytes=MAX_HANDSHAKE_BYTES):
    """Read a newline-delimited JSON message, bounded.

    Raises HandshakeTooLarge if the buffer would grow past max_bytes; this
    closes the memory-DoS vector on the LAN-reachable setup port.
    """
    conn.settimeout(timeout)
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
        if len(buf) > max_bytes:
            raise HandshakeTooLarge(
                f"handshake payload exceeded {max_bytes} bytes (got {len(buf)})"
            )
    return json.loads(buf.split(b"\n", 1)[0])


def send_json(conn, obj):
    conn.sendall(json.dumps(obj).encode() + b"\n")


def add_worker_to_config(config_path, ip, rpc_port, gpu_backend, gpus):
    with open(config_path) as f:
        state = json.load(f)

    # Check for duplicate
    for node in state.get("nodes", []):
        if node["ip"] == ip and node.get("rpc_port", 50052) == rpc_port:
            return False, "already registered"

    state.setdefault("nodes", []).append({
        "ip": ip,
        "rpc_port": rpc_port,
        "gpu_backend": gpu_backend,
        "gpus": gpus,
        "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "online",
    })

    tmp = config_path + ".tmp"
    # Mirror AgentState._save() — O_EXCL|0o600 so the on-disk topology
    # (IPs, GPU inventory, join timestamps) isn't world-readable on shared
    # controllers. No secrets live here today, but the 0600 rollout in
    # 371eaa8 was meant to be consistent.
    if os.path.exists(tmp):
        os.unlink(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    os.replace(tmp, config_path)
    return True, "ok"


def format_gpu_info(gpus):
    if not gpus:
        return "no GPU info"
    parts = []
    for g in gpus:
        name = g.get("name", "Unknown")
        vram = g.get("vram_mb", 0)
        if vram > 0:
            parts.append(f"{name} ({round(vram / 1024, 1)} GB)")
        else:
            parts.append(name)
    return ", ".join(parts)


def _resolve_token(args):
    """Return the shared token from --token-file, --token, or CLUSTER_TOKEN env.

    Preference order: --token-file > CLUSTER_TOKEN env > --token CLI.
    --token emits a deprecation warning because it is visible to other local
    users via `ps`.
    """
    if args.token_file:
        path = os.path.expanduser(args.token_file)
        try:
            with open(path) as f:
                token = f.read().strip()
        except OSError as e:
            print(f"Error: cannot read --token-file {path!r}: {e}", file=sys.stderr)
            sys.exit(2)
        if not token:
            print(f"Error: --token-file {path!r} is empty", file=sys.stderr)
            sys.exit(2)
        return token
    env_token = os.environ.get("CLUSTER_TOKEN", "").strip()
    if env_token:
        return env_token
    if args.token:
        print("warning: --token on the command line is visible via `ps`; "
              "prefer --token-file or the CLUSTER_TOKEN env var",
              file=sys.stderr)
        return args.token
    print("Error: no token supplied (use --token-file, CLUSTER_TOKEN env, or --token)",
          file=sys.stderr)
    sys.exit(2)


def _sanitize_display_text(raw, max_len=128):
    """Strip control chars from untrusted text before printing to a terminal.

    Removes C0/C1 controls (including ESC) so a hostile worker can't inject
    ANSI sequences to repaint the screen and spoof the Y/n prompt that
    follows. Keeps printable ASCII + space + common latin-1 characters.
    """
    s = str(raw)[:max_len]
    # Drop: 0x00-0x1F (C0 incl. ESC), 0x7F (DEL), 0x80-0x9F (C1 controls)
    return "".join(c for c in s if 0x20 <= ord(c) < 0x7F or 0xA0 <= ord(c))


def _validate_join_payload(msg):
    """Return (ok, reason, parsed) for a join payload.

    Rejects the connection if gpu_backend is not in {cpu,nvidia,amd} or if
    rpc_port is missing/non-integer/out of range. Worker-controlled fields
    are treated as untrusted input — this data is later written to
    cluster.json and consumed by the supervisor.
    """
    if msg.get("action") not in VALID_ACTIONS:
        return False, "unknown action", None
    gpu_backend = msg.get("gpu_backend")
    if gpu_backend not in VALID_GPU_BACKENDS:
        return False, f"unsupported gpu_backend: {gpu_backend!r}", None
    rpc_port = msg.get("rpc_port", 50052)
    if not isinstance(rpc_port, int) or isinstance(rpc_port, bool):
        return False, "rpc_port must be an integer", None
    if not (1 <= rpc_port <= 65535):
        return False, f"rpc_port {rpc_port} out of range 1-65535", None
    gpus = msg.get("gpus", [])
    if not isinstance(gpus, list):
        return False, "gpus must be a list", None
    # Normalize GPU entries so only known fields land in cluster.json.
    sanitized_gpus = []
    for g in gpus:
        if not isinstance(g, dict):
            continue
        # Strip control chars — this name is printed to the operator's
        # terminal before a Y/n prompt. Without sanitization a hostile worker
        # could send ANSI escapes to spoof the prompt.
        name = _sanitize_display_text(g.get("name", "Unknown"), max_len=128) or "Unknown"
        try:
            vram_mb = int(g.get("vram_mb", 0))
        except (TypeError, ValueError):
            vram_mb = 0
        sanitized_gpus.append({"name": name, "vram_mb": max(0, vram_mb)})
    return True, "ok", {
        "gpu_backend": gpu_backend,
        "rpc_port": rpc_port,
        "gpus": sanitized_gpus,
    }


def _resolve_bind(cli_bind):
    """Pick the bind address for the setup TCP listener.

    Priority: --bind > CLUSTER_INTERFACE env > 0.0.0.0. 0.0.0.0 is the safe
    default for the setup listener because workers need to reach it; an
    operator who cares about interface isolation sets --bind.
    """
    if cli_bind:
        return cli_bind
    env_iface = os.environ.get("CLUSTER_INTERFACE", "").strip()
    if env_iface:
        return env_iface
    return "0.0.0.0"


def main():
    args = parse_args()

    if not os.path.isfile(args.config):
        print(f"Error: cluster config not found at {args.config}", file=sys.stderr)
        sys.exit(1)

    token = _resolve_token(args)

    bind_ip = _resolve_bind(args.bind)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((bind_ip, args.port))
    srv.listen(5)
    srv.settimeout(1.0)  # allows keyboard interrupt check

    workers_added = 0

    # Start auto-discovery beacon so workers can find us. The beacon's
    # advertised controller_ip must be a routable address (not 0.0.0.0,
    # not loopback) so workers know where to connect back to.
    controller_ip = os.environ.get("CLUSTER_INTERFACE", "").strip()
    if not controller_ip and bind_ip not in ("", "0.0.0.0"):
        controller_ip = bind_ip
    if not controller_ip:
        # "Connect" a UDP socket to a routable address — the kernel picks
        # the outbound interface IP without sending any packets, which is
        # the only stdlib-portable way to resolve the primary interface.
        # gethostbyname(gethostname()) is deliberately avoided: on Debian/
        # Ubuntu it returns 127.0.1.1, which would silently break every
        # worker join. If the UDP trick fails we refuse to guess.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("255.255.255.255", 1))
            controller_ip = s.getsockname()[0]
        finally:
            s.close()
    if not controller_ip or controller_ip.startswith("127."):
        print(
            f"Error: could not determine a routable controller IP "
            f"(got {controller_ip!r}). "
            f"Pass --bind <ip> or set CLUSTER_INTERFACE explicitly.",
            file=sys.stderr,
        )
        sys.exit(2)
    # Sign the beacon with the shared token so only workers that already
    # hold the same token will trust it. Prevents a LAN attacker from
    # redirecting workers at a fake setup port via forged beacons (H1).
    beacon = ClusterBeacon(
        controller_ip=controller_ip,
        setup_port=args.port,
        bind_ip=controller_ip,
        token=token,
    )
    beacon.start()

    print(f"\n\033[0;34m--- Cluster Setup ---\033[0m\n")
    print(f"  Listening on {bind_ip}:{args.port}")
    print(f"  Broadcasting signed discovery beacon on {controller_ip} (UDP {DISCOVERY_PORT})")
    print(f"  Waiting for workers to connect...")
    print(f"  Press Ctrl+C when all workers have joined.\n")

    def handle_shutdown(signum, frame):
        # Raising from a signal handler can interrupt C code mid-op
        # (json.load, os.replace) and leave cluster.json.tmp half-written.
        # Flip an Event instead; the accept loop polls via socket timeout.
        _stop_event.set()

    # SIGINT keeps Python's default (raises KeyboardInterrupt) so operator
    # Ctrl+C still breaks the blocking input() prompt. SIGTERM uses the
    # event path because it can arrive during a file write.
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        while not _stop_event.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue

            worker_ip = addr[0]
            print(f"  Connection from {worker_ip}...")

            try:
                msg = recv_json(conn)
            except HandshakeTooLarge as e:
                print(f"  \033[0;31mRejected\033[0m {worker_ip}: {e}")
                conn.close()
                continue
            except (json.JSONDecodeError, UnicodeDecodeError,
                    socket.timeout, OSError) as e:
                print(f"  Bad request from {worker_ip}: {e}")
                conn.close()
                continue

            if msg is None:
                print(f"  Empty request from {worker_ip}")
                conn.close()
                continue
            if not isinstance(msg, dict):
                send_json(conn, {"status": "rejected", "reason": "payload must be an object"})
                conn.close()
                continue

            # Token check is first — don't leak validation hints to unauthenticated peers.
            # hmac.compare_digest runs in constant time to blunt timing side-channels.
            received_token = msg.get("token")
            if not isinstance(received_token, str) or not hmac.compare_digest(received_token, token):
                print(f"  \033[0;31mRejected\033[0m {worker_ip}: invalid token")
                send_json(conn, {"status": "rejected", "reason": "invalid token"})
                conn.close()
                continue

            ok, reason, parsed = _validate_join_payload(msg)
            if not ok:
                print(f"  \033[0;31mRejected\033[0m {worker_ip}: {reason}")
                send_json(conn, {"status": "rejected", "reason": reason})
                conn.close()
                continue

            gpu_backend = parsed["gpu_backend"]
            gpus = parsed["gpus"]
            rpc_port = parsed["rpc_port"]
            gpu_info = format_gpu_info(gpus)

            print(f"\n  \033[0;33mNew worker wants to join:\033[0m")
            print(f"    IP:       {worker_ip}")
            print(f"    RPC port: {rpc_port}")
            print(f"    Backend:  {gpu_backend}")
            print(f"    GPU:      {gpu_info}")
            print()

            try:
                answer = input("  Accept this worker? [Y/n] ").strip().lower()
            except EOFError:
                answer = "y"

            if answer in ("", "y", "yes"):
                ok, reason = add_worker_to_config(
                    args.config, worker_ip, rpc_port, gpu_backend, gpus
                )
                if ok:
                    send_json(conn, {"status": "accepted"})
                    workers_added += 1
                    print(f"  \033[0;32mAccepted\033[0m {worker_ip}:{rpc_port}")
                    print(f"  Workers joined so far: {workers_added}\n")
                else:
                    send_json(conn, {"status": "accepted", "note": reason})
                    print(f"  \033[0;33m{worker_ip}:{rpc_port} {reason}\033[0m\n")
            else:
                send_json(conn, {"status": "rejected", "reason": "operator declined"})
                print(f"  \033[0;31mRejected\033[0m {worker_ip}\n")

            conn.close()

    except KeyboardInterrupt:
        pass
    finally:
        beacon.stop()
        srv.close()

    print(f"\n  Setup complete. {workers_added} worker(s) added.")

    # Print exit code hint for the shell wrapper
    # 0 = workers were added (rebuild env), 1 = no workers added
    sys.exit(0 if workers_added > 0 else 1)


if __name__ == "__main__":
    main()
