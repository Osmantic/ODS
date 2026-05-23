#!/usr/bin/env python3
"""Parallel docker image pull with an aggregated live dashboard.

Replaces the sequential pull-with-spinner loop in installers/phases/08-images.sh.
Runs up to --concurrency pulls at once and renders one dashboard with per-job
rows showing spinner, per-image elapsed, and layer-event counts
("3/8 layers"). Lore strings rotate inline at the dashboard footer every 8s
instead of being printed on new lines.

Why layer counts and not bytes/MB/percent: the "X.YZMB / AB.CDMB" suffix
on docker's "Downloading" lines only appears reliably with the classic
overlay2 image store. Docker's containerd snapshotter (default on Docker
Desktop, opt-in on engine) emits bare "abc: Downloading" with no bytes in
non-TTY mode. A bytes-based progress display silently showed 0 on those
installs. Layer-event counts (Pull complete / Already exists / Download
complete / Pulling fs layer / Downloading) work across both stores.

Usage:
  parallel-pull.py [-c N] [-l LOG] [--lore TEXT]... [--max-attempts N]
                   [--pull-timeout SEC] <image|label>...

Each positional arg is "<image_ref>|<display_label>" — same format the bash
PULL_LIST uses. Exit code: 0 if every pull succeeded, 1 otherwise.

Non-TTY fallback: when stdout is not a terminal (CI, "| tee", etc.) the
dashboard is replaced with plain per-event lines so logs remain readable.

Why Python and not bash: a single foreground bash loop can't drive a
multi-row in-place dashboard while reading from N background pulls — their
stdouts would interleave on the same tty. Python's select(2) + non-blocking
reads give us one renderer thread fed by many subprocesses, no garbling.
"""

import argparse
import errno
import fcntl
import os
import re
import select
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# --- ANSI / styling ---------------------------------------------------------

HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
CLEAR_EOL = "\033[K"
MOVE_UP = "\033[A"
RESET = "\033[0m"
BOLD_GREEN = "\033[1;32m"
GREEN = "\033[0;32m"
DIM_GREEN = "\033[2;32m"
AMBER = "\033[1;33m"
RED = "\033[0;31m"

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# --- docker pull output parsing --------------------------------------------

# Examples we parse (non-TTY output — docker switches to one-line-per-event
# when stdout isn't a terminal, which is what we get from PIPE):
#   9f87c8ff3cb8: Pulling fs layer
#   9f87c8ff3cb8: Waiting
#   9f87c8ff3cb8: Downloading [==>] 60.12MB/120.5MB
#   9f87c8ff3cb8: Verifying Checksum
#   9f87c8ff3cb8: Download complete
#   9f87c8ff3cb8: Extracting [==>] 10.2MB/45.6MB
#   9f87c8ff3cb8: Pull complete
#   9f87c8ff3cb8: Already exists
#   Status: Downloaded newer image for ...
_LAYER = r"([a-f0-9]{6,64})"
# We deliberately do NOT parse the "X.YZMB / AB.CDMB" suffix on
# Downloading/Extracting lines: that format only appears reliably with
# the classic overlay2 image store. Docker's containerd snapshotter
# (default on Docker Desktop, opt-in on engine) emits bare events like
# "abc: Downloading" with no bytes, so any bytes-derived display
# (per-row %, combined throughput) silently shows 0 forever on those
# installs. Layer-event counting is the robust signal across stores.
PULLING_LAYER_RE = re.compile(rf"^{_LAYER}:\s+Pulling fs layer")
DOWNLOADING_RE = re.compile(rf"^{_LAYER}:\s+Downloading")
EXTRACTING_RE = re.compile(rf"^{_LAYER}:\s+Extracting")
DOWNLOAD_COMPLETE_RE = re.compile(rf"^{_LAYER}:\s+Download complete")
PULL_COMPLETE_RE = re.compile(rf"^{_LAYER}:\s+Pull complete")
ALREADY_EXISTS_RE = re.compile(rf"^{_LAYER}:\s+Already exists")

