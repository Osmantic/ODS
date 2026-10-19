"""Real child-process cancellation, including stale requests and interruptions."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
import bootstrap_download_control as control

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Native bootstrap child supervision is POSIX-only")

GEN = "a" * 32


def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Child did not reach the expected state")


def launch(tmp_path, child_code):
    request = tmp_path / "cancel.json"
    process = subprocess.Popen([
        sys.executable, control.__file__, "--request", str(request),
        "--generation", GEN, "--", sys.executable, "-c", child_code,
    ])
    return process, request


def test_cancel_stops_the_owned_writer_and_preserves_partial(tmp_path):
    partial = tmp_path / "model.part"
    process, request = launch(tmp_path, f"import time; f=open({str(partial)!r},'wb');\nwhile True: f.write(b'x'*100); f.flush(); time.sleep(.02)")
    try:
        wait_for(lambda: partial.exists() and partial.stat().st_size > 0)
        request.write_text(json.dumps({"generation": GEN}))
        assert process.wait(timeout=5) == control.CANCELLED
        saved = partial.read_bytes()
        time.sleep(0.1)
        assert partial.read_bytes() == saved and saved
        assert request.exists()  # Never unlink a request that could be replaced.
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_stale_generation_does_not_cancel_new_download(tmp_path):
    request = tmp_path / "cancel.json"
    request.write_text(json.dumps({"generation": "b" * 32}))
    assert control.supervise([sys.executable, "-c", "raise SystemExit(7)"], request, GEN) == 7


def test_child_failure_cannot_impersonate_cancellation(tmp_path):
    assert control.supervise(
        [sys.executable, "-c", "raise SystemExit(76)"], tmp_path / "request", GEN
    ) == 1


def test_preexisting_request_prevents_spawning(tmp_path):
    request = tmp_path / "cancel.json"
    request.write_text(json.dumps({"generation": GEN}))
    assert control.supervise(["nonexistent-command-should-never-start"], request, GEN) == 76


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_external_interrupt_reaps_child_and_preserves_signal_status(tmp_path, sig):
    marker = tmp_path / "child.json"
    process, _ = launch(tmp_path, f"import os,time,json; open({str(marker)!r},'w').write(json.dumps(os.getpid())); time.sleep(60)")
    try:
        wait_for(marker.exists)
        pid = json.loads(marker.read_text())
        process.send_signal(sig)
        assert process.wait(timeout=5) == 128 + sig
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_invalid_requests_never_block_or_cancel(tmp_path):
    path = tmp_path / "request"
    for raw in ["bad json", "[]", "{}", "x" * 4097, '{"generation": "wrong"}']:
        path.write_text(raw)
        assert not control.cancel_requested(path, GEN)
    path.unlink()
    target = tmp_path / "target"
    target.write_text(json.dumps({"generation": GEN}))
    path.symlink_to(target)
    assert not control.cancel_requested(path, GEN)
    path.unlink()
    os.mkfifo(path)
    assert not control.cancel_requested(path, GEN)


def test_missing_command_and_bad_generation_are_refused(tmp_path):
    with pytest.raises(ValueError):
        control.supervise([], tmp_path / "request", GEN)
    with pytest.raises(ValueError):
        control.supervise([sys.executable], tmp_path / "request", "../bad")


def test_actual_bootstrap_worker_acknowledges_cancel_without_changing_model(tmp_path):
    ods = Path(__file__).resolve().parents[4]
    install = tmp_path / "install"
    models = install / "data/models"
    models.mkdir(parents=True)
    (install / "config/llama-server").mkdir(parents=True)
    (install / "data/.llama-server.pid").write_text("999999")
    original = "GGUF_FILE=Bootstrap.gguf\nLLM_MODEL=bootstrap\nGPU_BACKEND=apple\nCTX_SIZE=8192\n"
    (install / ".env").write_text(original)
    (models / "Bootstrap.gguf").write_bytes(b"bootstrap")
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    for name in ["docker", "sudo"]:
        path = fakebin / name
        path.write_text("#!/bin/sh\nexit 1\n")
        path.chmod(0o755)
    curl = fakebin / "curl"
    curl.write_text(f"#!{sys.executable}\nimport sys,time\nif '-sI' in sys.argv:\n print('HTTP/1.1 200 OK\\r\\ncontent-length: 1000000000\\r\\n'); raise SystemExit(0)\nf=open(sys.argv[sys.argv.index('-o')+1],'ab')\nwhile True: f.write(b'x'*1000); f.flush(); time.sleep(.05)\n")
    curl.chmod(0o755)
    partial = models / "Full.gguf.part"
    with (tmp_path / "worker.log").open("w") as log:
        process = subprocess.Popen(["bash", str(ods / "scripts/bootstrap-upgrade.sh"), str(install),
                                    "Full.gguf", "https://example.invalid/Full.gguf", "", "full", "32768", "Bootstrap.gguf"],
                                   env={**os.environ, "PATH": f"{fakebin}:{os.environ['PATH']}"}, stdout=log, stderr=log)
        try:
            wait_for(lambda: partial.exists() and partial.stat().st_size > 0, seconds=15)
            status_path = install / "data/bootstrap-status.json"
            status = json.loads(status_path.read_text())
            assert control.valid_generation(status["downloadGeneration"])
            request = install / "data/bootstrap-download.cancel.json"
            request.write_text(json.dumps({"generation": status["downloadGeneration"]}))
            assert process.wait(timeout=10) == 0
            final = json.loads(status_path.read_text())
            assert final["status"] == "cancelled"
            assert final["downloadGeneration"] == status["downloadGeneration"]
            assert partial.stat().st_size > 0 and not (models / "Full.gguf").exists()
            assert (install / ".env").read_text() == original
            assert (models / "Bootstrap.gguf").read_bytes() == b"bootstrap"
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
