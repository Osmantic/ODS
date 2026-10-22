#!/usr/bin/env python3
"""
Experimental Opt-In Windows Native Supervisor for ODS
Supervises llama-server.exe and dashboard-api inside a single Win32 Job Object
with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, ensuring zero orphaned processes.
"""

import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# --- Pinned Dependency Specifications ---
LLAMA_SERVER_PIN = {
    "release_tag": "b9014",
    "commit": "d4b0c22f9",
    "asset": "llama-b9014-bin-win-cpu-x64.zip",
    "url": "https://github.com/ggml-org/llama.cpp/releases/download/b9014/llama-b9014-bin-win-cpu-x64.zip",
    "sha256": "3503cc30154266ac0fbedd38e39e35a313a13412415738ae8f9e23e4fcb44da2",
    "size_bytes": 15917797,
    "exe_name": "llama-server.exe",
    "exe_sha256": "fe91d802f890c05b97b79fabdf7002abc8be4872f239ef21ff55d7d6c3d8c591"
}

MODEL_PIN = {
    "repo": "unsloth/SmolLM2-135M-Instruct-GGUF",
    "commit": "9e6855bc4be717fca1ef21360a1db4b29d5c559a",
    "filename": "SmolLM2-135M-Instruct-Q4_K_M.gguf",
    "url": "https://huggingface.co/unsloth/SmolLM2-135M-Instruct-GGUF/resolve/9e6855bc4be717fca1ef21360a1db4b29d5c559a/SmolLM2-135M-Instruct-Q4_K_M.gguf",
    "sha256": "ed5fa30c487b282ec156c29062f1222e5c20875a944ac98289dbd242e947f747",
    "size_bytes": 105454144
}

# Win32 Constants
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9
PROCESS_ALL_ACCESS = 0x1F0FFF

def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()

