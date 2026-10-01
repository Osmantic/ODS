"""Bounded owner-only PTY for the installed exact-plan approval helper.

Not a shell service. Callers may supply only an Operations job/hash and input
lines to its existing password/challenge dialogue. Never log input or output.
"""

from __future__ import annotations

import errno
import codecs
import fcntl
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import sys
import termios
import threading
import time

JOB = re.compile(r"ops-[0-9]{13}-[a-f0-9]{12}")
HASH = re.compile(r"[a-f0-9]{64}")
MAX_OUTPUT = 1024 * 1024
MAX_INPUT = 1024


class ApprovalTerminalError(RuntimeError):
    pass


def _identity(job, plan):
    if (
        not isinstance(job, str)
        or not JOB.fullmatch(job)
        or not isinstance(plan, str)
        or not HASH.fullmatch(plan)
    ):
        raise ApprovalTerminalError("invalid-plan-identity")


def _trusted_file(file):
    info = file.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
        or not info.st_mode & 0o111
        or info.st_size > 256 * 1024
        or file.resolve() != file
    ):
        raise ApprovalTerminalError("approval-helper-unavailable")


def _child(helper, job, plan, parent_pid):
    """Fresh child process, avoiding preexec_fn in the threaded host agent."""
    _identity(job, plan)
    if os.getuid() == 0 or not all(os.isatty(fd) for fd in (0, 1, 2)):
        raise ApprovalTerminalError("owner-terminal-required")
    import ctypes

    interrupted = False

    def stop(_signum, _frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGHUP, stop)
    signal.signal(signal.SIGTERM, stop)

    if not parent_pid.isdecimal() or int(parent_pid) < 1:
        raise ApprovalTerminalError("owner-terminal-required")
    if ctypes.CDLL(None, use_errno=True).prctl(
        1, signal.SIGHUP, 0, 0, 0
    ) != 0 or os.getppid() != int(parent_pid):
        raise ApprovalTerminalError("owner-terminal-required")
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    _trusted_file(Path(helper))
    environment = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "TERM": "dumb",
    }
    process = None
    try:
        if interrupted:
            raise ApprovalTerminalError("owner-terminal-required")
        process = subprocess.Popen([helper, job, plan, "--confirm"], env=environment)
        while process.poll() is None and not interrupted:
            time.sleep(0.02)
        if interrupted:
            process.terminate()
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        result = process.returncode
    finally:
        # Stay as the session leader: exec/setuid and a shell's signal handling
        # must not be relied on to run the original helper's EXIT cleanup.
        _revoke_credentials()
        if interrupted:
            os.killpg(os.getpgrp(), signal.SIGKILL)
    raise SystemExit(result)


