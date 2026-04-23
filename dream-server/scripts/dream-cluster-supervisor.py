#!/usr/bin/env python3
"""
Cluster supervisor for llama-server.

Wraps llama-server with pre-flight checks, auto-restart on crash, and a
watchdog that monitors worker reachability. Key behaviors:

  - On crash: checks which workers are alive, restarts with only live
    workers (strips dead ones from --rpc to avoid 20-75s connect timeout).
  - Degraded mode: if some workers are down but remaining devices can hold
    the model, inference continues on fewer nodes.
  - Recovery: watchdog detects when a dead worker comes back online and
    triggers a restart to re-add it (full --rpc restored).
  - Hung detection: if a worker is unreachable AND llama-server stops
    responding to /health, the watchdog kills the hung process so the
    restart loop can proceed.

llama.cpp RPC has zero fault tolerance — when a worker disconnects, the
controller calls ggml_abort() and terminates with exit 134 (SIGABRT).
Network partitions cause indefinite hangs (no SO_KEEPALIVE or timeouts).

Env vars:
  CLUSTER_WORKERS         — comma-separated host:port list (required)
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
import threading
import time

PREFLIGHT_TIMEOUT = 5       # seconds per worker TCP check
POLL_INTERVAL = 2           # health poll interval during recovery wait
RESTART_DELAY = 3           # seconds between crash and restart attempt
MAX_WORKER_WAIT = 15        # seconds to wait for offline workers before degraded restart
WATCHDOG_INTERVAL = 5       # seconds between watchdog TCP pings
WATCHDOG_TCP_TIMEOUT = 3    # seconds per watchdog TCP check
HEALTH_CHECK_TIMEOUT = 5    # seconds for llama-server /health check
LLAMA_HEALTH_URL = "http://127.0.0.1:8080/health"
EVENTS_FILE = "/tmp/cluster-events/events.json"

# Circuit breaker: if llama-server crashes more than MAX_CRASHES_PER_WINDOW
# times in CRASH_WINDOW_SECONDS, stop restarting and exit non-zero so systemd's
# StartLimitBurst can stop thrashing. Only counts real crashes, not
# watchdog-initiated restarts (which are expected during worker recovery).
CRASH_WINDOW_SECONDS = 120
MAX_CRASHES_PER_WINDOW = 5

# Module-level ref so the SIGTERM handler and watchdog can reach the child.
_child_proc = None
_child_lock = threading.Lock()
_term_requested = threading.Event()

# Serializes log_event's read-modify-write on EVENTS_FILE. Without this the
# watchdog thread and main thread race — both read the same list, both append,
# and whichever os.replace lands second overwrites the earlier event.
_events_lock = threading.Lock()


def _handle_term(signum, frame):
    """Flag termination and nudge the child so the main loop can shut down.

    Async-signal-safe: sets a threading.Event (atomic), captures the child
    ref without taking a lock (the lock is only held for ~1ms around
    Popen()/cleanup, so re-entering it from the signal context is
    technically a deadlock risk), and calls .terminate() which is just
    os.kill(SIGTERM) under the hood. Actual shutdown happens on the main
    thread in main() after run_server() returns.
    """
    _term_requested.set()
    proc = _child_proc  # local snapshot; avoids reacquiring _child_lock
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except (OSError, ProcessLookupError):
            pass


def _interruptible_sleep(seconds):
    """Sleep up to `seconds`, but return early if SIGTERM flagged."""
    _term_requested.wait(timeout=seconds)


def parse_workers(rpc_flag):
    """Parse comma-separated host:port list into list of (host, port) tuples.

    Raises ValueError with the offending entry if any address is malformed
    (missing host, missing/non-numeric port, port outside 1-65535). Empty
    segments from trailing/duplicate commas are tolerated.
    """
    workers = []
    for raw in rpc_flag.split(","):
        addr = raw.strip()
        if not addr:
            continue
        if ":" not in addr:
            raise ValueError(
                f"Malformed CLUSTER_WORKERS entry {addr!r}: expected host:port"
            )
        host, port_s = addr.rsplit(":", 1)
        if not host:
            raise ValueError(
                f"Malformed CLUSTER_WORKERS entry {addr!r}: empty host"
            )
        try:
            port = int(port_s)
        except ValueError as e:
            raise ValueError(
                f"Malformed CLUSTER_WORKERS entry {addr!r}: port {port_s!r} is not an integer"
            ) from e
        if not (1 <= port <= 65535):
            raise ValueError(
                f"Malformed CLUSTER_WORKERS entry {addr!r}: port {port} out of range 1-65535"
            )
        workers.append((host, port))
    return workers


def workers_to_rpc(workers):
    """Convert list of (host, port) tuples back to --rpc flag value."""
    return ",".join(f"{h}:{p}" for h, p in workers)


def check_worker(host, port, timeout=PREFLIGHT_TIMEOUT):
    """TCP connect check. Returns True if reachable."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def partition_workers(workers):
    """Split workers into (live, dead) lists based on TCP reachability."""
    live, dead = [], []
    for host, port in workers:
        if check_worker(host, port):
            live.append((host, port))
        else:
            dead.append((host, port))
    return live, dead


