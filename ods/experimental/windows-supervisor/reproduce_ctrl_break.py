#!/usr/bin/env python3
"""
Win32 CTRL_BREAK / SIGBREAK Exit Code 3 Reproduction Script.
Demonstrates:
  1. Direct MSVCRT default SIGBREAK handling produces exit code 3.
  2. Standard Uvicorn captures SIGBREAK, runs full lifespan shutdown,
     then re-raises SIGBREAK after restoring SIG_DFL, causing MSVCRT exit code 3.
  3. Pre-registering a SIGBREAK handler (lambda s, f: sys.exit(0)) allows Uvicorn
     to complete full lifespan shutdown and exit cleanly with code 0.
  4. Stub child process with SIGBREAK handler exits cleanly with code 0 on CTRL_BREAK_EVENT.
"""

import json
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def run_reproduction():
    print("===============================================================")
    print("       Win32 CTRL_BREAK / SIGBREAK Reproduction Suite          ")
    print("===============================================================")
    print(f"Python: {sys.version}")
    print(f"Platform: {sys.platform}")

    evidence_dir = Path("scratch/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)

    if sys.platform != "win32":
        print("\nNotice: Current platform is not Win32. Win32 CTRL_BREAK is Windows-specific.")
        print("Writing platform notice to scratch/evidence/ctrl_break_findings.json.")
        summary = {
            "platform": sys.platform,
            "status": "skipped_on_posix",
            "explanation": "Win32 CTRL_BREAK_EVENT and MSVCRT exit code 3 are Windows-specific."
        }
        (evidence_dir / "ctrl_break_findings.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return

    # -------------------------------------------------------------
    # Test 1: Direct MSVCRT Default SIGBREAK Behavior
    # -------------------------------------------------------------
    print("\n--- Test 1: Direct MSVCRT Default SIGBREAK Handler ---")
    script_msvcrt = evidence_dir / "test_msvcrt_sigbreak.py"
    script_msvcrt.write_text('''
import signal
print("Raising unhandled SIGBREAK...")
signal.raise_signal(signal.SIGBREAK)
print("Unreachable")
''', encoding="utf-8")

    res1 = subprocess.run([sys.executable, str(script_msvcrt)], capture_output=True, text=True)
    print(f"Test 1 Exit Code: {res1.returncode}")
    print(f"Test 1 Stdout: {res1.stdout.strip()}")
    print(f"Test 1 Stderr: {res1.stderr.strip()}")

    # -------------------------------------------------------------
    # Test 2: Standard Uvicorn App on CTRL_BREAK_EVENT
    # -------------------------------------------------------------
    print("\n--- Test 2: Standard Uvicorn on CTRL_BREAK_EVENT ---")
    marker2 = evidence_dir / "marker_standard_uvicorn.txt"
    if marker2.exists():
        marker2.unlink()

    port2 = get_free_port()
    script_uvicorn_std = evidence_dir / "app_standard.py"
    script_uvicorn_std.write_text(f'''
import sys
import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager
from pathlib import Path

marker = Path("scratch/evidence/marker_standard_uvicorn.txt")

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[STD] Lifespan startup")
    try:
        yield
    finally:
        print("[STD] Lifespan shutdown executing...")
        marker.write_text("LIFESPAN_COMPLETED", encoding="utf-8")
        print("[STD] Lifespan shutdown completed.")

app = FastAPI(lifespan=lifespan)

@app.get("/health")
def health():
    return {{"status": "ok"}}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port={port2}, log_level="warning")
''', encoding="utf-8")

    proc2 = subprocess.Popen(
        [sys.executable, str(script_uvicorn_std)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    )

    import urllib.request
    ready2 = False
    for _ in range(40):
        time.sleep(0.25)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port2}/health", timeout=1.0) as r:
                if r.status == 200:
                    ready2 = True
                    break
        except Exception:
            pass
    assert ready2, "Standard Uvicorn server failed to start!"
    print(f"Standard Uvicorn server healthy on port {port2}. Sending CTRL_BREAK_EVENT...")
    proc2.send_signal(signal.CTRL_BREAK_EVENT)
    out2, _ = proc2.communicate(timeout=10)
    print(f"Test 2 Exit Code: {proc2.returncode}")
    print(f"Test 2 Stdout:\n{out2.strip()}")
    print(f"Test 2 Lifespan Marker Exists: {marker2.exists()}")
    if marker2.exists():
        print(f"Test 2 Lifespan Marker Content: {marker2.read_text(encoding='utf-8')}")

    # -------------------------------------------------------------
    # Test 3: Uvicorn with Pre-Registered SIGBREAK Clean Exit Handler
    # -------------------------------------------------------------
    print("\n--- Test 3: Uvicorn with Pre-Registered SIGBREAK Handler ---")
    marker3 = evidence_dir / "marker_clean_uvicorn.txt"
    if marker3.exists():
        marker3.unlink()

    port3 = get_free_port()
    while port3 == port2:
        port3 = get_free_port()

    script_uvicorn_clean = evidence_dir / "app_clean.py"
    script_uvicorn_clean.write_text(f'''
import sys
import signal
import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager
from pathlib import Path

marker = Path("scratch/evidence/marker_clean_uvicorn.txt")

# Pre-register clean exit handler for SIGBREAK before uvicorn.run
# When uvicorn restores handlers and re-raises SIGBREAK in finally,
# this handler executes sys.exit(0) instead of the MSVCRT exit(3).
def handle_sigbreak(sig, frame):
    print("[CLEAN] Pre-registered SIGBREAK handler invoked; exiting 0.")
    sys.exit(0)

signal.signal(signal.SIGBREAK, handle_sigbreak)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[CLEAN] Lifespan startup")
    try:
        yield
    finally:
        print("[CLEAN] Lifespan shutdown executing...")
        marker.write_text("LIFESPAN_COMPLETED", encoding="utf-8")
        print("[CLEAN] Lifespan shutdown completed.")

app = FastAPI(lifespan=lifespan)

@app.get("/health")
def health():
    return {{"status": "ok"}}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port={port3}, log_level="warning")
''', encoding="utf-8")

    proc3 = subprocess.Popen(
        [sys.executable, str(script_uvicorn_clean)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    )

    ready3 = False
    for _ in range(40):
        time.sleep(0.25)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port3}/health", timeout=1.0) as r:
                if r.status == 200:
                    ready3 = True
                    break
        except Exception:
            pass
    assert ready3, "Clean Uvicorn server failed to start!"
    print(f"Clean Uvicorn server healthy on port {port3}. Sending CTRL_BREAK_EVENT...")
    proc3.send_signal(signal.CTRL_BREAK_EVENT)
    out3, _ = proc3.communicate(timeout=10)
    print(f"Test 3 Exit Code: {proc3.returncode}")
    print(f"Test 3 Stdout:\n{out3.strip()}")
    print(f"Test 3 Lifespan Marker Exists: {marker3.exists()}")
    if marker3.exists():
        print(f"Test 3 Lifespan Marker Content: {marker3.read_text(encoding='utf-8')}")

    # -------------------------------------------------------------
    # Test 4: Stub Child Process with SIGBREAK Handler
    # -------------------------------------------------------------
    print("\n--- Test 4: Stub Child Process on CTRL_BREAK_EVENT ---")
    stub_code = (
        "import time, sys, signal\n"
        "def _exit(s, f): sys.exit(0)\n"
        "signal.signal(signal.SIGBREAK, _exit)\n"
        "while True:\n"
        "    try: time.sleep(0.2)\n"
        "    except BaseException: sys.exit(0)\n"
    )
    proc4 = subprocess.Popen(
        [sys.executable, "-c", stub_code],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    )
    time.sleep(0.5)
    assert proc4.poll() is None, "Stub child exited prematurely!"
    proc4.send_signal(signal.CTRL_BREAK_EVENT)
    proc4.wait(timeout=5)
    print(f"Test 4 Stub Exit Code: {proc4.returncode}")

    summary = {
        "test1_direct_sigbreak_code": res1.returncode,
        "test2_standard_uvicorn_code": proc2.returncode,
        "test2_lifespan_executed": marker2.exists() and marker2.read_text(encoding="utf-8") == "LIFESPAN_COMPLETED",
        "test3_clean_uvicorn_code": proc3.returncode,
        "test3_lifespan_executed": marker3.exists() and marker3.read_text(encoding="utf-8") == "LIFESPAN_COMPLETED",
        "test4_stub_exit_code": proc4.returncode
    }
    (evidence_dir / "ctrl_break_findings.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nSummary of Findings:\n{json.dumps(summary, indent=2)}")

if __name__ == "__main__":
    run_reproduction()
