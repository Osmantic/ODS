#!/usr/bin/env python3
"""
Real llama-server and model download test suite for ODS Windows Native Supervisor.
Exercises heavy network asset acquisition, cryptographic pinning, and live chat probe:
  1. Live Happy Path Probe (real llama-server.exe + real dashboard-api + /api/chat probe)
  2. Cached hash mismatch re-download healing via .part
  3. Persistent hash mismatch rejection via test server URL override
"""

import os
import sys
import time
import json
import socket
import subprocess
from pathlib import Path

PYTHON = sys.executable
THIS_DIR = Path(__file__).resolve().parent
SUPERVISOR_SCRIPT = THIS_DIR / "supervisor.py"
DATA_DIR = THIS_DIR / "test_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def get_free_port_pair() -> tuple[int, int]:
    p1 = get_free_port()
    p2 = get_free_port()
    while p2 == p1:
        p2 = get_free_port()
    return p1, p2

def run_test_happy_path_live():
    print("\n[REAL-TEST 1/3] Testing Live Happy Path Probe with Real Downloads & /api/chat...")
    llama_port, dash_port = get_free_port_pair()
    print(f"Allocated ports: llama={llama_port}, dashboard={dash_port}")
    t0 = time.perf_counter()
    shared_data = DATA_DIR / "shared_data"
    cmd = [
        PYTHON, str(SUPERVISOR_SCRIPT),
        "--data-dir", str(shared_data),
        "--llama-port", str(llama_port),
        "--dashboard-port", str(dash_port),
        "--run-probe"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.perf_counter() - t0
    print(f"Exit code: {res.returncode} in {elapsed:.2f}s")
    print(f"Stdout snippet:\n{res.stdout[-350:].strip()}")
    if res.returncode != 0:
        print(f"FULL STDERR:\n{res.stderr}")
    assert res.returncode == 0, f"Expected exit code 0, got {res.returncode}\nStderr: {res.stderr}"
    assert "Both processes healthy" in res.stdout, "Expected healthy processes in stdout"
    assert "Chat probe response" in res.stdout, "Expected chat probe response"

    key_file = shared_data / "api_key.txt"
    assert key_file.exists(), "api_key.txt not written"
    api_key = key_file.read_text(encoding="utf-8").strip()
    assert len(api_key) >= 16, f"Generated API key is unexpectedly short: {api_key}"
    print(f"PASS: Live happy path probe executed cleanly (API Key: {api_key[:8]}...).")

def run_test_cached_hash_healing():
    print("\n[REAL-TEST 2/3] Testing Cached Hash Mismatch Re-Download Healing...")
    bad_data = DATA_DIR / "bad_hash_data"
    bad_data.mkdir(parents=True, exist_ok=True)

    shared_models = DATA_DIR / "shared_data" / "models"
    bad_models = bad_data / "models"
    bad_models.mkdir(parents=True, exist_ok=True)
    src_model = shared_models / "SmolLM2-135M-Instruct-Q4_K_M.gguf"
    if src_model.exists():
        import shutil
        shutil.copy2(src_model, bad_models / "SmolLM2-135M-Instruct-Q4_K_M.gguf")

    # Corrupt cached archive
    bad_zip = bad_data / "llama-b9014-bin-win-cpu-x64.zip"
    bad_zip.write_text("corrupted cached content", encoding="utf-8")

    cmd = [PYTHON, "-u", str(SUPERVISOR_SCRIPT), "--data-dir", str(bad_data), "--stop-after-ready"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    print(f"Exit code: {res.returncode}")
    if res.stderr:
        print(f"Stderr snippet:\n{res.stderr[-300:].strip()}")
    assert res.returncode == 0, f"Expected exit code 0 after re-download healing, got {res.returncode}\nStderr: {res.stderr}"
    assert "SHA-256 mismatch" in res.stderr and "re-downloading" in res.stderr, "Expected re-download notice in stderr"
    assert bad_zip.stat().st_size == 15917797, f"Corrupted file was not replaced: {bad_zip.stat().st_size}"
    print("PASS: Cached hash mismatch detected, re-downloaded to .part, and healed successfully.")

def run_test_persistent_hash_mismatch():
    print("\n[REAL-TEST 3/3] Testing Persistent Hash Mismatch Rejection via Test Server URL Override (Exit Code 2)...")
    import http.server
    import threading

    corrupt_data = DATA_DIR / "corrupt_data"
    corrupt_data.mkdir(parents=True, exist_ok=True)

    test_server_port = get_free_port()

    class CorruptPayloadHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            payload = b"PERSISTENT_CORRUPT_BYTES_FOR_HASH_TEST"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", test_server_port), CorruptPayloadHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    try:
        corrupt_url = f"http://127.0.0.1:{test_server_port}/corrupt-llama.zip"
        cmd = [
            PYTHON, "-u", str(SUPERVISOR_SCRIPT),
            "--data-dir", str(corrupt_data),
            "--llama-url", corrupt_url
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        print(f"Exit code: {res.returncode}")
        print(f"Stderr: {res.stderr.strip()}")
        assert res.returncode == 2, f"Expected exit code 2, got {res.returncode}"
        assert "SHA-256 mismatch" in res.stderr, "Expected SHA-256 mismatch in stderr"
        print("PASS: Persistent hash mismatch rejected cleanly with exit code 2.")
    finally:
        httpd.shutdown()
        httpd.server_close()

def main():
    print("===============================================================")
    print("   ODS Native Supervisor Real Download Test Suite              ")
    print("   (Heavy Network Downloads, Pinned SHA-256 Verification)      ")
    print("===============================================================")
    t0 = time.perf_counter()
    run_test_happy_path_live()
    time.sleep(1.0)
    run_test_cached_hash_healing()
    time.sleep(0.5)
    run_test_persistent_hash_mismatch()
    elapsed = time.perf_counter() - t0
    print("\n===============================================================")
    print(f"SUCCESS: All 3 real download tests passed in {elapsed:.2f}s!    ")
    print("===============================================================")

if __name__ == "__main__":
    main()