def verify_port_available(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        s.close()
        return True
    except OSError:
        return False

def write_private_secret_file(path: Path, content: str) -> None:
    """Writes a secret file with owner-only access permissions (DACL / chmod 0600).
    Follows ODS Windows private-file contract (icacls /inheritance:r /grant:r %USERNAME%:F)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if sys.platform == "win32":
        import getpass
        username = os.environ.get("USERNAME") or getpass.getuser()
        try:
            subprocess.run(
                ["icacls.exe", str(path), "/inheritance:r", "/grant:r", f"{username}:(F)"],
                capture_output=True, text=True, check=True
            )
        except Exception as e:
            print(f"Warning: Failed to set owner-only ACL on {path}: {e}", file=sys.stderr)
    else:
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass

class Win32JobManager:
    """Manages an NT Job Object ensuring all assigned processes terminate when closed."""
    def __init__(self):
        self.handle = None
        self.is_windows = sys.platform == "win32"
        if self.is_windows:
            self._init_job_object()

    def _init_job_object(self):
        import ctypes
        from ctypes import wintypes

        CreateJobObjectW = ctypes.windll.kernel32.CreateJobObjectW
        CreateJobObjectW.restype = wintypes.HANDLE
        CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)

        SetInformationJobObject = ctypes.windll.kernel32.SetInformationJobObject
        SetInformationJobObject.restype = wintypes.BOOL

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_uint64),
                        ("WriteOperationCount", ctypes.c_uint64),
                        ("OtherOperationCount", ctypes.c_uint64),
                        ("ReadTransferCount", ctypes.c_uint64),
                        ("WriteTransferCount", ctypes.c_uint64),
                        ("OtherTransferCount", ctypes.c_uint64)]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                        ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.handle = CreateJobObjectW(None, None)
        if not self.handle:
            raise RuntimeError(f"CreateJobObjectW failed with error {ctypes.GetLastError()}")

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = SetInformationJobObject(self.handle, JobObjectExtendedLimitInformation,
                                     ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            raise RuntimeError(f"SetInformationJobObject failed with error {ctypes.GetLastError()}")

    def assign_process(self, pid: int):
        if not self.is_windows or not self.handle:
            return
        import ctypes
        h_proc = ctypes.windll.kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not h_proc:
            raise RuntimeError(f"OpenProcess for PID {pid} failed with error {ctypes.GetLastError()}")
        try:
            ok = ctypes.windll.kernel32.AssignProcessToJobObject(self.handle, h_proc)
            if not ok:
                raise RuntimeError(f"AssignProcessToJobObject failed for PID {pid} with error {ctypes.GetLastError()}")
        finally:
            ctypes.windll.kernel32.CloseHandle(h_proc)

    def close(self):
        if self.handle:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None

class WindowsNativeSupervisor:
    """Supervises llama-server and dashboard-api native execution."""
    def __init__(self, data_dir: Path, llama_port: int = 8080, dashboard_port: int = 3002,
                 api_key: str = None, stop_timeout: float = 5.0,
                 llama_url: str = None, model_url: str = None,
                 stub_children: bool = False, stub_behavior: str = "clean"):
        self.data_dir = data_dir.resolve()
        self.llama_port = llama_port
        self.dashboard_port = dashboard_port
        self.api_key = api_key or secrets.token_hex(16)
        self.llama_key = secrets.token_hex(32)
        self.stop_timeout = stop_timeout
        self.llama_url = llama_url or LLAMA_SERVER_PIN["url"]
        self.model_url = model_url or MODEL_PIN["url"]
        self.stub_children = stub_children
        self.stub_behavior = stub_behavior
        self.job = Win32JobManager()
        self.llama_proc = None
        self.dashboard_proc = None
        self.running = False

        self.runtime_dir = self.data_dir / "runtime"
        self.models_dir = self.data_dir / "models"
        self.logs_dir = self.data_dir / "logs"
        self.pids_file = self.data_dir / "pids.json"
        self.key_file = self.data_dir / "api_key.txt"
        self.llama_key_file = self.data_dir / "llama_key.txt"

        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.llama_log_path = self.logs_dir / "llama_server.log"
        self.dashboard_log_path = self.logs_dir / "dashboard_api.log"
        self.llama_log_file = None
        self.dashboard_log_file = None

        # Write API keys to disk with owner-only ACLs (zero credentials in pids.json)
        write_private_secret_file(self.key_file, self.api_key)
        write_private_secret_file(self.llama_key_file, self.llama_key)

    def _write_pids(self, ready: bool = False):
        pids_data = {
            "supervisor_pid": os.getpid(),
            "llama_pid": self.llama_proc.pid if self.llama_proc else None,
            "dashboard_pid": self.dashboard_proc.pid if self.dashboard_proc else None,
            "llama_port": self.llama_port,
            "dashboard_port": self.dashboard_port,
            "ready": ready
        }
        self.pids_file.write_text(json.dumps(pids_data, indent=2), encoding="utf-8")

    def _spawn_and_assign_child(self, name: str, cmd: list, **kwargs) -> subprocess.Popen:
        """Spawns child process and assigns it to Job Object.
        If assignment fails for any reason, immediately terminates child
        and raises RuntimeError to guarantee zero orphaned processes."""
        creationflags = kwargs.pop("creationflags", 0)
        if sys.platform == "win32":
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(cmd, creationflags=creationflags, **kwargs)
        if "llama" in name:
            self.llama_proc = proc
        elif "dashboard" in name:
            self.dashboard_proc = proc
        try:
            self.job.assign_process(proc.pid)
        except Exception as e:
            print(f"ERROR: Failed to assign {name} (PID {proc.pid}) to Win32 Job Object: {e}. Terminating child immediately to guarantee zero orphans.", file=sys.stderr)
            try:
                proc.kill()
                proc.wait(timeout=2.0)
            except Exception:
                pass
            self._terminate_spawned_children()
            raise
        return proc

    def _terminate_spawned_children(self):
        for proc in [self.llama_proc, self.dashboard_proc]:
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
        self.job.close()

    def _tail_log(self, path: Path, max_lines: int = 25) -> str:
        if not path.exists():
            return ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                return "".join(lines[-max_lines:])
        except Exception:
            return ""

    def _download(self, url: str, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        part_path = dest.with_suffix(dest.suffix + ".part")
        if part_path.exists():
            try:
                part_path.unlink()
            except OSError:
                pass
        req = urllib.request.Request(url, headers={"User-Agent": "ODS-Supervisor/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(part_path, "wb") as f:
            while chunk := resp.read(1024 * 1024):
                f.write(chunk)
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        part_path.replace(dest)

    def download_and_verify(self, force: bool = False):
        # 1. Download and verify llama-server
        zip_path = self.data_dir / LLAMA_SERVER_PIN["asset"]
        if force or not zip_path.exists():
            print(f"Downloading {self.llama_url}...")
            self._download(self.llama_url, zip_path)
            actual_zip_sha = compute_sha256(zip_path)
            if actual_zip_sha != LLAMA_SERVER_PIN["sha256"]:
                print(f"ERROR: SHA-256 mismatch for runtime archive! Expected {LLAMA_SERVER_PIN['sha256']}, got {actual_zip_sha}", file=sys.stderr)
                sys.exit(2)
        else:
            actual_zip_sha = compute_sha256(zip_path)
            if actual_zip_sha != LLAMA_SERVER_PIN["sha256"]:
                print(f"Notice: Cached runtime archive {zip_path.name} SHA-256 mismatch ({actual_zip_sha[:12]}... != {LLAMA_SERVER_PIN['sha256'][:12]}...); re-downloading...", file=sys.stderr)
                self._download(self.llama_url, zip_path)
                actual_zip_sha = compute_sha256(zip_path)
                if actual_zip_sha != LLAMA_SERVER_PIN["sha256"]:
                    print(f"ERROR: SHA-256 mismatch for runtime archive after re-download! Expected {LLAMA_SERVER_PIN['sha256']}, got {actual_zip_sha}", file=sys.stderr)
                    sys.exit(2)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(self.runtime_dir)

        llama_exe = self.runtime_dir / LLAMA_SERVER_PIN["exe_name"]
        if not llama_exe.exists():
            matches = list(self.runtime_dir.glob(f"**/{LLAMA_SERVER_PIN['exe_name']}"))
            if not matches:
                print(f"ERROR: {LLAMA_SERVER_PIN['exe_name']} missing from extracted archive!", file=sys.stderr)
                sys.exit(2)
            llama_exe = matches[0]

        actual_exe_sha = compute_sha256(llama_exe)
        if actual_exe_sha != LLAMA_SERVER_PIN["exe_sha256"]:
            print(f"ERROR: SHA-256 mismatch for llama-server.exe! Expected {LLAMA_SERVER_PIN['exe_sha256']}, got {actual_exe_sha}", file=sys.stderr)
            sys.exit(2)

        # 2. Download and verify model GGUF
        model_path = self.models_dir / MODEL_PIN["filename"]
        if force or not model_path.exists():
            print(f"Downloading {self.model_url}...")
            self._download(self.model_url, model_path)
            actual_model_sha = compute_sha256(model_path)
            if actual_model_sha != MODEL_PIN["sha256"]:
                print(f"ERROR: SHA-256 mismatch for GGUF model! Expected {MODEL_PIN['sha256']}, got {actual_model_sha}", file=sys.stderr)
                sys.exit(2)
        else:
            actual_model_sha = compute_sha256(model_path)
            if actual_model_sha != MODEL_PIN["sha256"]:
                print(f"Notice: Cached model {model_path.name} SHA-256 mismatch ({actual_model_sha[:12]}... != {MODEL_PIN['sha256'][:12]}...); re-downloading...", file=sys.stderr)
                self._download(self.model_url, model_path)
                actual_model_sha = compute_sha256(model_path)
                if actual_model_sha != MODEL_PIN["sha256"]:
                    print(f"ERROR: SHA-256 mismatch for GGUF model after re-download! Expected {MODEL_PIN['sha256']}, got {actual_model_sha}", file=sys.stderr)
                    sys.exit(2)

        print("SUCCESS: Runtime and Model verified with matching cryptographic hashes.")
        return llama_exe, model_path

    def start(self, llama_exe: Path = None, model_path: Path = None):
        # 1. Pre-flight port checks
        if not verify_port_available(self.llama_port):
            print(f"ERROR: Port {self.llama_port} is already in use by another process.", file=sys.stderr)
            sys.exit(3)
        if not verify_port_available(self.dashboard_port):
            print(f"ERROR: Port {self.dashboard_port} is already in use by another process.", file=sys.stderr)
            sys.exit(3)

        # Open log files for child redirection
        self.llama_log_file = open(self.llama_log_path, "a", encoding="utf-8")
        self.dashboard_log_file = open(self.dashboard_log_path, "a", encoding="utf-8")

        if self.stub_children:
            stub_clean = (
                "import time, sys, signal\n"
                "def _exit(s, f): sys.exit(0)\n"
                "signal.signal(signal.SIGINT, _exit)\n"
                "if hasattr(signal, 'SIGBREAK'):\n"
                "    signal.signal(signal.SIGBREAK, _exit)\n"
                "while True:\n"
                "    try:\n"
                "        time.sleep(1)\n"
                "    except BaseException:\n"
                "        sys.exit(0)\n"
            )
            stub_abnormal = (
                "import time, sys, signal\n"
                "def _exit(s, f): sys.exit(3)\n"
                "signal.signal(signal.SIGINT, _exit)\n"
                "if hasattr(signal, 'SIGBREAK'):\n"
                "    signal.signal(signal.SIGBREAK, _exit)\n"
                "while True:\n"
                "    try:\n"
                "        time.sleep(1)\n"
                "    except BaseException:\n"
                "        sys.exit(3)\n"
            )
            stub_hang = (
                "import time, signal\n"
                "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
                "if hasattr(signal, 'SIGBREAK'):\n"
                "    signal.signal(signal.SIGBREAK, signal.SIG_IGN)\n"
                "while True:\n"
                "    try:\n"
                "        time.sleep(1)\n"
                "    except BaseException:\n"
                "        pass\n"
            )

            dash_stub = stub_clean
            if self.stub_behavior == "abnormal":
                dash_stub = stub_abnormal
            elif self.stub_behavior == "hang":
                dash_stub = stub_hang

            llama_cmd = [sys.executable, "-c", stub_clean]
            api_cmd = [sys.executable, "-c", dash_stub]

            print("Starting stub llama child (clean)...")
            self.llama_proc = self._spawn_and_assign_child(
                "stub-llama", llama_cmd, stdout=self.llama_log_file, stderr=subprocess.STDOUT
            )

            print(f"Starting stub dashboard child ({self.stub_behavior})...")
            self.dashboard_proc = self._spawn_and_assign_child(
                f"stub-dashboard ({self.stub_behavior})", api_cmd, stdout=self.dashboard_log_file, stderr=subprocess.STDOUT
            )

            # Give stub children a moment to initialize signal handlers
            time.sleep(0.3)

            self._write_pids(ready=True)
            self.running = True
            print("SUCCESS: Both stub child processes spawned and marked ready.")
            return

        # 2. Launch llama-server.exe
        llama_cmd = [
            str(llama_exe),
            "--model", str(model_path),
            "--alias", model_path.name,
            "--host", "127.0.0.1",
            "--port", str(self.llama_port),
            "--ctx-size", "2048",
            "--parallel", "1",
            "--threads", "4",
            "--no-webui",
            "--api-key-file", str(self.llama_key_file)
        ]
        llama_env = os.environ.copy()
        llama_env["LLAMA_API_KEY"] = self.llama_key
        print(f"Starting llama-server.exe on port {self.llama_port} (logs: {self.llama_log_path.name})...")
        self.llama_proc = self._spawn_and_assign_child(
            "llama-server", llama_cmd, stdout=self.llama_log_file, stderr=subprocess.STDOUT, env=llama_env
        )
        print(f"Assigned llama-server (PID {self.llama_proc.pid}) to Win32 Job Object.")

        # 3. Launch dashboard-api via Uvicorn with clean SIGBREAK handler pre-registered
        repo_root = Path(__file__).resolve().parents[3]
        api_dir = repo_root / "ods" / "extensions" / "services" / "dashboard-api"
        env = os.environ.copy()
        env["DASHBOARD_API_KEY"] = self.api_key
        env["ODS_INSTALL_DIR"] = str(repo_root / "ods")
        env["ODS_DATA_DIR"] = str(self.data_dir / "ods_data")
        env["ODS_EXTENSIONS_DIR"] = str(repo_root / "ods" / "extensions" / "services")
        env["PYTHONPATH"] = str(api_dir)
        env["SERVICE_HOST"] = "127.0.0.1"
        env["LLM_API_URL"] = f"http://127.0.0.1:{self.llama_port}/v1"
        env["LLM_MODEL"] = model_path.name
        env["GGUF_FILE"] = model_path.name
        env["AMD_INFERENCE_LOCATION"] = ""
        env["OPEN_WEBUI_LLM_BASE_URL"] = f"http://127.0.0.1:{self.llama_port}/v1"
        env["OPEN_WEBUI_LLM_API_KEY"] = self.llama_key
        env["LLAMA_SERVER_API_KEY"] = self.llama_key
        env["LLAMA_API_KEY"] = self.llama_key
        (self.data_dir / "ods_data").mkdir(parents=True, exist_ok=True)

        runner_code = (
            "import sys, signal, uvicorn; "
            "signal.signal(getattr(signal, 'SIGBREAK', signal.SIGINT), lambda s, f: sys.exit(0)); "
            "import main; "
            "uvicorn.run(main.app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')"
        )
        api_cmd = [
            sys.executable, "-c", runner_code, str(self.dashboard_port)
        ]
        print(f"Starting dashboard-api on port {self.dashboard_port} (logs: {self.dashboard_log_path.name})...")
        self.dashboard_proc = self._spawn_and_assign_child(
            "dashboard-api", api_cmd, env=env, stdout=self.dashboard_log_file, stderr=subprocess.STDOUT
        )
        print(f"Assigned dashboard-api (PID {self.dashboard_proc.pid}) to Win32 Job Object.")

        # Write initial PIDs
        self._write_pids(ready=False)

        # 4. Wait for readiness
        self._wait_for_health()
        self.running = True

    def _wait_for_health(self, timeout: float = 30.0):
        t0 = time.perf_counter()
        llama_url = f"http://127.0.0.1:{self.llama_port}/health"
        dash_url = f"http://127.0.0.1:{self.dashboard_port}/health"
        llama_headers = {"Authorization": f"Bearer {self.llama_key}"}
        dash_headers = {"Authorization": f"Bearer {self.api_key}"}

        llama_ready = False
        dash_ready = False

        while time.perf_counter() - t0 < timeout:
            # Check for premature child death
            if self.llama_proc.poll() is not None:
                log_tail = self._tail_log(self.llama_log_path)
                print(f"ERROR: Child process llama-server.exe exited prematurely with exit code {self.llama_proc.returncode}!\nLogs:\n{log_tail}", file=sys.stderr)
                self.stop()
                sys.exit(5)
            if self.dashboard_proc.poll() is not None:
                log_tail = self._tail_log(self.dashboard_log_path)
                print(f"ERROR: Child process dashboard-api exited prematurely with exit code {self.dashboard_proc.returncode}!\nLogs:\n{log_tail}", file=sys.stderr)
                self.stop()
                sys.exit(5)

            if not llama_ready:
                try:
                    req = urllib.request.Request(llama_url, headers=llama_headers)
                    with urllib.request.urlopen(req, timeout=1.0) as r:
                        if r.status == 200:
                            llama_ready = True
                            print(f"llama-server.exe is ready ({time.perf_counter() - t0:.2f}s).")
                except Exception:
                    pass

            if not dash_ready:
                try:
                    req = urllib.request.Request(dash_url, headers=dash_headers)
                    with urllib.request.urlopen(req, timeout=1.0) as r:
                        if r.status == 200:
                            dash_ready = True
                            print(f"dashboard-api is ready ({time.perf_counter() - t0:.2f}s).")
                except Exception:
                    pass

            if llama_ready and dash_ready:
                print(f"SUCCESS: Both processes healthy and responding on loopback in {time.perf_counter() - t0:.2f}s.")
                self._write_pids(ready=True)
                return

            time.sleep(0.4)

        print(f"ERROR: Timed out waiting for child health after {timeout}s!", file=sys.stderr)
        self.stop()
        sys.exit(5)

    def monitor_until_interrupted(self):
        """Monitors child processes. Exits code 6 if either dies."""
        try:
            while self.running:
                if self.llama_proc and self.llama_proc.poll() is not None:
                    code = self.llama_proc.returncode
                    print(f"ERROR: Child process llama-server.exe (PID {self.llama_proc.pid}) died unexpectedly with exit code {code}.", file=sys.stderr)
                    self.stop()
                    sys.exit(6)
                if self.dashboard_proc and self.dashboard_proc.poll() is not None:
                    code = self.dashboard_proc.returncode
                    print(f"ERROR: Child process dashboard-api (PID {self.dashboard_proc.pid}) died unexpectedly with exit code {code}.", file=sys.stderr)
                    self.stop()
                    sys.exit(6)
                time.sleep(0.4)
        except KeyboardInterrupt:
            print("\nReceived interrupt. Stopping gracefully...")
            self.stop()
            sys.exit(0)

    def stop(self, timeout: float = None) -> bool:
        if timeout is None:
            timeout = self.stop_timeout
        self.running = False
        print("Stopping supervised processes gracefully...")

        # Step 1: Send cooperative break/interrupt signal
        for name, proc in [("dashboard-api", self.dashboard_proc), ("llama-server", self.llama_proc)]:
            if proc and proc.poll() is None:
                try:
                    if sys.platform == "win32":
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        proc.send_signal(signal.SIGINT)
                except Exception as e:
                    print(f"Notice: signal to {name} failed: {e}", file=sys.stderr)

        # Step 2: Await cooperative exit up to timeout
        t0 = time.perf_counter()
        forced_termination = False
        abnormal_exit = False
        for name, proc in [("dashboard-api", self.dashboard_proc), ("llama-server", self.llama_proc)]:
            if proc:
                if proc.poll() is None:
                    remaining = max(0.001, timeout - (time.perf_counter() - t0))
                    try:
                        proc.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        forced_termination = True
                        print(f"Warning: {name} did not exit within {timeout}s; terminating forcefully.", file=sys.stderr)
                        try:
                            proc.kill()
                            proc.wait(timeout=2.0)
                        except Exception:
                            pass
                # On Windows, native console applications (such as llama-server.exe)
                # exit with NTSTATUS 0xC000013A (STATUS_CONTROL_C_EXIT: 3221225786 unsigned,
                # -1073741510 signed) when responding to CTRL_BREAK_EVENT.
                is_clean = (
                    proc.returncode == 0 or
                    (sys.platform == "win32" and proc.returncode in (3221225786, -1073741510))
                )
                if is_clean:
                    if proc.returncode == 0:
                        print(f"{name} exited with code 0.")
                    else:
                        print(f"{name} exited with code {proc.returncode} (STATUS_CONTROL_C_EXIT).")
                else:
                    abnormal_exit = True
                    print(f"{name} exited with code {proc.returncode}.")

        # Step 3: Final Win32 Job Object close (guarantees zero orphaned child processes)
        self.job.close()

        # Flush and close child log file handles
        if self.llama_log_file and not self.llama_log_file.closed:
            try:
                self.llama_log_file.flush()
                self.llama_log_file.close()
            except Exception:
                pass
        if self.dashboard_log_file and not self.dashboard_log_file.closed:
            try:
                self.dashboard_log_file.flush()
                self.dashboard_log_file.close()
            except Exception:
                pass

        if forced_termination:
            print("Stop mode: FORCED (cooperative timeout expired, terminated forcefully).")
            print("Win32 Job Object closed; child processes terminated.")
            return False
        elif abnormal_exit:
            print("Stop mode: ABNORMAL (one or more processes exited with non-zero code).")
            print("Win32 Job Object closed; child processes terminated.")
            return False
        else:
            print("Stop mode: GRACEFUL (all processes exited cooperatively with code 0).")
            print("Win32 Job Object closed; child processes terminated.")
            return True

def main():
    import argparse
    parser = argparse.ArgumentParser(description="ODS Experimental Windows Native Supervisor")
    parser.add_argument("--data-dir", default="./data/supervisor_data", help="Data directory")
    parser.add_argument("--llama-port", type=int, default=8080)
    parser.add_argument("--dashboard-port", type=int, default=3002)
    parser.add_argument("--api-key", default=None, help="Dashboard API Key (generated if omitted)")
    parser.add_argument("--stop-timeout", type=float, default=5.0, help="Graceful stop timeout in seconds")
    parser.add_argument("--llama-url", default=None, help="Override download URL for llama-server runtime archive")
    parser.add_argument("--model-url", default=None, help="Override download URL for GGUF model")
    parser.add_argument("--stub-children", action="store_true", help="Run with lightweight stub children (no network, no downloads)")
    parser.add_argument("--stub-abnormal", action="store_true", help="Configure stub child to exit with code 3 on break (for abnormal stop test)")
    parser.add_argument("--stub-hang", action="store_true", help="Configure stub child to ignore break signal (for forced timeout test)")
    parser.add_argument("--run-probe", action="store_true", help="Start, verify health & chat, then stop")
    parser.add_argument("--stop-after-ready", action="store_true", help="Start, verify health, then execute stop() and exit 0")
    args = parser.parse_args()

    stub_behavior = "clean"
    if args.stub_abnormal:
        stub_behavior = "abnormal"
    elif args.stub_hang:
        stub_behavior = "hang"

    sup = WindowsNativeSupervisor(
        Path(args.data_dir),
        llama_port=args.llama_port,
        dashboard_port=args.dashboard_port,
        api_key=args.api_key,
        stop_timeout=args.stop_timeout,
        llama_url=args.llama_url,
        model_url=args.model_url,
        stub_children=args.stub_children,
        stub_behavior=stub_behavior
    )

    # Register signal handlers
    def handle_signal(sig, frame):
        sup.stop()
        sys.exit(0)
    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, handle_signal)

    # Pre-flight port availability check
    if not verify_port_available(args.llama_port):
        print(f"ERROR: Port {args.llama_port} is already in use by another process.", file=sys.stderr)
        sys.exit(3)
    if not verify_port_available(args.dashboard_port):
        print(f"ERROR: Port {args.dashboard_port} is already in use by another process.", file=sys.stderr)
        sys.exit(3)

    if args.stub_children:
        sup.start()
    else:
        llama_exe, model_path = sup.download_and_verify()
        sup.start(llama_exe, model_path)

    if args.stop_after_ready:
        print("Executing stop path verification after health check...")
        success = sup.stop()
        if not success:
            print("ERROR: Stop path experienced forced termination or abnormal child exit.", file=sys.stderr)
            sys.exit(7)
        print("SUCCESS: Stop path execution completed.")
        sys.exit(0)
    elif args.run_probe:
        # Run a test chat query through dashboard-api
        print("Probing dashboard-api /api/chat...")
        chat_payload = {"message": "Say PASS", "system": "You are a test assistant."}
        req = urllib.request.Request(
            f"http://127.0.0.1:{args.dashboard_port}/api/chat",
            data=json.dumps(chat_payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {sup.api_key}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"Chat probe response: {data}")
            if not data.get("success"):
                sup.stop()
                sys.exit(1)
        sup.stop()
        print("SUCCESS: Probe completed cleanly.")
        sys.exit(0)
    else:
        sup.monitor_until_interrupted()

if __name__ == "__main__":
    main()
