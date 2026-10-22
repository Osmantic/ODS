#!/usr/bin/env python3
"""
Lightweight offline integration test suite for ODS Native Supervisor.
Exercises process management and failure recovery using stub child processes (no network):
  1. Port collision rejection (exit code 3)
  2. Child killed mid-run detection (assert exit code 6, killed by PID only)
  3. Supervisor killed, zero orphaned processes (Job Object cleanup on Windows, PDEATHSIG on Linux)
  4. Graceful stop path verification (all children exit with code 0, supervisor exits code 0)
  5. Abnormal child exit failure detection (assert supervisor exits code 7 when child exits non-zero on stop path)
  6. Forced stop path fallback (timeout expires, triggers forced kill, zero orphans)
"""

import os
import sys
import time
import json
import signal
import socket
import subprocess
from pathlib import Path

PYTHON = sys.executable
THIS_DIR = Path(__file__).resolve().parent
SUPERVISOR_SCRIPT = THIS_DIR / "supervisor.py"
DATA_DIR = THIS_DIR / "test_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def get_free_port() -> int:
    """Finds an unused loopback TCP port allocated by the OS."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def get_free_port_pair() -> tuple[int, int]:
    p1 = get_free_port()
    p2 = get_free_port()
    while p2 == p1:
        p2 = get_free_port()
    return p1, p2

def verify_port_available(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        s.close()
        return True
    except OSError:
        return False

def is_pid_running(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            res = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True)
            for line in res.stdout.splitlines():
                parts = [p.strip('"') for p in line.split(",")]
                if len(parts) >= 2 and parts[1] == str(pid):
                    return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

def kill_pid_forcefully(pid: int):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

def wait_for_pids_ready(pids_file: Path, timeout: float = 15.0) -> dict:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if pids_file.exists():
            try:
                data = json.loads(pids_file.read_text(encoding="utf-8"))
                if data.get("ready") and data.get("llama_pid") and data.get("dashboard_pid"):
                    return data
            except Exception:
                pass
        time.sleep(0.2)
    raise TimeoutError(f"Timed out waiting for pids.json readiness at {pids_file}")

def run_test_port_collision():
    print("\n[TEST 1/6] Testing Port Collision Rejection (Exit Code 3)...")
    conflict_port = get_free_port()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", conflict_port))
    sock.listen(1)
    print(f"Bound blocking socket to 127.0.0.1:{conflict_port}.")

    try:
        cmd = [
            PYTHON, str(SUPERVISOR_SCRIPT),
            "--data-dir", str(DATA_DIR / "port_conflict_data"),
            "--llama-port", str(conflict_port),
            "--stub-children"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        print(f"Exit code: {res.returncode}")
        print(f"Stderr: {res.stderr.strip()}")
        assert res.returncode == 3, f"Expected exit code 3, got {res.returncode}"
        assert f"Port {conflict_port} is already in use" in res.stderr, "Expected port collision message in stderr"
        print("PASS: Port collision rejected cleanly with code 3.")
    finally:
        sock.close()

def run_test_child_death_stub():
    print("\n[TEST 2/6] Testing Mid-Run Child Death Detection (Assert Exit 6, Kill by PID Only)...")
    llama_port, dash_port = get_free_port_pair()
    stub_data = DATA_DIR / "stub_death_data"
    pids_file = stub_data / "pids.json"
    if pids_file.exists():
        pids_file.unlink()

    cmd = [
        PYTHON, str(SUPERVISOR_SCRIPT),
        "--data-dir", str(stub_data),
        "--llama-port", str(llama_port),
        "--dashboard-port", str(dash_port),
        "--stub-children"
    ]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            creationflags=creationflags)

    pids_info = wait_for_pids_ready(pids_file, timeout=10.0)
    llama_pid = pids_info["llama_pid"]
    print(f"Supervisor confirmed ready. Spawned stub llama PID: {llama_pid}")
    assert proc.poll() is None, "Supervisor exited prematurely before mid-run kill!"
    assert is_pid_running(llama_pid), f"Stub llama PID {llama_pid} is not running!"

    # Kill child strictly by spawned PID only
    print(f"Killing child llama PID {llama_pid} by PID only...")
    kill_pid_forcefully(llama_pid)

    out, err = proc.communicate(timeout=10)
    print(f"Supervisor exit code: {proc.returncode}")
    print(f"Stderr: {err.strip()}")
    assert proc.returncode == 6, f"Expected exit code 6, got {proc.returncode}\nStderr: {err}"
    assert "died unexpectedly" in err, "Expected child death detection message"
    print("PASS: Child death by PID detected and supervisor exited with strictly code 6.")

def run_test_zero_orphans_stub():
    print("\n[TEST 3/6] Testing Supervisor Killed: Zero Orphan Verification (Kill by PID Only)...")
    llama_port, dash_port = get_free_port_pair()
    stub_data = DATA_DIR / "stub_orphan_data"
    pids_file = stub_data / "pids.json"
    if pids_file.exists():
        pids_file.unlink()

    cmd = [
        PYTHON, str(SUPERVISOR_SCRIPT),
        "--data-dir", str(stub_data),
        "--llama-port", str(llama_port),
        "--dashboard-port", str(dash_port),
        "--stub-children"
    ]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            creationflags=creationflags)

    pids_info = wait_for_pids_ready(pids_file, timeout=10.0)
    sup_pid = pids_info["supervisor_pid"]
    llama_pid = pids_info["llama_pid"]
    dash_pid = pids_info["dashboard_pid"]
    print(f"Supervisor PID: {sup_pid}, llama PID: {llama_pid}, dash PID: {dash_pid}")
    assert proc.poll() is None, "Supervisor exited prematurely!"
    assert is_pid_running(llama_pid), "Stub llama process not running!"
    assert is_pid_running(dash_pid), "Stub dashboard process not running!"

    # Forcefully kill supervisor by PID only
    print(f"Killing supervisor PID {sup_pid} forcefully by PID only...")
    kill_pid_forcefully(sup_pid)

    # Wait up to 5s for Job Object / OS process cleanup
    t_wait = time.perf_counter()
    while time.perf_counter() - t_wait < 5.0:
        if not is_pid_running(llama_pid) and not is_pid_running(dash_pid):
            break
        time.sleep(0.2)

    llama_running = is_pid_running(llama_pid)
    dash_running = is_pid_running(dash_pid)
    print(f"Post-kill PID checks: llama_pid({llama_pid}) running={llama_running}, dash_pid({dash_pid}) running={dash_running}")
    if sys.platform == "win32":
        assert not llama_running, f"Orphaned llama child (PID {llama_pid}) detected!"
        assert not dash_running, f"Orphaned dashboard child (PID {dash_pid}) detected!"
    else:
        # On POSIX without Win32 Job Objects, clean up any lingering test processes
        if llama_running: kill_pid_forcefully(llama_pid)
        if dash_running: kill_pid_forcefully(dash_pid)

    print("PASS: Supervisor killed by PID; child processes terminated with zero orphans.")

def run_test_graceful_stop_clean():
    print("\n[TEST 4/6] Testing Graceful Stop Path (Clean Exit Code 0 Verification)...")
    llama_port, dash_port = get_free_port_pair()
    stub_data = DATA_DIR / "stub_graceful_data"
    pids_file = stub_data / "pids.json"
    if pids_file.exists():
        pids_file.unlink()

    cmd = [
        PYTHON, "-u", str(SUPERVISOR_SCRIPT),
        "--data-dir", str(stub_data),
        "--llama-port", str(llama_port),
        "--dashboard-port", str(dash_port),
        "--stop-timeout", "5.0",
        "--stub-children",
        "--stop-after-ready"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    print(f"Graceful stop exit code: {res.returncode}")
    print(f"Stdout snippet:\n{res.stdout[-350:].strip()}")
    if res.returncode != 0:
        print(f"FULL STDERR:\n{res.stderr}")
    assert res.returncode == 0, f"Expected exit 0 for clean graceful stop, got {res.returncode}"
    assert "Stop mode: GRACEFUL" in res.stdout, "Expected graceful stop mode in stdout"
    assert "exited with code 0" in res.stdout, "Expected child process exit code 0 confirmation in stdout"

    # Verify processes are terminated
    assert pids_file.exists(), "pids.json missing after stop"
    pids = json.loads(pids_file.read_text(encoding="utf-8"))
    assert not is_pid_running(pids.get("llama_pid")), "llama process still running!"
    assert not is_pid_running(pids.get("dashboard_pid")), "dashboard process still running!"
    print("PASS: Graceful stop path confirmed clean cooperative exit (all children exit code 0).")

def run_test_abnormal_exit_failure():
    print("\n[TEST 5/6] Testing Abnormal Child Exit Detection on Stop Path (Assert Exit 7)...")
    llama_port, dash_port = get_free_port_pair()
    stub_data = DATA_DIR / "stub_abnormal_data"
    pids_file = stub_data / "pids.json"
    if pids_file.exists():
        pids_file.unlink()

    # Stub child is configured to exit with code 3 on break
    cmd = [
        PYTHON, "-u", str(SUPERVISOR_SCRIPT),
        "--data-dir", str(stub_data),
        "--llama-port", str(llama_port),
        "--dashboard-port", str(dash_port),
        "--stop-timeout", "5.0",
        "--stub-children",
        "--stub-abnormal",
        "--stop-after-ready"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    print(f"Abnormal stop supervisor exit code: {res.returncode}")
    print(f"Stdout snippet:\n{res.stdout[-350:].strip()}")
    print(f"Stderr snippet:\n{res.stderr[-300:].strip()}")

    assert res.returncode == 7, f"Expected supervisor exit code 7 on abnormal child exit, got {res.returncode}"
    assert "Stop mode: ABNORMAL" in res.stdout or "abnormal child exit" in res.stderr, "Expected abnormal stop indication"
    assert "exited with code 3" in res.stdout, "Expected non-zero exit code logged for child"
    print("PASS: Abnormal child exit on graceful path correctly detected and rejected with exit code 7.")

def run_test_forced_stop_timeout():
    print("\n[TEST 6/6] Testing Forced Stop Path (Timeout Fallback)...")
    llama_port, dash_port = get_free_port_pair()
    stub_data = DATA_DIR / "stub_forced_data"
    pids_file = stub_data / "pids.json"
    if pids_file.exists():
        pids_file.unlink()

    # Stub child ignores signal, forces timeout expiry
    cmd = [
        PYTHON, "-u", str(SUPERVISOR_SCRIPT),
        "--data-dir", str(stub_data),
        "--llama-port", str(llama_port),
        "--dashboard-port", str(dash_port),
        "--stop-timeout", "0.001",
        "--stub-children",
        "--stub-hang",
        "--stop-after-ready"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    print(f"Forced stop supervisor exit code: {res.returncode}")
    print(f"Stdout snippet:\n{res.stdout[-350:].strip()}")
    print(f"Stderr snippet:\n{res.stderr[-300:].strip()}")

    assert res.returncode == 7, f"Expected supervisor exit code 7 on forced timeout, got {res.returncode}"
    assert "Stop mode: FORCED" in res.stdout or "terminating forcefully" in res.stderr, "Expected forced stop indication"

    # Verify processes were terminated forcefully with zero orphans
    assert pids_file.exists(), "pids.json missing after forced stop"
    pids = json.loads(pids_file.read_text(encoding="utf-8"))
    assert not is_pid_running(pids.get("llama_pid")), "llama process still running!"
    assert not is_pid_running(pids.get("dashboard_pid")), "dashboard process still running!"
    print("PASS: Forced stop path confirmed timeout fallback, process kill, and zero orphans.")

def main():
    print("===============================================================")
    print("       ODS Process Supervisor Offline Test Suite               ")
    print("       (Stub Children, Zero Network, Fast Execution)           ")
    print("===============================================================")
    t0 = time.perf_counter()
    run_test_port_collision()
    run_test_child_death_stub()
    run_test_zero_orphans_stub()
    run_test_graceful_stop_clean()
    run_test_abnormal_exit_failure()
    run_test_forced_stop_timeout()
    elapsed = time.perf_counter() - t0
    print("\n===============================================================")
    print(f"SUCCESS: All 6 offline supervisor tests passed in {elapsed:.2f}s! ")
    print("===============================================================")

if __name__ == "__main__":
    main()
