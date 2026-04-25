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

HEALTH_CHECK_INTERVAL = 10     # seconds between rpc-server health checks
DISCOVERY_TIMEOUT = 30         # seconds to wait for beacon per attempt
DISCOVERY_RETRY_INTERVAL = 15  # seconds between discovery attempts
HANDSHAKE_MAX_BYTES = 1_048_576  # bound controller response to 1 MiB (H3 mirror)
HANDSHAKE_RECV_POLL = 2.0      # granularity of handshake recv — lets _handle_term cancel it
HANDSHAKE_TOTAL_TIMEOUT = 300  # total seconds to wait for operator confirmation
# Caps for external CLIs (docker, nvidia-smi). dockerd has well-known hang
# modes (sick overlay2 filesystem, dead containerd socket) and without a
# timeout here the agent blocks forever — SIGTERM can't interrupt a blocking
# subprocess.run, so the systemd stop path also hangs.
DOCKER_CLI_TIMEOUT = 30        # seconds for any docker subcommand
GPU_PROBE_TIMEOUT = 10         # seconds for nvidia-smi probes
VALID_GPU_BACKENDS = frozenset({"cpu", "nvidia", "amd"})

# Crash-loop circuit breaker for the rpc-server container. Mirrors the
# supervisor's CRASH_WINDOW_SECONDS / MAX_CRASHES_PER_WINDOW pair so a
# deterministic failure (missing image, bad GPU device, OOM on startup)
# exits non-zero and lets systemd's StartLimitBurst throttle the service
# instead of the agent spinning docker run forever.
RPC_CRASH_WINDOW_SECONDS = 120
MAX_RPC_CRASHES_PER_WINDOW = 5

# Termination flag. threading.Event (not a plain bool) so the monitor loop's
# `_stop.wait(HEALTH_CHECK_INTERVAL)` returns immediately on SIGTERM instead
# of waiting up to HEALTH_CHECK_INTERVAL seconds — without this, systemd's
# stop sequence could escalate to SIGKILL while leaving the rpc-server
# container orphaned.
_stop = threading.Event()


def _is_running():
    return not _stop.is_set()


def _handle_term(signum, frame):
    _stop.set()


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
        # Create with mode 0o600 before write. The state file holds the
        # cluster token and must not be world-readable. os.open(O_CREAT|O_EXCL)
        # applies the mode atomically; os.replace then swaps it into place,
        # preserving the restricted perms (unlike a plain open() which uses
        # the process umask, typically 0o022 → mode 0o644).
        if os.path.exists(tmp):
            os.unlink(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._state, f, indent=2)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        os.replace(tmp, self._path)

    def snapshot(self):
        with self._lock:
            return dict(self._state)