NON_RETRYABLE_RE = re.compile(
    r"(?i)\bunauthorized\b|\bdenied\b|\bnot[\s-]?found\b|\b404\b|"
    r"no space left on device|cannot connect to the docker daemon|"
    r"is the docker daemon running|manifest unknown"
)


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


# --- state ------------------------------------------------------------------

@dataclass
class Layer:
    # Three coarse states: seen (default) -> downloading -> done.
    # "done" covers Pull complete + Already exists + Download complete; we
    # don't distinguish further because the bytes view is gone.
    downloading: bool = False
    done: bool = False


@dataclass
class Job:
    img: str
    label: str
    log_path: str
    max_attempts: int
    proc: Optional[subprocess.Popen] = None
    layers: dict = field(default_factory=dict)
    status: str = "queued"  # queued | running | success | failed
    attempt: int = 0
    queued_time: float = 0.0
    start_time: float = 0.0
    end_time: float = 0.0
    error_reason: str = ""
    last_lines: list = field(default_factory=list)

    @property
    def layers_done(self) -> int:
        return sum(1 for L in self.layers.values() if L.done)

    @property
    def layers_seen(self) -> int:
        return len(self.layers)


# --- subprocess management --------------------------------------------------

def _set_nonblocking(fd: int) -> None:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def start_pull(job: Job) -> None:
    job.attempt += 1
    job.status = "running"
    if job.start_time == 0.0:
        job.start_time = time.monotonic()
    job.layers = {}
    job.last_lines = []
    job.proc = subprocess.Popen(
        ["docker", "pull", job.img],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    _set_nonblocking(job.proc.stdout.fileno())


def consume_output(job: Job) -> None:
    """Drain everything currently readable from job.proc.stdout."""
    if job.proc is None or job.proc.stdout is None:
        return
    fd = job.proc.stdout.fileno()
    buf = b""
    while True:
        r, _, _ = select.select([fd], [], [], 0)
        if not r:
            break
        try:
            chunk = os.read(fd, 65536)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                break
            raise
        if not chunk:
            break
        buf += chunk
    if not buf:
        return
    text = buf.decode("utf-8", errors="replace")
    fresh_lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        fresh_lines.append(line)
        _apply_line(job, line)
    job.last_lines.extend(fresh_lines)
    if len(job.last_lines) > 60:
        job.last_lines = job.last_lines[-60:]
    if job.log_path and fresh_lines:
        # Open/close per batch — keeps writes ordered when multiple jobs
        # flush at once. The OS append-write is atomic for line-sized
        # payloads, so prefixed lines never interleave mid-line.
        with open(job.log_path, "a") as f:
            for line in fresh_lines:
                f.write(f"[{job.label}] {line}\n")


def _apply_line(job: Job, line: str) -> None:
    # Check the terminal states first so "Download complete" doesn't get
    # demoted to "downloading" by the simpler Downloading regex below.
    for rx in (PULL_COMPLETE_RE, ALREADY_EXISTS_RE, DOWNLOAD_COMPLETE_RE):
        m = rx.match(line)
        if m:
            L = job.layers.setdefault(m.group(1), Layer())
            L.done = True
            return
    for rx in (PULLING_LAYER_RE,):
        m = rx.match(line)
        if m:
            job.layers.setdefault(m.group(1), Layer())
            return
    for rx in (DOWNLOADING_RE, EXTRACTING_RE):
        m = rx.match(line)
        if m:
            L = job.layers.setdefault(m.group(1), Layer())
            L.downloading = True
            return


def _is_non_retryable(job: Job) -> tuple[bool, str]:
    for line in job.last_lines:
        if NON_RETRYABLE_RE.search(line):
            return True, line[:200]
    return False, ""


def reap(job: Job) -> None:
    """Process exit observed — decide success / retry / fail."""
    if job.proc is None:
        return
    rc = job.proc.returncode
    if rc is None:
        # Should not happen — caller checks poll() first
        return
    job.proc = None
    if rc == 0:
        job.status = "success"
        job.end_time = time.monotonic()
        return
    non_retry, hint = _is_non_retryable(job)
    if non_retry:
        job.status = "failed"
        job.end_time = time.monotonic()
        job.error_reason = hint or "non-retryable error"
        return
    if job.attempt < job.max_attempts:
        job.status = "queued"
        job.queued_time = time.monotonic()
        job.error_reason = f"attempt {job.attempt} exit {rc}, retrying"
        return
    job.status = "failed"
    job.end_time = time.monotonic()
    job.error_reason = f"exit {rc} after {job.attempt} attempts"


# --- rendering --------------------------------------------------------------

LABEL_WIDTH = 38


class TTYRenderer:
    """In-place multi-line dashboard. Fixed N+3 rows per frame."""

    def __init__(self, lore: list):
        self.lore = lore or []
        self.lore_idx = 0
        self.frame = 0
        self.start = time.monotonic()
        self.last_drawn = 0
        self.last_lore_advance = self.start

    def _spinner(self) -> str:
        return SPINNER_FRAMES[self.frame % len(SPINNER_FRAMES)]

    def _row(self, j: Job) -> str:
        label = j.label[:LABEL_WIDTH].ljust(LABEL_WIDTH)
        if j.status == "success":
            dur = fmt_duration(j.end_time - j.start_time)
            return (f"  {BOLD_GREEN}✓{RESET} {label}  "
                    f"{DIM_GREEN}done in {dur}{RESET}")
        if j.status == "failed":
            return f"  {RED}✗{RESET} {label}  {RED}{j.error_reason[:60]}{RESET}"
        if j.status == "running":
            per_elapsed = fmt_duration(time.monotonic() - j.start_time)
            if j.layers_seen:
                progress = f"{j.layers_done}/{j.layers_seen} layers"
            else:
                progress = "starting…"
            attempt = ""
            if j.attempt > 1:
                attempt = f" {AMBER}(try {j.attempt}/{j.max_attempts}){RESET}"
            return (f"  {GREEN}{self._spinner()}{RESET} {label}  "
                    f"[{per_elapsed:>6}] {progress:>15}{attempt}")
        return f"  {DIM_GREEN}·{RESET} {label}  queued"

    def render(self, jobs: list) -> None:
        self.frame += 1
        completed = sum(1 for j in jobs if j.status in ("success", "failed"))
        total = len(jobs)
        elapsed = time.monotonic() - self.start
        running = sum(1 for j in jobs if j.status == "running")

        bar_width = 24
        filled = int(bar_width * completed / total) if total else 0
        bar = "█" * filled + "░" * (bar_width - filled)

        header = (
            f"  [{GREEN}{bar}{RESET}] {completed}/{total} modules · "
            f"{running} active · elapsed {fmt_duration(elapsed)}"
        )

        # Rotate lore on a wall-clock cadence so it doesn't speed up just
        # because we render at 4Hz instead of 1Hz.
        now = time.monotonic()
        if self.lore and now - self.last_lore_advance >= 8.0:
            self.lore_idx = (self.lore_idx + 1) % len(self.lore)
            self.last_lore_advance = now
        lore_line = ""
        if self.lore:
            lore_line = f"  {DIM_GREEN}« {self.lore[self.lore_idx]} »{RESET}"

        lines = [header]
        lines.extend(self._row(j) for j in jobs)
        lines.append("")
        lines.append(lore_line)

        # Redraw: move cursor up to top of previous frame, rewrite every
        # line with clear-to-EOL so leftovers (shorter labels, smaller %)
        # never bleed through.
        out = sys.stdout
        if self.last_drawn:
            out.write(MOVE_UP * self.last_drawn)
        for line in lines:
            out.write("\r" + line + CLEAR_EOL + "\n")
        # If a prior frame was taller, clear the trailing lines so we
        # don't leave ghost rows below.
        extra = self.last_drawn - len(lines)
        if extra > 0:
            for _ in range(extra):
                out.write("\r" + CLEAR_EOL + "\n")
            out.write(MOVE_UP * extra)
        self.last_drawn = len(lines)
        out.flush()

    def finalize(self) -> None:
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()


class PlainRenderer:
    """Non-TTY: emit one line per state transition. No cursor magic."""

    def __init__(self, _lore: list):
        self._seen_running: set = set()
        self._seen_done: set = set()

    def render(self, jobs: list) -> None:
        for j in jobs:
            key = id(j)
            if j.status == "running" and key not in self._seen_running:
                self._seen_running.add(key)
                print(f"  ▸ pulling {j.label} ({j.img})", flush=True)
            elif j.status == "success" and key not in self._seen_done:
                self._seen_done.add(key)
                dur = fmt_duration(j.end_time - j.start_time)
                print(f"  ✓ {j.label} — done in {dur}", flush=True)
            elif j.status == "failed" and key not in self._seen_done:
                self._seen_done.add(key)
                print(f"  ✗ {j.label} — {j.error_reason}", flush=True)

    def finalize(self) -> None:
        pass


# --- main loop --------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-c", "--concurrency", type=int, default=4,
                        help="max simultaneous docker pulls (default: 4)")
    parser.add_argument("-l", "--log-file", default="",
                        help="append all docker output to this file, prefixed by label")
    parser.add_argument("--lore", action="append", default=[],
                        help="lore string for the dashboard footer "
                             "(repeat to provide several; rotates every 8s)")
    parser.add_argument("--max-attempts", type=int, default=3,
                        help="retry budget per image (default: 3)")
    parser.add_argument("--pull-timeout", type=int, default=3600,
                        help="kill any single docker pull that exceeds this many seconds")
    parser.add_argument("entries", nargs="+",
                        help="<image_ref>|<display_label> tuples")
    args = parser.parse_args()

    jobs: list[Job] = []
    for entry in args.entries:
        if "|" in entry:
            img, label = entry.split("|", 1)
        else:
            img, label = entry, entry
        jobs.append(Job(img=img.strip(), label=label.strip(),
                        log_path=args.log_file,
                        max_attempts=args.max_attempts,
                        queued_time=time.monotonic()))

    is_tty = sys.stdout.isatty()
    renderer = TTYRenderer(args.lore) if is_tty else PlainRenderer(args.lore)

    if is_tty:
        sys.stdout.write(HIDE_CURSOR)
        sys.stdout.flush()

    def _shutdown(signum=None, _frame=None):
        for j in jobs:
            if j.proc and j.proc.poll() is None:
                try:
                    j.proc.terminate()
                except OSError:
                    pass
        renderer.finalize()
        if signum is not None:
            # Give children a brief moment to exit, then bail.
            time.sleep(0.5)
            sys.exit(128 + signum)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            running = [j for j in jobs if j.status == "running"]
            queued = [j for j in jobs if j.status == "queued"]
            while len(running) < args.concurrency and queued:
                nxt = queued.pop(0)
                start_pull(nxt)
                running.append(nxt)

            for j in running:
                consume_output(j)
                if j.proc is None:
                    continue
                rc = j.proc.poll()
                if rc is not None:
                    # Drain remaining output before reaping so we don't miss
                    # the "Pull complete" or error message that arrived in
                    # the same scheduling tick as exit.
                    consume_output(j)
                    reap(j)
                    continue
                if time.monotonic() - j.start_time > args.pull_timeout:
                    try:
                        j.proc.terminate()
                        j.proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        j.proc.kill()
                    except OSError:
                        pass
                    consume_output(j)
                    if j.proc is not None and j.proc.returncode is None:
                        # Force the reap path by stamping a returncode.
                        j.proc.returncode = 124
                    reap(j)
                    if j.status == "failed":
                        j.error_reason = (
                            f"timed out after {args.pull_timeout}s")

            renderer.render(jobs)

            if all(j.status in ("success", "failed") for j in jobs):
                break
            time.sleep(0.25)
    finally:
        renderer.finalize()

    print()  # leave dashboard intact, move cursor to fresh line
    failures = [j for j in jobs if j.status == "failed"]
    if failures:
        print(f"  {RED}{len(failures)} of {len(jobs)} pull(s) failed:{RESET}")
        for j in failures:
            print(f"    ✗ {j.label} — {j.error_reason}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
