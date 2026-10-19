"""Cancel only the child owned by this bootstrap download generation."""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time


CANCELLED = 76


def valid_generation(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) is not None


def cancel_requested(path, generation):
    if not valid_generation(generation):
        return False
    try:
        # NONBLOCK prevents a substituted FIFO from hanging before fstat.
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                return False
            data = os.read(fd, 4097)
        finally:
            os.close(fd)
        if len(data) > 4096:
            return False
        value = json.loads(data)
        return isinstance(value, dict) and value.get("generation") == generation
    except (OSError, ValueError, UnicodeError):
        return False


def _stop_child(child):
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def supervise(command, request_path, generation, *, interrupted=None):
    if not valid_generation(generation) or not command:
        raise ValueError("A generation and child command are required")
    interrupted = interrupted if interrupted is not None else [0]
    if interrupted[0]:
        return 128 + interrupted[0]
    if cancel_requested(request_path, generation):
        return CANCELLED
    child = None
    try:
        child = subprocess.Popen(command, shell=False)
        while True:
            if interrupted[0]:
                _stop_child(child)
                return 128 + interrupted[0]
            if cancel_requested(request_path, generation):
                _stop_child(child)
                return CANCELLED
            code = child.poll()
            if code is not None:
                return code if code >= 0 else 128 - code
            time.sleep(0.25)
    finally:
        if child is not None:
            _stop_child(child)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not valid_generation(args.generation):
        parser.error("Invalid generation")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.check:
        return 0 if cancel_requested(args.request, args.generation) else 1
    if not command:
        parser.error("Missing child command")
    interrupted = [0]

    def observe_signal(signum, _frame):
        # Record instead of raising: a signal between Popen spawning and
        # assigning its result must not orphan a child we have not stored yet.
        interrupted[0] = signum

    previous = {}
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, observe_signal)
        return supervise(command, args.request, args.generation, interrupted=interrupted)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
