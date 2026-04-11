#!/usr/bin/env python3
"""Dream Server — Cluster join client.

Runs on a worker during 'dream cluster join'. Connects to the controller's
setup listener, sends GPU info and token, waits for acceptance.

Usage:
  python3 cluster-join-client.py --controller-ip IP --port 50051 \
      --token TOKEN --gpu-backend BACKEND --rpc-port 50052 [--gpu-json '...']

Exit codes: 0 = accepted, 1 = rejected/error
"""

import argparse
import json
import socket
import sys


def parse_args():
    p = argparse.ArgumentParser(description="Cluster join client")
    p.add_argument("--controller-ip", required=True)
    p.add_argument("--port", type=int, default=50051)
    p.add_argument("--token", required=True)
    p.add_argument("--gpu-backend", default="cpu")
    p.add_argument("--rpc-port", type=int, default=50052)
    p.add_argument("--gpu-json", default="[]", help="JSON array of GPU info")
    return p.parse_args()


def main():
    args = parse_args()

    gpus = json.loads(args.gpu_json)

    msg = {
        "action": "join",
        "token": args.token,
        "gpu_backend": args.gpu_backend,
        "gpus": gpus,
        "rpc_port": args.rpc_port,
    }

    try:
        sock = socket.create_connection((args.controller_ip, args.port), timeout=10)
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        print(f"Cannot reach controller at {args.controller_ip}:{args.port}: {e}",
              file=sys.stderr)
        sys.exit(1)

    sock.sendall(json.dumps(msg).encode() + b"\n")

    # Wait for response (controller may take a while if operator is confirming)
    sock.settimeout(300)  # 5 min timeout for human confirmation
    buf = b""
    try:
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    except socket.timeout:
        print("Timed out waiting for controller response.", file=sys.stderr)
        sock.close()
        sys.exit(1)

    sock.close()

    if not buf:
        print("Controller closed connection without responding.", file=sys.stderr)
        sys.exit(1)

    resp = json.loads(buf.split(b"\n", 1)[0])
    status = resp.get("status", "unknown")

    if status == "accepted":
        note = resp.get("note", "")
        if note:
            print(note)
        sys.exit(0)
    else:
        reason = resp.get("reason", "unknown")
        print(f"Rejected: {reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
