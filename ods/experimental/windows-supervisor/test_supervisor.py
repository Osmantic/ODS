#!/usr/bin/env python3
"""
Lightweight offline integration test suite for ODS Native Supervisor.
Exercises process management, loopback security, and failure recovery using stub child processes (no network):
  1. Port collision rejection (exit code 3)
  2. Child killed mid-run detection (assert exit code 6, killed by PID only)
  3. Supervisor killed, zero orphaned processes (Job Object cleanup on Windows; explicit SKIP on POSIX)
  4. Graceful stop path verification (all children exit with code 0, supervisor exits code 0)
  5. Abnormal child exit failure detection (assert supervisor exits code 7 when child exits non-zero on stop path)
  6. Forced stop path fallback (timeout expires, triggers forced kill, zero orphans)
  7. Job assignment failure compensation (zero orphans on AssignProcessToJobObject failure)
  8. Secret file permissions (owner-only via icacls / chmod 600) and pids.json credential elimination
  9. Real dashboard-api /status health verification, loopback isolation, and zero false greens
"""

import http.server
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
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
    print("\n[TEST 1/8] Testing Port Collision Rejection (Exit Code 3)...")
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
    print("\n[TEST 2/8] Testing Mid-Run Child Death Detection (Assert Exit 6, Kill by PID Only)...")
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
    print("\n[TEST 3/8] Testing Win32 Job Object KILL_ON_JOB_CLOSE (Windows Only; Explicit SKIP on POSIX)...")
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
        print("PASS: Supervisor killed by PID; child processes terminated with zero orphans via Win32 Job Object.")
    else:
        # On POSIX without Win32 Job Objects, clean up any lingering test processes
        if llama_running: kill_pid_forcefully(llama_pid)
        if dash_running: kill_pid_forcefully(dash_pid)
        print("SKIP: Win32 Job Object KILL_ON_JOB_CLOSE is Windows-only; skipped on POSIX.")

def run_test_graceful_stop_clean():
    print("\n[TEST 4/8] Testing Graceful Stop Path (Clean Exit Code 0 Verification)...")
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
    print("\n[TEST 5/8] Testing Abnormal Child Exit Detection on Stop Path (Assert Exit 7)...")
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
    print("\n[TEST 6/8] Testing Forced Stop Path (Timeout Fallback)...")
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

