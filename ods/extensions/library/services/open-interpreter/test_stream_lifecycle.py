"""Subprocess lifecycle tests for the Open Interpreter stream endpoint.

These cover the paths where the runner subprocess would otherwise be left
running: a client that disconnects mid-stream, and a runner that ignores
SIGTERM or never exits on its own.
"""

import pathlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

# server.py calls DATA_DIR.mkdir() at import time against the hardcoded
# container path /app/data, so importing it on a host needs mkdir stubbed.
with patch.object(pathlib.Path, "mkdir"):
    import server


def fake_proc(lines=(), *, returncode=0, alive=True):
    """A Popen stand-in. `alive` drives poll(): None means still running."""
    proc = MagicMock()
    # A MagicMock, not a bare iterator: the code both iterates it and closes it.
    proc.stdout = MagicMock(closed=False)
    proc.stdout.__iter__.return_value = iter(lines)
    proc.stdin = MagicMock(closed=False)
    proc.poll.return_value = None if alive else returncode
    proc.wait.return_value = returncode
    return proc


def drain(gen):
    return list(gen)


# --- client disconnects mid-stream -----------------------------------------

def test_terminates_runner_when_client_disconnects():
    proc = fake_proc(["SSE: a\n", "SSE: b\n", "SSE: c\n"])
    with patch.object(server.subprocess, "Popen", return_value=proc), \
         patch.object(server.os, "unlink"):
        gen = server._stream_interpreter("/tmp/runner.py", "{}")
        # NB: "SSE: a\n"[5:] keeps the newline, so the frame carries an extra
        # blank line. That framing quirk predates this change; see notes.
        assert next(gen) == "data: a\n\n\n"
        gen.close()  # Starlette closes the generator when the client goes away

    proc.terminate.assert_called_once()


def test_removes_temp_script_when_client_disconnects():
    proc = fake_proc(["SSE: a\n", "SSE: b\n"])
    with patch.object(server.subprocess, "Popen", return_value=proc), \
         patch.object(server.os, "unlink") as unlink:
        gen = server._stream_interpreter("/tmp/runner.py", "{}")
        next(gen)
        gen.close()

    unlink.assert_called_once_with("/tmp/runner.py")


def test_closes_pipes_when_client_disconnects():
    proc = fake_proc(["SSE: a\n", "SSE: b\n"])
    with patch.object(server.subprocess, "Popen", return_value=proc), \
         patch.object(server.os, "unlink"):
        gen = server._stream_interpreter("/tmp/runner.py", "{}")
        next(gen)
        gen.close()

    proc.stdout.close.assert_called_once()


# --- runner that will not die ----------------------------------------------

def test_kills_runner_that_ignores_sigterm():
    proc = fake_proc(["SSE: a\n", "SSE: b\n"])
    # Only the post-SIGTERM wait times out; the wait after SIGKILL returns.
    proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="python", timeout=5), -9]
    with patch.object(server.subprocess, "Popen", return_value=proc), \
         patch.object(server.os, "unlink"):
        gen = server._stream_interpreter("/tmp/runner.py", "{}")
        next(gen)
        gen.close()

    proc.kill.assert_called_once()


def test_arms_watchdog_and_cancels_it_on_normal_completion():
    proc = fake_proc(["SSE: a\n"], alive=False)
    timer = MagicMock()
    with patch.object(server.subprocess, "Popen", return_value=proc), \
         patch.object(server.threading, "Timer", return_value=timer) as timer_cls, \
         patch.object(server.os, "unlink"):
        drain(server._stream_interpreter("/tmp/runner.py", "{}"))

    timer_cls.assert_called_once_with(server.STREAM_TIMEOUT_SECONDS, proc.kill)
    timer.start.assert_called_once()
    timer.cancel.assert_called_once()


# --- exit status ------------------------------------------------------------

def test_emits_error_frame_when_runner_exits_nonzero():
    proc = fake_proc(["SSE: a\n", "Traceback: boom\n"], returncode=1, alive=False)
    with patch.object(server.subprocess, "Popen", return_value=proc), \
         patch.object(server.os, "unlink"):
        frames = drain(server._stream_interpreter("/tmp/runner.py", "{}"))

    assert frames[0] == "data: a\n\n\n"
    assert frames[-1].startswith("event: error")


def test_no_error_frame_on_clean_exit():
    proc = fake_proc(["SSE: a\n"], returncode=0, alive=False)
    with patch.object(server.subprocess, "Popen", return_value=proc), \
         patch.object(server.os, "unlink"):
        frames = drain(server._stream_interpreter("/tmp/runner.py", "{}"))

    assert frames == ["data: a\n\n\n"]


def test_does_not_signal_an_already_exited_runner():
    proc = fake_proc(["SSE: a\n"], alive=False)
    with patch.object(server.subprocess, "Popen", return_value=proc), \
         patch.object(server.os, "unlink"):
        drain(server._stream_interpreter("/tmp/runner.py", "{}"))

    proc.terminate.assert_not_called()
    proc.kill.assert_not_called()


# --- other exit paths -------------------------------------------------------

def test_removes_temp_script_when_spawn_fails():
    with patch.object(server.subprocess, "Popen", side_effect=OSError("no python")), \
         patch.object(server.os, "unlink") as unlink:
        with pytest.raises(OSError):
            drain(server._stream_interpreter("/tmp/runner.py", "{}"))

    unlink.assert_called_once_with("/tmp/runner.py")


def test_failure_log_tail_is_bounded():
    noise = [f"line {i}\n" for i in range(200)]
    proc = fake_proc(noise, returncode=1, alive=False)
    with patch.object(server.subprocess, "Popen", return_value=proc), \
         patch.object(server.os, "unlink"), \
         patch.object(server.logger, "error") as log_error:
        drain(server._stream_interpreter("/tmp/runner.py", "{}"))

    logged = log_error.call_args[0][2]
    assert len(logged.split(" | ")) == server.STDERR_TAIL_LINES
