"""Exercise native readiness with real POSIX spool IO and controlled receipts."""
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX spool contract")
SOURCE = Path(__file__).resolve().parents[1] / "installers/macos/lib/pixel-native-services.py"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        time.sleep(0.001)


def succeeded(job):
    return {"jobId": job, "status": "succeeded",
            "steps": [{"stdout": json.dumps({"available": True})}]}


def timed_out(job):
    return {"jobId": job, "status": "failed", "error": "step exceeded its hard timeout"}


@pytest.fixture
def harness(tmp_path):
    @contextmanager
    def start(respond, on_read=lambda receipt, clock: None):
        spec = importlib.util.spec_from_file_location("readiness_under_test", SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        state = tmp_path / "state"
        for directory in (state, state / "requests", state / "results"):
            directory.mkdir(mode=0o700)
        calls, errors, stop = [], [], threading.Event()
        clock = Clock()

        def loads(data):
            receipt = json.loads(data)
            on_read(receipt, clock)
            return receipt

        module.time = clock
        module.json = SimpleNamespace(loads=loads, dumps=json.dumps)

        @contextmanager
        def protected_directory(path):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                yield fd
            finally:
                os.close(fd)

        def publish(job, receipt):
            temporary = state / "results" / ("." + job + ".tmp")
            with temporary.open("wb") as stream:
                stream.write(json.dumps(receipt).encode())
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(state / "results" / (job + ".json"))

        def worker():
            try:
                while not stop.is_set():
                    for path in sorted((state / "requests").glob("*.json")):
                        if path.stem in calls:
                            continue
                        payload = json.loads(path.read_bytes())
                        assert payload["jobId"] == path.stem
                        assert payload["action"] == "host.os-release"
                        assert payload["target"] == "ods-host"
                        calls.append(path.stem)
                        respond(path.stem, len(calls), publish, state)
                    stop.wait(0.001)
            except BaseException as exc:
                errors.append(exc)

        identity = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        run = lambda: module._operations_readiness(
            custody=SimpleNamespace(protected_directory=protected_directory),
            state=state, owner_entry=identity, broker=identity)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            yield SimpleNamespace(run=run, calls=calls, state=state)
        finally:
            stop.set()
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert not errors, errors
    return start


@pytest.mark.parametrize("failures", [0, 1, 2, 3])
def test_terminal_timeout_retry_limit(harness, failures):
    def respond(job, count, publish, state):
        publish(job, timed_out(job) if count <= failures else succeeded(job))
    with harness(respond) as probe:
        if failures == 3:
            with pytest.raises(ValueError, match="native-operations-readiness-failed"):
                probe.run()
        else:
            assert probe.run() is True
        assert len(probe.calls) == min(failures + 1, 3)
        assert len(set(probe.calls)) == len(probe.calls)
        for job in probe.calls[:failures]:
            assert json.loads((probe.state / "results" / (job + ".json")).read_bytes()) == timed_out(job)


@pytest.mark.parametrize("status", ["failed", "cancelled", "rejected"])
def test_other_terminal_failure_is_not_retried(harness, status):
    def respond(job, count, publish, state):
        publish(job, {"jobId": job, "status": status, "error": "different failure"})
    with harness(respond) as probe:
        with pytest.raises(ValueError, match="native-operations-readiness-failed"):
            probe.run()
        assert len(probe.calls) == 1


@pytest.mark.parametrize("kind", ["mismatch", "symlink", "missing"])
def test_invalid_or_missing_result_does_not_submit_another_job(harness, kind):
    def respond(job, count, publish, state):
        if kind == "mismatch":
            publish(job, succeeded("different-job"))
        elif kind == "symlink":
            target = state / "target.json"
            target.write_text(json.dumps(succeeded(job)))
            (state / "results" / (job + ".json")).symlink_to(target)
    error = OSError if kind == "symlink" else ValueError
    match = None if kind == "symlink" else ("result-mismatch" if kind == "mismatch" else "readiness-timeout")
    with harness(respond) as probe:
        with pytest.raises(error, match=match):
            probe.run()
        assert len(probe.calls) == 1


def test_running_receipt_is_polled_without_resubmission(harness):
    parsed = threading.Event()
    def on_read(receipt, clock):
        if receipt.get("status") == "running":
            parsed.set()
    def respond(job, count, publish, state):
        publish(job, {"jobId": job, "status": "running"})
        assert parsed.wait(timeout=2)
        publish(job, succeeded(job))
    with harness(respond, on_read) as probe:
        assert probe.run() is True
        assert len(probe.calls) == 1


def test_shared_deadline_prevents_retry_after_terminal_timeout(harness):
    def on_read(receipt, clock):
        if receipt.get("status") == "failed":
            clock.now += 45
    def respond(job, count, publish, state):
        publish(job, timed_out(job))
    with harness(respond, on_read) as probe:
        with pytest.raises(ValueError, match="native-operations-readiness-timeout"):
            probe.run()
        assert len(probe.calls) == 1