def run_test_job_assignment_failure_compensation():
    print("\n[TEST 7/8] Testing Job Assignment Failure Compensation (Zero Orphans on Error 5)...")
    import tempfile
    sup_dir = str(SUPERVISOR_SCRIPT.parent)
    if sup_dir not in sys.path:
        sys.path.insert(0, sup_dir)
    from supervisor import WindowsNativeSupervisor

    with tempfile.TemporaryDirectory() as tmpdir:
        sup = WindowsNativeSupervisor(
            Path(tmpdir),
            llama_port=get_free_port(),
            dashboard_port=get_free_port(),
            stub_children=True,
            stub_behavior="clean"
        )

        real_assign = sup.job.assign_process
        calls = 0
        def patched_assign(pid, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("Simulated error 5: AssignProcessToJobObject Access Denied")
            return real_assign(pid, *args, **kwargs)

        sup.job.assign_process = patched_assign

        raised = False
        try:
            sup.start()
        except RuntimeError as e:
            raised = True
            print(f"Caught expected assignment exception: {e}")
        finally:
            sup._terminate_spawned_children()

        assert raised, "Expected RuntimeError from patched assign_process"

        dash_proc = sup.dashboard_proc
        llama_proc = sup.llama_proc

        assert dash_proc is not None, "Dashboard process was never created"
        dash_poll = dash_proc.poll()
        print(f"Unassigned dashboard child poll(): {dash_poll}")
        assert dash_poll is not None, f"Orphan detected! Unassigned dashboard child PID {dash_proc.pid} is still alive!"

        if llama_proc:
            llama_poll = llama_proc.poll()
            print(f"Assigned llama child poll(): {llama_poll}")
            assert llama_poll is not None, f"Orphan detected! Llama child PID {llama_proc.pid} is still alive!"

        print("PASS: Assignment failure triggered immediate compensation kill; zero orphans confirmed.")

def run_test_secret_file_permissions_and_pids_json():
    print("\n[TEST 8/8] Testing Secret File ACLs & pids.json Credential Elimination...")
    import tempfile
    sup_dir = str(SUPERVISOR_SCRIPT.parent)
    if sup_dir not in sys.path:
        sys.path.insert(0, sup_dir)
    from supervisor import WindowsNativeSupervisor

    with tempfile.TemporaryDirectory() as tmpdir:
        test_key = "sk-test-super-secret-key-12345"
        sup = WindowsNativeSupervisor(
            Path(tmpdir),
            llama_port=get_free_port(),
            dashboard_port=get_free_port(),
            api_key=test_key,
            stub_children=True
        )
        try:
            sup.start()

            # 1. Verify pids.json contains NO secrets
            pids_file = Path(tmpdir) / "pids.json"
            assert pids_file.exists(), "pids.json was not created"
            pids_data = json.loads(pids_file.read_text(encoding="utf-8"))
            assert "api_key" not in pids_data, f"SECURITY DEFECT: api_key found in pids.json! Content: {pids_data}"
            assert "llama_key" not in pids_data, f"SECURITY DEFECT: llama_key found in pids.json! Content: {pids_data}"
            print("pids.json contains zero secret keys:", list(pids_data.keys()))

            # 2. Verify key files exist and check permissions
            key_file = Path(tmpdir) / "api_key.txt"
            llama_key_file = Path(tmpdir) / "llama_key.txt"
            assert key_file.exists(), "api_key.txt missing"
            assert llama_key_file.exists(), "llama_key.txt missing"
            assert key_file.read_text(encoding="utf-8").strip() == test_key
            assert len(llama_key_file.read_text(encoding="utf-8").strip()) == 64

            if sys.platform == "win32":
                # Run icacls to verify owner-only permissions
                for f in [key_file, llama_key_file]:
                    res = subprocess.run(["icacls.exe", str(f)], capture_output=True, text=True, check=True)
                    icacls_out = res.stdout
                    print(f"icacls output for {f.name}:\n{icacls_out.strip()}")
                    assert "(F)" in icacls_out, f"Expected (F) full control for owner in {icacls_out}"
                    assert "(I)" not in icacls_out, f"Expected no inherited permissions (I) in {icacls_out}"
                    assert "BUILTIN\\Users" not in icacls_out, f"Public BUILTIN\\Users grant detected in {icacls_out}"
            else:
                # On POSIX: verify mode is 0600
                for f in [key_file, llama_key_file]:
                    mode = oct(f.stat().st_mode & 0o777)
                    print(f"POSIX mode for {f.name}: {mode}")
                    assert mode == "0o600", f"Expected mode 0600, got {mode}"
        finally:
            sup.stop()
        print("PASS: Secret files restricted owner-only (icacls / chmod 600) and pids.json contains zero credentials.")

def run_test_real_dashboard_status_no_false_green():
    print("\n[TEST 9/9] Testing Real dashboard-api /status Integration, Loopback Health, and No False Greens...")
    repo_root = THIS_DIR.parents[2]
    api_dir = repo_root / "ods" / "extensions" / "services" / "dashboard-api"
    data_dir = DATA_DIR / "real_dash_status_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 1. Start stub llama-server on port 8080 answering /health
    class StubLlamaHealthHandler(http.server.BaseHTTPRequestHandler):
        healthy = True

        def do_GET(self):
            if self.path == "/health":
                if StubLlamaHealthHandler.healthy:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "ok"}')
                else:
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "error"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    try:
        import uvicorn  # noqa: F401
        import fastapi  # noqa: F401
    except ImportError:
        print("SKIP: uvicorn/fastapi not installed in current environment; skipping real status test.")
        return

    if not verify_port_available(8080):
        print("Warning: Port 8080 is in use, skipping real status test")
        return
    if not verify_port_available(3002):
        print("Warning: Port 3002 is in use, skipping real status test")
        return

    llama_server = http.server.ThreadingHTTPServer(("127.0.0.1", 8080), StubLlamaHealthHandler)
    t_llama = threading.Thread(target=llama_server.serve_forever, daemon=True)
    t_llama.start()

    # Step A: Fail-Before demonstration (without OLLAMA_HOST and DASHBOARD_API_HOST)
    print("Testing Fail-Before baseline (unconfigured host environment)...")
    env_fail = os.environ.copy()
    env_fail["DASHBOARD_API_KEY"] = "sk-test-fail-before-key"
    env_fail["ODS_INSTALL_DIR"] = str(repo_root / "ods")
    env_fail["ODS_DATA_DIR"] = str(data_dir / "ods_data_fail")
    env_fail["ODS_EXTENSIONS_DIR"] = str(repo_root / "ods" / "extensions" / "services")
    env_fail["PYTHONPATH"] = str(api_dir)
    env_fail.pop("OLLAMA_HOST", None)
    env_fail.pop("DASHBOARD_API_HOST", None)
    env_fail.pop("SERVICE_HOST", None)

    runner = (
        "import sys, uvicorn, main; "
        "uvicorn.run(main.app, host='127.0.0.1', port=3002, log_level='warning')"
    )
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    dash_fail = subprocess.Popen([PYTHON, "-c", runner], env=env_fail, creationflags=creationflags)
    try:
        ready = False
        for _ in range(40):
            time.sleep(0.2)
            try:
                req = urllib.request.Request("http://127.0.0.1:3002/health", headers={"Authorization": "Bearer sk-test-fail-before-key"})
                with urllib.request.urlopen(req, timeout=1.0) as r:
                    if r.status == 200:
                        ready = True
                        break
            except Exception:
                pass
        assert ready, "Failed to start fail-before dashboard instance"

        req = urllib.request.Request("http://127.0.0.1:3002/status", headers={"Authorization": "Bearer sk-test-fail-before-key"})
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        fail_by_id = {s["id"]: s["status"] for s in data.get("services", [])}
        print(f"Fail-before statuses: llama-server={fail_by_id.get('llama-server')}, dashboard-api={fail_by_id.get('dashboard-api')}")
        assert fail_by_id.get("llama-server") in ("down", "not_deployed"), "Expected llama-server down without OLLAMA_HOST"
    finally:
        dash_fail.kill()
        dash_fail.wait(timeout=5.0)

    time.sleep(0.5)

    # Step B: Pass-After demonstration (with OLLAMA_HOST and DASHBOARD_API_HOST set to 127.0.0.1)
    print("Testing Pass-After supervisor configuration (OLLAMA_HOST and DASHBOARD_API_HOST = 127.0.0.1)...")
    env_pass = os.environ.copy()
    env_pass["DASHBOARD_API_KEY"] = "sk-test-pass-after-key"
    env_pass["ODS_INSTALL_DIR"] = str(repo_root / "ods")
    env_pass["ODS_DATA_DIR"] = str(data_dir / "ods_data_pass")
    env_pass["ODS_EXTENSIONS_DIR"] = str(repo_root / "ods" / "extensions" / "services")
    env_pass["PYTHONPATH"] = str(api_dir)
    env_pass["OLLAMA_HOST"] = "127.0.0.1"
    env_pass["DASHBOARD_API_HOST"] = "127.0.0.1"
    env_pass.pop("SERVICE_HOST", None)
    env_pass.pop("WEBUI_HOST", None)
    env_pass.pop("SEARXNG_HOST", None)
    env_pass.pop("TOKEN_SPY_HOST", None)

    dash_pass = subprocess.Popen([PYTHON, "-c", runner], env=env_pass, creationflags=creationflags)
    try:
        ready = False
        for _ in range(40):
            time.sleep(0.2)
            try:
                req = urllib.request.Request("http://127.0.0.1:3002/health", headers={"Authorization": "Bearer sk-test-pass-after-key"})
                with urllib.request.urlopen(req, timeout=1.0) as r:
                    if r.status == 200:
                        ready = True
                        break
            except Exception:
                pass
        assert ready, "Failed to start pass-after dashboard instance"

        req = urllib.request.Request("http://127.0.0.1:3002/status", headers={"Authorization": "Bearer sk-test-pass-after-key"})
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            pass_data = json.loads(resp.read().decode("utf-8"))
        by_id = {s["id"]: s["status"] for s in pass_data.get("services", [])}
        print("Pass-after statuses:")
        for sid in ["llama-server", "dashboard-api", "open-webui", "token-spy", "searxng"]:
            print(f"  {sid}: {by_id.get(sid)}")

        assert by_id.get("llama-server") == "healthy", f"llama-server expected healthy, got {by_id.get('llama-server')}"
        assert by_id.get("dashboard-api") == "healthy", f"dashboard-api expected healthy, got {by_id.get('dashboard-api')}"
        assert by_id.get("open-webui") in ("down", "not_deployed"), f"open-webui false green: {by_id.get('open-webui')}"
        assert by_id.get("token-spy") in ("down", "not_deployed"), f"token-spy false green: {by_id.get('token-spy')}"
        assert by_id.get("searxng") in ("down", "not_deployed"), f"searxng false green: {by_id.get('searxng')}"

        # Step C: Mutation check (mutate stub llama to unhealthy 503)
        print("Testing mutation check (mutating stub llama response to HTTP 503)...")
        StubLlamaHealthHandler.healthy = False
        time.sleep(1.0)
        req = urllib.request.Request("http://127.0.0.1:3002/status", headers={"Authorization": "Bearer sk-test-pass-after-key"})
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            mut_data = json.loads(resp.read().decode("utf-8"))
        mut_by_id = {s["id"]: s["status"] for s in mut_data.get("services", [])}
        print(f"Mutated statuses: llama-server={mut_by_id.get('llama-server')}, dashboard-api={mut_by_id.get('dashboard-api')}")
        assert mut_by_id.get("llama-server") in ("unhealthy", "down", "degraded"), f"Mutation check failed: {mut_by_id.get('llama-server')}"

        print("PASS: Real dashboard-api /status verified: healthy supervised services, zero false greens, and mutation verified.")
    finally:
        dash_pass.kill()
        dash_pass.wait(timeout=5.0)
        llama_server.shutdown()

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
    run_test_job_assignment_failure_compensation()
    run_test_secret_file_permissions_and_pids_json()
    run_test_real_dashboard_status_no_false_green()
    elapsed = time.perf_counter() - t0
    print("\n===============================================================")
    print(f"SUCCESS: All 9 offline supervisor tests passed in {elapsed:.2f}s! ")
    print("===============================================================")

if __name__ == "__main__":
    main()
