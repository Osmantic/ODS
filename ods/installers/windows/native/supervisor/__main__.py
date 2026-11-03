# ============================================================================
# ODS Windows-native supervisor — entry point
# ============================================================================
# Part of: installers/windows/native/supervisor/
# Purpose: `python -m supervisor --config <path>`: read the installer's JSON
#          config, build the plan, and supervise until stopped.
#
# Expects: Windows (the Job Object is mandatory), the `native` directory on
#          sys.path (the installer lists it in the embeddable python312._pth).
# Provides: main(argv) -> exit code. 0 = clean stop (CTRL_BREAK, CTRL_C, or the
#           stop file run\supervisor.stop). Any fatal condition raises, logs a
#           traceback to logs\supervisor.log, and exits non-zero so Task
#           Scheduler restarts the supervisor.
#
# Modder notes:
#   Under pythonw there is no stderr, so uncaught exceptions are routed to the
#   supervisor log through sys.excepthook rather than caught here.
# ============================================================================
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import signal
import sys

from .plan import build_plan
from .runner import PopenLauncher, Supervisor, SystemClock, WindowsJob, http_probe, log


def _configure_logging(path):
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    if sys.stderr is not None:
        log.addHandler(logging.StreamHandler(sys.stderr))
    log.setLevel(logging.INFO)
    previous_hook = sys.excepthook

    def log_crash(exc_type, exc, tb):
        log.critical("supervisor crashed", exc_info=(exc_type, exc, tb))
        previous_hook(exc_type, exc, tb)

    sys.excepthook = log_crash


def _install_stop_signals(supervisor):
    def on_signal(signum, _frame):
        log.info("received signal %s", signum)
        supervisor.request_stop()

    names = ("SIGINT", "SIGTERM", "SIGBREAK")  # SIGBREAK = CTRL_BREAK_EVENT, Windows only
    for name in names:
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), on_signal)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m supervisor", description="ODS Windows-native process supervisor")
    parser.add_argument("--config", required=True, help="path to the supervisor config JSON written by the installer")
    args = parser.parse_args(argv)
    with open(args.config, encoding="utf-8") as handle:
        plan = build_plan(json.load(handle))
    os.makedirs(plan.log_dir, exist_ok=True)
    os.makedirs(plan.run_dir, exist_ok=True)
    _configure_logging(plan.supervisor_log)
    log.info("supervisor starting (pid %s) with %s", os.getpid(), args.config)

    job = WindowsJob()
    supervisor = Supervisor(plan, launcher=PopenLauncher(plan.log_max_bytes, plan.log_backups),
                            job=job, probe=http_probe, clock=SystemClock())
    _install_stop_signals(supervisor)
    try:
        return supervisor.run()
    finally:
        # Children were stopped by run(); closing the job kills anything they left behind.
        job.close()


if __name__ == "__main__":
    raise SystemExit(main())
