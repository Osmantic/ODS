#!/usr/bin/env python3
"""Dream Server — Cluster Worker Agent.

Long-lived host process that manages this machine's participation in a
LAN cluster. Handles:
  - Auto-discovery of the controller via UDP beacon
  - Joining the cluster (TCP handshake with token)
  - Starting and monitoring the rpc-server Docker container
  - Restarting rpc-server if it crashes
  - HTTP status endpoint for the dashboard to poll

Config: $INSTALL_DIR/config/cluster-agent.json
  {
    "token": "dream_...",
    "controller_ip": "...",      // set after discovery or manual join
    "setup_port": 50051,
    "rpc_port": 50052,
    "gpu_backend": "nvidia",
    "status": "idle"             // idle | joined | running | error
  }

Usage:
  python3 cluster_worker_agent.py --config PATH [--status-port 50054]
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Auto-discovery
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cluster_discovery import discover_controller

HEALTH_CHECK_INTERVAL = 10  # seconds between rpc-server health checks
DISCOVERY_TIMEOUT = 30      # seconds to wait for beacon per attempt
DISCOVERY_RETRY_INTERVAL = 15  # seconds between discovery attempts

_running = True


def _handle_term(signum, frame):
    global _running
    _running = False


class AgentState:
    """Thread-safe agent state backed by a JSON config file."""

    def __init__(self, config_path):
        self._path = config_path
        self._lock = threading.Lock()
        self._state = self._load()

    def _load(self):
        if os.path.isfile(self._path):
            with open(self._path) as f:
                return json.load(f)
        return {
            "token": "",
            "controller_ip": "",
            "setup_port": 50051,
            "rpc_port": 50052,
            "gpu_backend": "",
            "status": "idle",
        }

    def get(self, key, default=None):
        with self._lock:
            return self._state.get(key, default)

    def update(self, **kwargs):
        with self._lock:
            self._state.update(kwargs)
            self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._state, f, indent=2)
        os.replace(tmp, self._path)

    def snapshot(self):
        with self._lock:
            return dict(self._state)


def detect_gpu_backend():
    """Detect GPU backend from environment or hardware."""
    backend = os.environ.get("GPU_BACKEND", "")
    if backend:
        return backend
    # nvidia-smi present → nvidia
    if subprocess.run(["nvidia-smi"], capture_output=True).returncode == 0:
        return "nvidia"
    # /dev/kfd present → amd
    if os.path.exists("/dev/kfd"):
        return "amd"
    return "cpu"


def detect_gpus(backend):
    """Detect GPU info for join handshake."""
    gpus = []
    if backend == "nvidia":
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                text=True,
            )
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    gpus.append({"name": parts[0], "vram_mb": int(parts[1])})
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    elif backend == "amd":
        for card in sorted(os.listdir("/sys/class/drm/")):
            dev = f"/sys/class/drm/{card}/device"
            vendor_f = f"{dev}/vendor"
            if not os.path.isfile(vendor_f):
                continue
            with open(vendor_f) as f:
                if f.read().strip() != "0x1002":
                    continue
            name = "AMD Radeon"
            name_f = f"{dev}/product_name"
            if os.path.isfile(name_f):
                with open(name_f) as f:
                    name = f.read().strip()
            vram = 0
            vram_f = f"{dev}/mem_info_vram_total"
            if os.path.isfile(vram_f):
                with open(vram_f) as f:
                    vram = int(f.read().strip()) // 1048576
            gpus.append({"name": name, "vram_mb": vram})
    if not gpus:
        gpus.append({"name": "CPU only", "vram_mb": 0})
    return gpus


def join_cluster(controller_ip, setup_port, token, gpu_backend, gpus, rpc_port):
    """TCP handshake with the controller's setup listener. Returns True if accepted."""
    try:
        sock = socket.create_connection((controller_ip, setup_port), timeout=30)
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        print(f"[AGENT] Cannot connect to controller {controller_ip}:{setup_port}: {e}")
        return False

    msg = json.dumps({
        "action": "join",
        "token": token,
        "gpu_backend": gpu_backend,
        "gpus": gpus,
        "rpc_port": rpc_port,
    }).encode() + b"\n"
    sock.sendall(msg)

    # Wait for response (operator may take a while to confirm)
    sock.settimeout(300)
    buf = b""
    try:
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    except socket.timeout:
        print("[AGENT] Timed out waiting for controller response")
        sock.close()
        return False

    sock.close()

    if not buf:
        print("[AGENT] No response from controller")
        return False

    resp = json.loads(buf.split(b"\n", 1)[0])
    if resp.get("status") == "accepted":
        print("[AGENT] Controller accepted this worker")
        return True

    print(f"[AGENT] Rejected: {resp.get('reason', 'unknown')}")
    return False


def get_worker_image(gpu_backend):
    """Return the Docker image tag for the rpc-server."""
    if gpu_backend == "amd":
        return "dream-rpc-server:rocm"
    if gpu_backend == "cpu":
        return "dream-rpc-server:cpu"
    return "dream-rpc-server:cuda"


