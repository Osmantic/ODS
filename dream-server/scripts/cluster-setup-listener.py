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
import json
import os
import signal
import socket
import sys
import time


def parse_args():
    p = argparse.ArgumentParser(description="Cluster setup listener")
    p.add_argument("--port", type=int, default=50051)
    p.add_argument("--token", required=True)
    p.add_argument("--config", required=True, help="Path to cluster.json")
    return p.parse_args()


def recv_json(conn, timeout=30):
    """Read newline-delimited JSON from a socket."""
    conn.settimeout(timeout)
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
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
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
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


def main():
    args = parse_args()

    if not os.path.isfile(args.config):
        print(f"Error: cluster config not found at {args.config}", file=sys.stderr)
        sys.exit(1)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(5)
    srv.settimeout(1.0)  # allows keyboard interrupt check

    workers_added = 0

    print(f"\n\033[0;34m--- Cluster Setup ---\033[0m\n")
    print(f"  Listening on port {args.port}")
    print(f"  Waiting for workers to connect...")
    print(f"  Press Ctrl+C when all workers have joined.\n")

    def handle_shutdown(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        while True:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue

            worker_ip = addr[0]
            print(f"  Connection from {worker_ip}...")

            try:
                msg = recv_json(conn)
            except (json.JSONDecodeError, socket.timeout, OSError) as e:
                print(f"  Bad request from {worker_ip}: {e}")
                conn.close()
                continue

            if msg is None:
                print(f"  Empty request from {worker_ip}")
                conn.close()
                continue

            if msg.get("action") != "join":
                send_json(conn, {"status": "rejected", "reason": "unknown action"})
                conn.close()
                continue

            if msg.get("token") != args.token:
                print(f"  \033[0;31mRejected\033[0m {worker_ip}: invalid token")
                send_json(conn, {"status": "rejected", "reason": "invalid token"})
                conn.close()
                continue

            gpu_backend = msg.get("gpu_backend", "unknown")
            gpus = msg.get("gpus", [])
            rpc_port = msg.get("rpc_port", 50052)
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
        srv.close()

    print(f"\n  Setup complete. {workers_added} worker(s) added.")

    # Print exit code hint for the shell wrapper
    # 0 = workers were added (rebuild env), 1 = no workers added
    sys.exit(0 if workers_added > 0 else 1)


if __name__ == "__main__":
    main()
