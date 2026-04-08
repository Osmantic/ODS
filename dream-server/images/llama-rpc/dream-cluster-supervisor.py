#!/usr/bin/env python3
"""
Cluster supervisor for llama-server.

Wraps llama-server with pre-flight checks and auto-restart on crash.
llama.cpp RPC has zero fault tolerance — when a worker disconnects,
the controller calls ggml_abort() and terminates. This supervisor
detects the crash, checks which workers are still reachable, waits
for recovery if possible, then restarts llama-server.

Env vars:
  CLUSTER_WORKERS         — comma-separated host:port list (required for cluster mode)
  CLUSTER_RESTART_POLICY  — always | on-worker-recovery | manual (default: always)

If CLUSTER_WORKERS is empty, the supervisor execs llama-server directly
(transparent passthrough for non-cluster setups).
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time

PREFLIGHT_TIMEOUT = 5       # seconds per worker TCP check
POLL_INTERVAL = 2           # health poll interval during recovery wait
RESTART_DELAY = 3           # seconds between crash and restart attempt
MAX_WORKER_WAIT = 30        # seconds to wait for offline worker recovery
EVENTS_FILE = "/tmp/cluster-events/events.json"

# Module-level ref so the SIGTERM handler can forward the signal to the child.
_child_proc = None


def _handle_term(signum, frame):
    """Forward SIGTERM to child llama-server, then exit cleanly.

    Note: don't call _child_proc.wait() here — the main thread may already
    hold Popen._waitpid_lock, which would deadlock. Just send the signal
    and exit; the OS will clean up the child when the process terminates.
    """
    if _child_proc and _child_proc.poll() is None:
        _child_proc.terminate()
    sys.exit(0)


def parse_workers(rpc_flag):
    """Parse CLUSTER_WORKERS env var into list of (host, port) tuples."""
    workers = []
    for addr in rpc_flag.split(","):
        addr = addr.strip()
        if ":" in addr:
            host, port = addr.rsplit(":", 1)
            workers.append((host, int(port)))
    return workers


def check_worker(host, port, timeout=PREFLIGHT_TIMEOUT):
    """TCP connect check. Returns True if reachable."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def preflight(workers):
    """Check all workers are reachable. Returns (ok, failed_list)."""
    failed = []
    for host, port in workers:
        if not check_worker(host, port):
            failed.append(f"{host}:{port}")
    return len(failed) == 0, failed


def log_event(event_type, detail=""):
    """Append event to JSON file for dashboard consumption."""
    os.makedirs(os.path.dirname(EVENTS_FILE), exist_ok=True)
    events = []
    if os.path.exists(EVENTS_FILE):
        with open(EVENTS_FILE) as f:
            events = json.load(f)
    events.append({
        "type": event_type,
        "detail": detail,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    # Keep last 100 events
    events = events[-100:]
    # Atomic write: write to temp file then rename, so readers never see partial JSON
    tmp = EVENTS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(events, f)
    os.replace(tmp, EVENTS_FILE)


def run_server(cmd):
    """Launch llama-server and wait for it to exit. Returns exit code."""
    global _child_proc
    _child_proc = subprocess.Popen(cmd)
    try:
        return _child_proc.wait()
    except KeyboardInterrupt:
        _child_proc.terminate()
        _child_proc.wait()
        return 0
    finally:
        _child_proc = None


def wait_for_workers(workers, policy):
    """
    After a crash, check worker health and optionally wait for recovery.
    Returns True if we should restart, False if we should exit.
    """
    _, failed = preflight(workers)

    if not failed:
        print("[SUPERVISOR] All workers reachable. Transient crash. Restarting...")
        return True

    print(f"[SUPERVISOR] Offline workers: {', '.join(failed)}")

    if policy == "manual":
        log_event("manual_intervention_required", f"Offline: {', '.join(failed)}")
        print("[SUPERVISOR] Restart policy is 'manual'. Exiting.")
        return False

    if policy == "on-worker-recovery":
        print(f"[SUPERVISOR] Waiting up to {MAX_WORKER_WAIT}s for recovery...")
        still_failed = failed
        deadline = time.time() + MAX_WORKER_WAIT
        while time.time() < deadline:
            _, still_failed = preflight(workers)
            if not still_failed:
                print("[SUPERVISOR] All workers recovered.")
                return True
            time.sleep(POLL_INTERVAL)
        log_event("workers_offline", f"Still offline after {MAX_WORKER_WAIT}s: {', '.join(still_failed)}")
        print(f"[SUPERVISOR] Workers still offline after {MAX_WORKER_WAIT}s. Exiting.")
        return False

    # policy == "always" (default)
    print(f"[SUPERVISOR] Waiting up to {MAX_WORKER_WAIT}s for recovery...")
    still_failed = failed
    deadline = time.time() + MAX_WORKER_WAIT
    while time.time() < deadline:
        _, still_failed = preflight(workers)
        if not still_failed:
            print("[SUPERVISOR] All workers recovered.")
            return True
        time.sleep(POLL_INTERVAL)

    log_event("workers_offline", f"Still offline: {', '.join(still_failed)}")
    print(f"[SUPERVISOR] Workers still offline after {MAX_WORKER_WAIT}s. Restarting anyway...")
    return True


def main():
    cluster_workers = os.environ.get("CLUSTER_WORKERS", "")
    if not cluster_workers:
        # Not in cluster mode — transparent passthrough
        os.execvp("llama-server", ["llama-server"] + sys.argv[1:])

    restart_policy = os.environ.get("CLUSTER_RESTART_POLICY", "always")
    workers = parse_workers(cluster_workers)
    cmd = ["llama-server"] + sys.argv[1:]

    print(f"[SUPERVISOR] Cluster mode enabled. Workers: {cluster_workers}")
    print(f"[SUPERVISOR] Restart policy: {restart_policy}")

    while True:
        # Pre-flight: verify all workers reachable before starting
        ok, failed = preflight(workers)
        if not ok:
            log_event("preflight_failed", f"Unreachable: {', '.join(failed)}")
            print(f"[SUPERVISOR] Pre-flight failed. Unreachable workers: {', '.join(failed)}")
            print(f"[SUPERVISOR] Retrying in {RESTART_DELAY}s...")
            time.sleep(RESTART_DELAY)
            continue

        log_event("server_starting", f"Workers: {cluster_workers}")
        print("[SUPERVISOR] Pre-flight passed. Starting llama-server...")

        exit_code = run_server(cmd)

        if exit_code == 0:
            log_event("server_stopped", "Clean shutdown (exit 0)")
            print("[SUPERVISOR] llama-server exited cleanly.")
            break

        log_event("server_crashed", f"Exit code: {exit_code}")
        print(f"[SUPERVISOR] llama-server exited with code {exit_code}")

        if not wait_for_workers(workers, restart_policy):
            break

        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_term)
    main()
