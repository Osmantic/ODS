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
import os
import socket
import sys

# Mirror of HANDSHAKE_MAX_BYTES in cluster_worker_agent.py. Bounds a hostile or
# buggy controller from streaming bytes until this process OOMs — the socket
# timeout only fires on idle periods between recvs, not on total bytes.
HANDSHAKE_MAX_BYTES = 1_048_576  # 1 MiB


def parse_args():
    p = argparse.ArgumentParser(description="Cluster join client")
    p.add_argument("--controller-ip", required=True)
    p.add_argument("--port", type=int, default=50051)
    p.add_argument("--token",
                   help="Shared join token (prefer --token-file or CLUSTER_TOKEN env — "
                        "--token is visible via `ps`)")
    p.add_argument("--token-file", help="Path to a file containing the join token (mode 0600)")
    p.add_argument("--gpu-backend", default="cpu")
    p.add_argument("--rpc-port", type=int, default=50052)
    p.add_argument("--gpu-json", default="[]", help="JSON array of GPU info")
    return p.parse_args()


def _resolve_token(args):
    """Priority: --token-file > CLUSTER_TOKEN env > --token CLI (deprecated)."""
    if args.token_file:
        path = os.path.expanduser(args.token_file)
        try:
            with open(path) as f:
                token = f.read().strip()
        except OSError as e:
            print(f"Error: cannot read --token-file {path!r}: {e}", file=sys.stderr)
            sys.exit(1)
        if not token:
            print(f"Error: --token-file {path!r} is empty", file=sys.stderr)
            sys.exit(1)
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
    sys.exit(1)


def main():
    args = parse_args()

    token = _resolve_token(args)
    gpus = json.loads(args.gpu_json)

    msg = {
        "action": "join",
        "token": token,
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
            if len(buf) > HANDSHAKE_MAX_BYTES:
                print(
                    f"Controller response exceeded {HANDSHAKE_MAX_BYTES} bytes — aborting.",
                    file=sys.stderr,
                )
                sock.close()
                sys.exit(1)
    except socket.timeout:
        print("Timed out waiting for controller response.", file=sys.stderr)
        sock.close()
        sys.exit(1)

    sock.close()

    if not buf:
        print("Controller closed connection without responding.", file=sys.stderr)
        sys.exit(1)

    try:
        resp = json.loads(buf.split(b"\n", 1)[0])
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"Malformed response from controller: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(resp, dict):
        print(f"Unexpected response shape from controller: {type(resp).__name__}",
              file=sys.stderr)
        sys.exit(1)
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
