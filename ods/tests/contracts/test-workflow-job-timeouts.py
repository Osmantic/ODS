#!/usr/bin/env python3
"""Contract: every job in every CI workflow must declare timeout-minutes.

A job without a timeout inherits GitHub's 360-minute default, so a single
wedged step (hung test, stalled container pull, deadlocked script) burns up
to six hours of runner time and delays every signal behind it. Several jobs
already set timeout-minutes; this guards the convention repo-wide and fails
if a new or existing job drops the bound.
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

JOB_KEY = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
JOB_PROP = re.compile(r"^    [A-Za-z_-]+:")

failures = []
total_jobs = 0

for wf in sorted(WORKFLOWS.glob("*.y*ml")):
    in_jobs = False
    job = None
    job_has_timeout = False

    def flush():
        if job is not None and not job_has_timeout:
            failures.append(f"{wf.name}: job '{job}' has no timeout-minutes")

    for line in wf.read_text().splitlines():
        if line == "jobs:":
            in_jobs = True
            continue
        if not in_jobs:
            continue
        m = JOB_KEY.match(line)
        if m:
            flush()
            job = m.group(1)
            job_has_timeout = False
            total_jobs += 1
            continue
        if job is not None and JOB_PROP.match(line) and "timeout-minutes" in line:
            job_has_timeout = True
    flush()

print(f"checked {total_jobs} jobs across {len(list(WORKFLOWS.glob('*.y*ml')))} workflows")
for f in failures:
    print(f"FAIL: {f}")
if failures:
    sys.exit(1)
print("all jobs declare timeout-minutes")