def detect_gpu_backend():
    """Detect GPU backend from environment or hardware."""
    backend = os.environ.get("GPU_BACKEND", "")
    if backend:
        return backend
    # nvidia-smi present → nvidia. Bounded timeout so a wedged GPU driver
    # doesn't block the agent forever on startup.
    try:
        rc = subprocess.run(
            ["nvidia-smi"], capture_output=True, timeout=GPU_PROBE_TIMEOUT
        ).returncode
        if rc == 0:
            return "nvidia"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
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
                timeout=GPU_PROBE_TIMEOUT,
            )
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    gpus.append({"name": parts[0], "vram_mb": int(parts[1])})
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired):
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
    """TCP handshake with the controller's setup listener. Returns True if accepted.

    Handshake recv is polled in short chunks (HANDSHAKE_RECV_POLL) rather
    than one big blocking call — so SIGTERM (which sets _stop) can
    cancel the wait without waiting the full HANDSHAKE_TOTAL_TIMEOUT.
    Controller response is capped at HANDSHAKE_MAX_BYTES to prevent a
    hostile controller from OOMing the worker.
    """
    if gpu_backend not in VALID_GPU_BACKENDS:
        print(f"[AGENT] Refusing to join: unsupported gpu_backend {gpu_backend!r}")
        return False
    if not (isinstance(rpc_port, int) and 1 <= rpc_port <= 65535):
        print(f"[AGENT] Refusing to join: rpc_port {rpc_port!r} out of range 1-65535")
        return False

    try:
        sock = socket.create_connection((controller_ip, setup_port), timeout=30)
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        print(f"[AGENT] Cannot connect to controller {controller_ip}:{setup_port}: {e}")
        return False

    try:
        msg = json.dumps({
            "action": "join",
            "token": token,
            "gpu_backend": gpu_backend,
            "gpus": gpus,
            "rpc_port": rpc_port,
        }).encode() + b"\n"
        sock.sendall(msg)

        sock.settimeout(HANDSHAKE_RECV_POLL)
        buf = b""
        deadline = time.monotonic() + HANDSHAKE_TOTAL_TIMEOUT
        while b"\n" not in buf:
            if _stop.is_set():
                print("[AGENT] Shutdown requested — aborting handshake")
                return False
            if time.monotonic() >= deadline:
                print("[AGENT] Timed out waiting for controller response")
                return False
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue  # poll _stop again
            if not chunk:
                break
            buf += chunk
            if len(buf) > HANDSHAKE_MAX_BYTES:
                print(f"[AGENT] Controller response exceeded {HANDSHAKE_MAX_BYTES} bytes — aborting")
                return False
    finally:
        sock.close()

    if not buf:
        print("[AGENT] No response from controller")
        return False

    try:
        resp = json.loads(buf.split(b"\n", 1)[0])
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"[AGENT] Malformed response from controller: {e}")
        return False
    if not isinstance(resp, dict):
        print(f"[AGENT] Unexpected response shape from controller: {type(resp).__name__}")
        return False

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


def _docker_run(argv, timeout=DOCKER_CLI_TIMEOUT):
    """subprocess.run wrapper that always passes a timeout.

    A wedged dockerd blocks every CLI call indefinitely; without the timeout
    the agent hangs and SIGTERM can't wake it. On timeout we return a
    synthetic CompletedProcess with returncode 124 (conventional timeout
    exit) so callers can treat it like any other failure.
    """
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        stderr = f"docker command timed out after {timeout}s: {' '.join(argv)}"
        return subprocess.CompletedProcess(
            argv, returncode=124, stdout=(e.stdout or b"").decode(errors="replace"),
            stderr=stderr,
        )