def log_event(event_type, detail=""):
    """Append event to JSON file for dashboard consumption.

    The watchdog thread and the main thread both call this. os.replace is
    atomic but the read → append → write sequence isn't — without the lock,
    two concurrent writers read the same events list, each appends one
    entry, and the second os.replace clobbers the first one's event.

    Writes go through O_EXCL|0o600 so the events log (which contains cluster
    topology and timing) is not world-readable on shared hosts.
    """
    with _events_lock:
        events_dir = os.path.dirname(EVENTS_FILE)
        os.makedirs(events_dir, mode=0o700, exist_ok=True)
        # Tighten mode if the directory already existed with a looser perm.
        try:
            os.chmod(events_dir, 0o700)
        except OSError:
            pass
        events = []
        if os.path.exists(EVENTS_FILE):
            with open(EVENTS_FILE) as f:
                events = json.load(f)
        events.append({
            "type": event_type,
            "detail": detail,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        events = events[-100:]
        tmp = EVENTS_FILE + ".tmp"
        if os.path.exists(tmp):
            os.unlink(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(events, f)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        os.replace(tmp, EVENTS_FILE)


def run_server(cmd):
    """Launch llama-server and wait for it to exit. Returns exit code."""
    global _child_proc
    with _child_lock:
        _child_proc = subprocess.Popen(cmd)
    try:
        return _child_proc.wait()
    except KeyboardInterrupt:
        _child_proc.terminate()
        _child_proc.wait()
        return 0
    finally:
        with _child_lock:
            _child_proc = None


def check_llama_health():
    """HTTP health check on llama-server. Returns True if responding.

    Only the expected network/HTTP-level failures are swallowed. Anything
    else (programming error, unexpected exception) is allowed to propagate
    so the supervisor crashes visibly rather than silently reporting
    "unhealthy" forever.
    """
    import http.client
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.urlopen(LLAMA_HEALTH_URL, timeout=HEALTH_CHECK_TIMEOUT)
        return req.status == 200
    except (urllib.error.URLError, http.client.HTTPException, socket.timeout, ConnectionError, OSError):
        return False


def build_cmd(base_args, active_workers):
    """Build llama-server command, replacing --rpc with active workers only."""
    cmd = ["llama-server"]
    skip_next = False
    for i, arg in enumerate(base_args):
        if skip_next:
            skip_next = False
            continue
        if arg == "--rpc":
            cmd.append("--rpc")
            cmd.append(workers_to_rpc(active_workers))
            skip_next = True  # skip original --rpc value
        else:
            cmd.append(arg)
    return cmd


def watchdog(all_workers, active_workers, stop_event, restart_reason):
    """Background thread: monitors worker health while llama-server runs.

    Two jobs:
    1. If an active worker goes down AND llama-server is hung (not
       responding to /health), kill it so supervisor can restart with
       remaining workers. Never kills a responsive llama-server.
    2. If a previously-dead worker comes back online, signal the main
       loop to restart with the full worker set.
    """
    worker_was_offline = set()
    dead_workers = set(all_workers) - set(active_workers)

    while not stop_event.is_set():
        stop_event.wait(WATCHDOG_INTERVAL)
        if stop_event.is_set():
            break

        # Check active workers
        for host, port in active_workers:
            key = f"{host}:{port}"
            if not check_worker(host, port, timeout=WATCHDOG_TCP_TIMEOUT):
                if key not in worker_was_offline:
                    print(f"[WATCHDOG] Active worker {key} unreachable")
                    log_event("worker_unreachable", f"Worker {key} unreachable")
                    worker_was_offline.add(key)
                # Worker down — check if llama-server is hung
                if not check_llama_health():
                    print(f"[WATCHDOG] llama-server not responding (worker {key} down) — restarting")
                    log_event("watchdog_restart", f"llama-server hung, worker {key} unreachable")
                    restart_reason.append("hung")
                    with _child_lock:
                        if _child_proc and _child_proc.poll() is None:
                            _child_proc.kill()
                    return
            else:
                if key in worker_was_offline:
                    print(f"[WATCHDOG] Worker {key} back online")
                    log_event("worker_recovered", f"Worker {key} back online")
                    worker_was_offline.discard(key)

        # Check dead workers — did any come back?
        if dead_workers:
            recovered = {
                (host, port) for host, port in dead_workers
                if check_worker(host, port, timeout=WATCHDOG_TCP_TIMEOUT)
            }
            if recovered:
                recovered_str = ", ".join(f"{h}:{p}" for h, p in recovered)
                print(f"[WATCHDOG] Dead workers recovered: {recovered_str} — restarting to re-add")
                log_event("workers_recovered", f"Recovered: {recovered_str}")
                restart_reason.append("recovery")
                with _child_lock:
                    if _child_proc and _child_proc.poll() is None:
                        _child_proc.terminate()
                return


def main():
    cluster_workers = os.environ.get("CLUSTER_WORKERS", "")
    if not cluster_workers:
        os.execvp("llama-server", ["llama-server"] + sys.argv[1:])

    restart_policy = os.environ.get("CLUSTER_RESTART_POLICY", "always")
    all_workers = parse_workers(cluster_workers)
    base_args = sys.argv[1:]

    print(f"[SUPERVISOR] Cluster mode enabled. Workers: {cluster_workers}")
    print(f"[SUPERVISOR] Restart policy: {restart_policy}")
    print(f"[SUPERVISOR] Watchdog: ping every {WATCHDOG_INTERVAL}s, degraded mode enabled")

    degraded = False  # True when running with fewer workers than configured

    # Circuit breaker: track recent crash timestamps (monotonic seconds).
    # Only real crashes (exit_code != 0 with no watchdog restart_reason) are
    # counted — watchdog-initiated restarts for worker recovery or hang
    # detection are expected and don't signal a broken config.
    crash_times = []

    while not _term_requested.is_set():
        # Check which workers are alive
        live, dead = partition_workers(all_workers)

        if dead:
            dead_str = ", ".join(f"{h}:{p}" for h, p in dead)

            if not live:
                # No workers at all — wait and retry
                log_event("all_workers_offline", f"All offline: {dead_str}")
                print(f"[SUPERVISOR] All workers offline: {dead_str}")

                if restart_policy == "manual":
                    print("[SUPERVISOR] Restart policy is 'manual'. Exiting.")
                    break

                print(f"[SUPERVISOR] Waiting up to {MAX_WORKER_WAIT}s for any worker...")
                deadline = time.time() + MAX_WORKER_WAIT
                while time.time() < deadline and not _term_requested.is_set():
                    live, dead = partition_workers(all_workers)
                    if live:
                        break
                    _interruptible_sleep(POLL_INTERVAL)

                if _term_requested.is_set():
                    break

                if not live:
                    if restart_policy == "on-worker-recovery":
                        print("[SUPERVISOR] No workers recovered. Exiting.")
                        break
                    # "always" — start with just controller GPU (no --rpc workers)
                    print("[SUPERVISOR] No workers recovered. Starting with controller GPU only...")
                    live = []

            if dead:
                dead_str = ", ".join(f"{h}:{p}" for h, p in dead)
                if live:
                    live_str = ", ".join(f"{h}:{p}" for h, p in live)
                    print(f"[SUPERVISOR] Degraded mode: using {live_str} (offline: {dead_str})")
                    log_event("degraded_start", f"Live: {live_str}, offline: {dead_str}")
                    degraded = True
                else:
                    print(f"[SUPERVISOR] Controller-only mode (all workers offline: {dead_str})")
                    log_event("controller_only_start", f"All workers offline: {dead_str}")
                    degraded = True
        else:
            if degraded:
                print("[SUPERVISOR] All workers back — full cluster restored")
                log_event("full_cluster_restored", f"Workers: {cluster_workers}")
                degraded = False

        # Build command with only live workers
        if live:
            cmd = build_cmd(base_args, live)
            active_str = workers_to_rpc(live)
        else:
            # No live workers — remove --rpc entirely, run on controller GPU only
            cmd = ["llama-server"]
            skip_next = False
            for arg in base_args:
                if skip_next:
                    skip_next = False
                    continue
                if arg == "--rpc":
                    skip_next = True
                    continue
                cmd.append(arg)
            active_str = "(controller only)"

        log_event("server_starting", f"Active workers: {active_str}")
        print(f"[SUPERVISOR] Starting llama-server with: {active_str}")

        # Start watchdog
        stop_watchdog = threading.Event()
        restart_reason = []  # watchdog writes reason here: "hung" or "recovery"
        wd_thread = threading.Thread(
            target=watchdog,
            args=(all_workers, live, stop_watchdog, restart_reason),
            daemon=True,
        )
        wd_thread.start()

        exit_code = run_server(cmd)

        stop_watchdog.set()
        wd_thread.join(timeout=5)

        if _term_requested.is_set():
            log_event("server_stopped", "SIGTERM received")
            print("[SUPERVISOR] SIGTERM received — shutting down.")
            break

        if exit_code == 0 and not restart_reason:
            log_event("server_stopped", "Clean shutdown (exit 0)")
            print("[SUPERVISOR] llama-server exited cleanly.")
            break

        # Log what happened
        if restart_reason and restart_reason[0] == "recovery":
            detail = "Restarting to re-add recovered workers"
        elif restart_reason and restart_reason[0] == "hung":
            detail = "Killed hung llama-server (network partition)"
        else:
            detail = f"Exit code: {exit_code}"
            if exit_code == 134:
                detail += " (SIGABRT — RPC worker disconnected)"

        log_event("server_crashed", detail)
        print(f"[SUPERVISOR] llama-server exited: {detail}")

        if restart_policy == "manual" and not restart_reason:
            log_event("manual_intervention_required", detail)
            print("[SUPERVISOR] Restart policy is 'manual'. Exiting.")
            break

        # Circuit breaker: a deterministic failure (bad model path, malformed
        # --rpc, mismatched llama.cpp builds) will crash on every restart. If
        # we've crashed MAX_CRASHES_PER_WINDOW times in CRASH_WINDOW_SECONDS,
        # stop restarting and exit non-zero so systemd's StartLimitBurst can
        # stop thrashing the service. Watchdog-initiated restarts are not
        # counted (they represent expected operational events).
        if not restart_reason:
            now = time.monotonic()
            crash_times.append(now)
            crash_times = [t for t in crash_times if now - t <= CRASH_WINDOW_SECONDS]
            if len(crash_times) >= MAX_CRASHES_PER_WINDOW:
                log_event(
                    "manual_intervention_required",
                    f"{len(crash_times)} crashes in {CRASH_WINDOW_SECONDS}s "
                    f"(last: {detail}). Exiting.",
                )
                print(
                    f"[SUPERVISOR] {len(crash_times)} crashes in "
                    f"{CRASH_WINDOW_SECONDS}s — stopping restart loop. "
                    f"Check llama-server config (model path, --rpc, image version).",
                    file=sys.stderr,
                )
                sys.exit(1)

        _interruptible_sleep(RESTART_DELAY)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)
    main()