def start_rpc_server(gpu_backend, rpc_port):
    """Start the rpc-server Docker container. Returns True on success."""
    image = get_worker_image(gpu_backend)

    # Check image exists
    if subprocess.run(["docker", "image", "inspect", image],
                      capture_output=True).returncode != 0:
        print(f"[AGENT] Worker image {image} not found")
        return False

    # Stop existing container
    subprocess.run(["docker", "stop", "dream-rpc-server"], capture_output=True)
    subprocess.run(["docker", "rm", "dream-rpc-server"], capture_output=True)

    cmd = [
        "docker", "run", "-d",
        "--name", "dream-rpc-server",
        "--restart", "no",  # agent manages restarts, not Docker
        "-p", f"{rpc_port}:50052",
    ]

    if gpu_backend == "nvidia":
        cmd.extend(["--gpus", "all"])
    elif gpu_backend == "amd":
        video_gid = os.environ.get("VIDEO_GID", "44")
        render_gid = os.environ.get("RENDER_GID", "992")
        cmd.extend([
            "--device", "/dev/dri",
            "--device", "/dev/kfd",
            "--group-add", video_gid,
            "--group-add", render_gid,
        ])

    cmd.extend([image, "-p", "50052", "-c", "--host", "0.0.0.0"])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[AGENT] Failed to start rpc-server: {result.stderr.strip()}")
        return False

    print(f"[AGENT] rpc-server started on port {rpc_port}")
    return True


def is_rpc_server_running():
    """Check if dream-rpc-server container is running."""
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", "dream-rpc-server"],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


class StatusHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for agent status endpoint."""

    agent_state = None  # set by main before server starts

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = json.dumps({
                "status": "ok",
                "agent": self.agent_state.snapshot() if self.agent_state else {},
                "rpc_server_running": is_rpc_server_running(),
            })
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress default stderr logging


def run_status_server(port, state):
    """Run HTTP status server in a daemon thread."""
    StatusHandler.agent_state = state
    server = HTTPServer(("0.0.0.0", port), StatusHandler)
    server.timeout = 1
    while _running:
        server.handle_request()
    server.server_close()


def main():
    parser = argparse.ArgumentParser(description="Dream Cluster Worker Agent")
    parser.add_argument("--config", required=True, help="Path to cluster-agent.json")
    parser.add_argument("--status-port", type=int, default=50054, help="HTTP status port")
    parser.add_argument("--pid-file", help="Write PID to file")
    parser.add_argument("--interface", default="", help="Bind discovery to this network interface IP")
    args = parser.parse_args()

    if args.pid_file:
        Path(args.pid_file).parent.mkdir(parents=True, exist_ok=True)
        Path(args.pid_file).write_text(str(os.getpid()))

    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)

    state = AgentState(args.config)
    print(f"[AGENT] Starting worker agent (status port {args.status_port})")

    # Start HTTP status server
    status_thread = threading.Thread(
        target=run_status_server, args=(args.status_port, state), daemon=True
    )
    status_thread.start()

    token = state.get("token", "")
    if not token:
        print("[AGENT] No token configured. Set token in config or use 'dream cluster agent start --token TOKEN'")
        state.update(status="error")
        return

    gpu_backend = state.get("gpu_backend") or detect_gpu_backend()
    rpc_port = state.get("rpc_port", 50052)
    bind_ip = args.interface or state.get("interface", "") or ""
    state.update(gpu_backend=gpu_backend, rpc_port=rpc_port)

    # Main loop: discover → join → run → monitor
    while _running:
        controller_ip = state.get("controller_ip", "")

        # Phase 1: discover controller if not known
        if not controller_ip:
            state.update(status="discovering")
            print("[AGENT] Searching for controller on LAN...")
            try:
                controller_ip, setup_port = discover_controller(DISCOVERY_TIMEOUT, bind_ip=bind_ip or None)
                state.update(controller_ip=controller_ip, setup_port=setup_port)
                print(f"[AGENT] Found controller at {controller_ip}:{setup_port}")
            except TimeoutError:
                print(f"[AGENT] No controller found. Retrying in {DISCOVERY_RETRY_INTERVAL}s...")
                for _ in range(DISCOVERY_RETRY_INTERVAL):
                    if not _running:
                        return
                    time.sleep(1)
                continue

        # Phase 2: join cluster if not already joined
        if state.get("status") not in ("joined", "running"):
            state.update(status="joining")
            gpus = detect_gpus(gpu_backend)
            setup_port = state.get("setup_port", 50051)

            if join_cluster(controller_ip, setup_port, token, gpu_backend, gpus, rpc_port):
                state.update(status="joined")
            else:
                print(f"[AGENT] Join failed. Retrying in {DISCOVERY_RETRY_INTERVAL}s...")
                # Clear controller_ip to re-discover in case it changed
                state.update(controller_ip="", status="idle")
                for _ in range(DISCOVERY_RETRY_INTERVAL):
                    if not _running:
                        return
                    time.sleep(1)
                continue

        # Phase 3: start rpc-server if not running
        if not is_rpc_server_running():
            print("[AGENT] Starting rpc-server...")
            if start_rpc_server(gpu_backend, rpc_port):
                state.update(status="running")
            else:
                state.update(status="error")
                print(f"[AGENT] Failed to start rpc-server. Retrying in {DISCOVERY_RETRY_INTERVAL}s...")
                for _ in range(DISCOVERY_RETRY_INTERVAL):
                    if not _running:
                        return
                    time.sleep(1)
                continue

        # Phase 4: monitor rpc-server
        state.update(status="running")
        while _running and is_rpc_server_running():
            time.sleep(HEALTH_CHECK_INTERVAL)

        if not _running:
            break

        # rpc-server died — restart it
        print("[AGENT] rpc-server container stopped. Restarting...")
        state.update(status="joined")
        time.sleep(2)

    # Cleanup
    print("[AGENT] Shutting down")
    if args.pid_file and os.path.isfile(args.pid_file):
        os.unlink(args.pid_file)


if __name__ == "__main__":
    main()