def start_rpc_server(gpu_backend, rpc_port, bind_ip=""):
    """Start the rpc-server Docker container. Returns True on success.

    bind_ip narrows the host-side publish to a specific interface IP. When
    set, the rpc-server listens only on that interface (e.g. the operator's
    LAN address) instead of every network the host is attached to —
    important on multi-homed hosts (Tailscale, virbr0, Docker bridges)
    because llama.cpp RPC has no auth or encryption.
    """
    image = get_worker_image(gpu_backend)

    # Check image exists
    if _docker_run(["docker", "image", "inspect", image]).returncode != 0:
        print(f"[AGENT] Worker image {image} not found")
        return False

    # Stop existing container
    _docker_run(["docker", "stop", "dream-rpc-server"])
    _docker_run(["docker", "rm", "dream-rpc-server"])

    publish = f"{bind_ip}:{rpc_port}:50052" if bind_ip else f"{rpc_port}:50052"
    cmd = [
        "docker", "run", "-d",
        "--name", "dream-rpc-server",
        "--restart", "no",  # agent manages restarts, not Docker
        "-p", publish,
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

    result = _docker_run(cmd)
    if result.returncode != 0:
        print(f"[AGENT] Failed to start rpc-server: {result.stderr.strip()}")
        return False

    print(f"[AGENT] rpc-server started on port {rpc_port}")
    return True


def is_rpc_server_running():
    """Check if dream-rpc-server container is running."""
    result = _docker_run(
        ["docker", "inspect", "-f", "{{.State.Running}}", "dream-rpc-server"]
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
            # Strip secrets before returning. The default bind is 127.0.0.1
            # — only `dream cluster agent status` (running on the same host)
            # consumes this; the dashboard's poll_cluster_health probes the
            # rpc port (50052) directly, not /health. To expose /health to
            # another host (so the controller's `dream cluster add <ip>` can
            # auto-fill GPU info), pass --status-bind <ip>, --interface <ip>,
            # or CLUSTER_INTERFACE=<ip> at agent start. Even with the token
            # stripped here, GPU model + topology fingerprint should not
            # leave the host by default.
            agent = {}
            if self.agent_state:
                snap = self.agent_state.snapshot()
                agent = {k: v for k, v in snap.items() if k != "token"}
            # Use the cached rpc_running flag (refreshed by the main loop on
            # every health-check tick) so a wedged dockerd can't head-of-line
            # block this single-threaded HTTPServer for DOCKER_CLI_TIMEOUT
            # seconds per request — `docker inspect` is shelled out elsewhere.
            rpc_running = bool(agent.pop("rpc_running", False))
            body = json.dumps({
                "status": "ok",
                "agent": agent,
                "rpc_server_running": rpc_running,
            })
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress default stderr logging


def run_status_server(bind_ip, port, state):
    """Run HTTP status server until stop_status_server() is called.

    Uses serve_forever + a shutdown thread so an in-flight GET finishes
    cleanly on SIGTERM (M2). Prior handle_request() poll-loop truncated
    live requests at shutdown.

    bind_ip:
      - set to args.interface or CLUSTER_INTERFACE → bind to that IP only,
        so the diagnostic endpoint isn't broadcast on every network the
        host is attached to (H9).
      - empty → bind 127.0.0.1. /health exposes the GPU-fingerprint and
        cluster topology, so the default keeps it host-local. Operators who
        need cross-host /health (e.g. `dream cluster add <ip>` from another
        machine) opt in explicitly via --status-bind / --interface.
    """
    StatusHandler.agent_state = state
    server = HTTPServer((bind_ip or "127.0.0.1", port), StatusHandler)
    shutdown_thread = threading.Thread(
        target=_wait_then_shutdown, args=(server,), daemon=True
    )
    shutdown_thread.start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


def _wait_then_shutdown(server):
    """Block until _stop is set, then shutdown the HTTP server."""
    _stop.wait()
    server.shutdown()


def _resolve_token(args, state):
    """Resolve the cluster token from --token-file, CLUSTER_TOKEN env, or state.

    Priority: --token-file > CLUSTER_TOKEN env > state["token"] (from config).
    --token-file and CLUSTER_TOKEN are preferred because they avoid leaking
    the token to other users via `ps aux` / `/proc/<pid>/cmdline` and via
    the JSON state file's default world-readable mode respectively.
    """
    if args.token_file:
        tf = Path(args.token_file)
        if not tf.is_file():
            print(f"[AGENT] --token-file {args.token_file!r} not found", file=sys.stderr)
            return ""
        # Operators are pointed at --token-file precisely because it doesn't
        # leak via `ps`; warn loudly if it's group/world-readable so they
        # know the file itself is the new leak channel.
        try:
            mode = tf.stat().st_mode & 0o777
        except OSError as e:
            # is_file() returned True a moment ago, so a stat() failure
            # here is unusual — surface it instead of silently treating
            # the file as 0o000. The read_text() below will likely also
            # fail and propagate, which is the right outcome.
            print(f"[AGENT] warning: cannot stat --token-file {args.token_file!r}: {e}",
                  file=sys.stderr)
            mode = 0
        if mode & 0o077:
            print(
                f"[AGENT] warning: --token-file {args.token_file!r} has mode "
                f"{mode:04o} (group/world readable); chmod 600 to restrict.",
                file=sys.stderr,
            )
        token = tf.read_text().strip()
        if token:
            return token
        print(f"[AGENT] --token-file {args.token_file!r} is empty", file=sys.stderr)
        return ""
    env_token = os.environ.get("CLUSTER_TOKEN", "").strip()
    if env_token:
        return env_token
    return state.get("token", "") or ""


def main():
    parser = argparse.ArgumentParser(description="Dream Cluster Worker Agent")
    parser.add_argument("--config", required=True, help="Path to cluster-agent.json")
    parser.add_argument("--status-port", type=int, default=50054, help="HTTP status port")
    parser.add_argument(
        "--status-bind", default="",
        help="Bind IP for the HTTP status server (default: --interface value, else CLUSTER_INTERFACE, else 0.0.0.0)",
    )
    parser.add_argument("--pid-file", help="Write PID to file")
    parser.add_argument("--interface", default="", help="Bind discovery/status to this network interface IP")
    parser.add_argument("--controller", default="", help="Controller IP (skip UDP discovery)")
    parser.add_argument(
        "--token-file",
        help="Path to a mode-0600 file containing the cluster token (preferred over --token / CLUSTER_TOKEN env / config)",
    )
    args = parser.parse_args()

    if args.pid_file:
        Path(args.pid_file).parent.mkdir(parents=True, exist_ok=True)
        Path(args.pid_file).write_text(str(os.getpid()))

    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)

    state = AgentState(args.config)
    print(f"[AGENT] Starting worker agent (status port {args.status_port})")

    bind_ip = (
        args.interface
        or os.environ.get("CLUSTER_INTERFACE", "").strip()
        or state.get("interface", "")
        or ""
    )
    status_bind = args.status_bind or bind_ip

    # Start HTTP status server
    status_thread = threading.Thread(
        target=run_status_server, args=(status_bind, args.status_port, state), daemon=True
    )
    status_thread.start()

    token = _resolve_token(args, state)
    if not token:
        print(
            "[AGENT] No token configured. Provide one via --token-file, "
            "CLUSTER_TOKEN env var, or the config file.",
            file=sys.stderr,
        )
        state.update(status="error")
        return

    gpu_backend = state.get("gpu_backend") or detect_gpu_backend()
    rpc_port = state.get("rpc_port", 50052)

    # If controller IP given via CLI, store it and skip discovery
    if args.controller:
        state.update(controller_ip=args.controller)

    state.update(gpu_backend=gpu_backend, rpc_port=rpc_port)

    # Circuit breaker state. We count both failed startups (start_rpc_server
    # returning False) and observed crashes (Docker container exiting while
    # we were supposed to be monitoring it). Both indicate an unhealthy
    # environment that retrying won't fix.
    crash_times = []

    def record_rpc_crash(reason):
        now = time.monotonic()
        crash_times.append(now)
        crash_times[:] = [t for t in crash_times if now - t <= RPC_CRASH_WINDOW_SECONDS]
        if len(crash_times) >= MAX_RPC_CRASHES_PER_WINDOW:
            msg = (
                f"rpc-server crashed {len(crash_times)} times in "
                f"{RPC_CRASH_WINDOW_SECONDS}s (last: {reason}) — stopping. "
                f"Check image, GPU devices, and worker logs."
            )
            print(f"[AGENT] {msg}", file=sys.stderr)
            state.update(status="error", error=msg)
            sys.exit(1)

    # Main loop: discover → join → run → monitor
    while not _stop.is_set():
        controller_ip = state.get("controller_ip", "")

        # Phase 1: discover controller if not known
        if not controller_ip:
            state.update(status="discovering", error="")
            print("[AGENT] Searching for controller on LAN...")
            try:
                # Require a signed beacon so a LAN attacker can't redirect
                # us at a fake setup listener (H1). We already have the
                # shared cluster token at this point — discover_controller
                # rejects any beacon whose HMAC doesn't verify.
                controller_ip, setup_port = discover_controller(
                    DISCOVERY_TIMEOUT,
                    bind_ip=bind_ip or None,
                    expected_token=token,
                )
                state.update(controller_ip=controller_ip, setup_port=setup_port)
                print(f"[AGENT] Found controller at {controller_ip}:{setup_port}")
            except TimeoutError:
                print(f"[AGENT] No controller found. Retrying in {DISCOVERY_RETRY_INTERVAL}s...")
                if _stop.wait(DISCOVERY_RETRY_INTERVAL):
                    break
                continue

        # Phase 2: join cluster if not already joined
        if state.get("status") not in ("joined", "running"):
            state.update(status="joining")
            gpus = detect_gpus(gpu_backend)
            setup_port = state.get("setup_port", 50051)
            # Persist detected GPUs so the /health endpoint can surface them
            # to operators running `dream cluster add <ip>` (M11).
            state.update(gpus=gpus)

            if join_cluster(controller_ip, setup_port, token, gpu_backend, gpus, rpc_port):
                # Clear any prior error message — operators reading /health
                # otherwise see status=joined alongside a stale failure
                # string from a previous discovery/join cycle.
                state.update(status="joined", error="")
            else:
                print(f"[AGENT] Join failed. Retrying in {DISCOVERY_RETRY_INTERVAL}s...")
                # Clear controller_ip to re-discover in case it changed
                state.update(controller_ip="", status="idle")
                if _stop.wait(DISCOVERY_RETRY_INTERVAL):
                    break
                continue

        # Phase 3: start rpc-server if not running
        rpc_running = is_rpc_server_running()
        state.update(rpc_running=rpc_running)
        if not rpc_running:
            print("[AGENT] Starting rpc-server...")
            if start_rpc_server(gpu_backend, rpc_port, bind_ip=bind_ip):
                state.update(status="running", rpc_running=True, error="")
            else:
                state.update(status="error", rpc_running=False)
                record_rpc_crash("start_rpc_server returned False")
                print(f"[AGENT] Failed to start rpc-server. Retrying in {DISCOVERY_RETRY_INTERVAL}s...")
                if _stop.wait(DISCOVERY_RETRY_INTERVAL):
                    break
                continue

        # Phase 4: monitor rpc-server. _stop.wait() returns True if SIGTERM
        # arrived during the wait, so the agent exits within
        # HEALTH_CHECK_INTERVAL granularity instead of waiting for a plain
        # time.sleep to elapse.
        state.update(status="running", error="")
        while not _stop.is_set():
            if _stop.wait(HEALTH_CHECK_INTERVAL):
                break
            rpc_running = is_rpc_server_running()
            state.update(rpc_running=rpc_running)
            if not rpc_running:
                break

        if _stop.is_set():
            break

        # rpc-server died — restart it. Count this against the circuit
        # breaker; a container that exits shortly after start is almost
        # always deterministic (bad image, missing device, OOM).
        print("[AGENT] rpc-server container stopped. Restarting...")
        state.update(status="joined", rpc_running=False)
        record_rpc_crash("rpc-server container exited")
        if _stop.wait(2):
            break

    # Cleanup. Stop the rpc-server container so the next agent invocation
    # gets a clean slate — leaving it running across an agent restart can
    # leave a stale model loaded that the controller still believes it's
    # talking to. Best-effort: if dockerd is wedged we still want to exit.
    print("[AGENT] Shutting down")
    stop_result = _docker_run(["docker", "stop", "dream-rpc-server"])
    if stop_result.returncode == 0:
        _docker_run(["docker", "rm", "dream-rpc-server"])
    elif stop_result.returncode == 124:
        print(f"[AGENT] docker stop timed out — container may persist", file=sys.stderr)
    state.update(rpc_running=False)
    if args.pid_file and os.path.isfile(args.pid_file):
        os.unlink(args.pid_file)


if __name__ == "__main__":
    main()