def _revoke_credentials():
    try:
        result = subprocess.run(
            ["/usr/bin/sudo", "-K"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={"PATH": "/usr/bin:/bin"},
            timeout=3,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class ApprovalPTY:
    """One process group and private bounded output; no credential persistence."""

    def __init__(self, install_dir, job, plan, *, timeout=180, idle_timeout=30):
        _identity(job, plan)
        if sys.platform != "linux" or os.getuid() == 0:
            raise ApprovalTerminalError("owner-terminal-required")
        helper = Path(install_dir) / "bin" / "ods-pixel-approve"
        _trusted_file(helper)
        self.job, self.plan = job, plan
        self._lock = threading.RLock()
        self._output = bytearray()
        self._total = 0
        self._sequence = 0
        self._state = "running"
        self._exit = None
        self._cleanup = False
        self._started = self._contact = time.monotonic()
        self._timeout, self._idle_timeout = timeout, idle_timeout
        master, slave = os.openpty()
        # Never echo user input, including a password sent before sudo's prompt.
        # sudo restores this already-disabled ECHO state after authentication.
        settings = termios.tcgetattr(slave)
        settings[3] &= ~(termios.ECHO | termios.ECHONL)
        termios.tcsetattr(slave, termios.TCSANOW, settings)
        self._master = master
        os.set_blocking(master, False)
        try:
            self._process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--child",
                    str(helper),
                    job,
                    plan,
                    str(os.getpid()),
                ],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                cwd="/",
                env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
                close_fds=True,
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self._worker = threading.Thread(target=self._pump, daemon=True)
        try:
            self._worker.start()
        except RuntimeError:
            self._stop("unconfirmed")
            self._process.wait(timeout=3)
            os.close(master)
            self._revoke()
            raise ApprovalTerminalError("approval-terminal-unavailable") from None

    def _revoke(self):
        # Also runs after forced termination, when the helper's EXIT trap cannot.
        self._cleanup = _revoke_credentials()

    def _stop(self, state):
        with self._lock:
            if self._state != "running":
                return
            self._state = state
            # Never target a reaped leader's numeric PID. All poll/wait calls
            # share this lock so the leader cannot be reaped during signaling.
            if self._process.poll() is not None:
                return
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self._process.pid, sig)
                except ProcessLookupError:
                    break
                if sig == signal.SIGTERM:
                    time.sleep(0.1)

    def _pump(self):
        try:
            while True:
                now = time.monotonic()
                if (
                    now - self._started >= self._timeout
                    or now - self._contact >= self._idle_timeout
                ):
                    self._stop("timed-out")
                ready, _, _ = select.select([self._master], [], [], 0.05)
                if ready:
                    try:
                        chunk = os.read(self._master, 8192)
                    except OSError as error:
                        if error.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    with self._lock:
                        self._total += len(chunk)
                        if self._total <= MAX_OUTPUT:
                            self._output.extend(chunk)
                        else:
                            self._stop("output-limit")
                with self._lock:
                    if self._process.poll() is not None and not ready:
                        break
        except (OSError, ValueError):
            self._stop("unconfirmed")
        finally:
            self._stop("exited")
            try:
                with self._lock:
                    self._exit = self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._state = "unconfirmed"
            os.close(self._master)
            self._revoke()

    def poll(self, cursor=0):
        with self._lock:
            if type(cursor) is not int or not 0 <= cursor <= len(self._output):
                raise ApprovalTerminalError("invalid-terminal-cursor")
            self._contact = time.monotonic()
            output = bytes(self._output[cursor : cursor + 65536])
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            text = decoder.decode(
                output,
                final=self._exit is not None
                and cursor + len(output) == len(self._output),
            )
            consumed = len(output) - len(decoder.getstate()[0])
            # Output is private, untrusted terminal text. HTTP must not log it;
            # UI renders text only, never ANSI/OSC/HTML actions.
            return {
                "state": self._state,
                "output": text,
                "exitCode": self._exit,
                "cleanupConfirmed": self._cleanup,
                "nextSequence": self._sequence,
                "nextCursor": cursor + consumed,
                "moreOutput": cursor + consumed < len(self._output),
            }

    def input(self, sequence, line):
        if type(sequence) is not int or not isinstance(line, str):
            raise ApprovalTerminalError("invalid-terminal-input")
        encoded = line.encode("utf-8")
        if (
            not encoded
            or len(encoded) > MAX_INPUT
            or any(c < 32 or c == 127 for c in encoded)
        ):
            raise ApprovalTerminalError("invalid-terminal-input")
        with self._lock:
            if self._state != "running" or sequence != self._sequence:
                raise ApprovalTerminalError("terminal-input-not-current")
            self._contact = time.monotonic()
            payload = encoded + b"\n"
            # One bounded atomic PTY write. Never retain input for replay.
            if os.write(self._master, payload) != len(payload):
                self._stop("unconfirmed")
                raise ApprovalTerminalError("terminal-input-unconfirmed")
            self._sequence += 1

    def cancel(self):
        self._stop("cancelled")
        self._worker.join(timeout=7)
        if not self._worker.is_alive() and not self._cleanup:
            self._revoke()
        return {
            "stopped": not self._worker.is_alive() and self._exit is not None,
            "cleanupConfirmed": self._cleanup,
        }


if __name__ == "__main__":
    if len(sys.argv) != 6 or sys.argv[1] != "--child":
        raise SystemExit(2)
    try:
        _child(*sys.argv[2:])
    except Exception:
        # No exception text, input, environment or path in the terminal.
        os.write(2, b"Approval terminal is unavailable.\n")
        raise SystemExit(1)


class ApprovalTerminals:
    """Single owner terminal; secret handles never enter URLs or chat receipts."""

    def __init__(self, install_dir, verify_plan):
        self._install = install_dir
        self._verify = verify_plan
        self._gate = threading.Lock()
        self._record = None

    def request(self, body):
        import secrets

        if not isinstance(body, dict) or body.get("action") not in {
            "start",
            "poll",
            "input",
            "cancel",
        }:
            raise ApprovalTerminalError("invalid-terminal-request")
        action = body["action"]
        required = (
            {"action", "owner", "job", "plan"}
            if action == "start"
            else {"action", "owner", "session"}
        )
        if action == "poll":
            required.add("cursor")
        if action == "input":
            required |= {"sequence", "line"}
        if (
            set(body) != required
            or not isinstance(body["owner"], str)
            or not HASH.fullmatch(body["owner"])
        ):
            raise ApprovalTerminalError("invalid-terminal-request")
        with self._gate:
            if action == "start":
                _identity(body["job"], body["plan"])
                if self._record:
                    previous = self._record["terminal"]
                    if previous._worker.is_alive() or not previous._cleanup:
                        raise ApprovalTerminalError("approval-terminal-busy")
                if not self._verify(body["job"], body["plan"]):
                    raise ApprovalTerminalError("plan-not-awaiting-approval")
                handle = secrets.token_hex(32)
                terminal = ApprovalPTY(self._install, body["job"], body["plan"])
                self._record = {
                    "session": handle,
                    "owner": body["owner"],
                    "terminal": terminal,
                }
                return {"session": handle, "state": "running", "nextSequence": 0}
            record = self._record
            if (
                not record
                or not isinstance(body["session"], str)
                or not secrets.compare_digest(record["session"], body["session"])
                or not secrets.compare_digest(record["owner"], body["owner"])
            ):
                raise ApprovalTerminalError("terminal-session-unavailable")
            terminal = record["terminal"]
            if action == "input":
                terminal.input(body["sequence"], body["line"])
                return {"accepted": True, "nextSequence": terminal._sequence}
            if action == "cancel":
                return terminal.cancel()
            return terminal.poll(body["cursor"])
