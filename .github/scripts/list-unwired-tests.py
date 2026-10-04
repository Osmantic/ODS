#!/usr/bin/env python3
"""Keep every test under ods/tests either running in CI or listed with a reason.

CI runs tests by explicit path, so a test that no runner names never runs and
silently rots. A test counts as covered when a workflow, the Makefile or a
runner script names it (directly, by directory or by glob), when it is listed in
ods/tests/ci-suite.txt, or when ods/tests/ci-not-run.txt records why it is not run.

  list-unwired-tests.py           report tests that no runner names
  list-unwired-tests.py --check   fail on tests that are neither covered nor listed
"""
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
SUITE = ROOT / 'ods/tests/ci-suite.txt'
NOT_RUN = ROOT / 'ods/tests/ci-not-run.txt'

files = subprocess.check_output(['git', 'ls-files'], cwd=ROOT, text=True).split()
tests = [p for p in files
         if p.startswith('ods/tests/') and '/fixtures/' not in p
         and re.search(r'/test[-_][^/]*\.(sh|py|mjs|bats)$', p)]
runner_files = [p for p in files
                if p.startswith('.github/workflows/') or p == 'ods/Makefile'
                or (p.startswith(('ods/tests/', 'ods/scripts/', '.github/scripts/'))
                    and p.endswith(('.sh', '.py', '.mjs'))
                    and p != '.github/scripts/list-unwired-tests.py')]
texts = {p: (ROOT / p).read_text(encoding='utf-8', errors='replace') for p in runner_files}

patterns = set()
for text in texts.values():
    for match in re.finditer(r'(?:ods/)?tests/[A-Za-z0-9_./*-]+', text):
        patterns.add(match.group(0).removeprefix('ods/').rstrip('.'))


def named_by_runner(test):
    rel = test.removeprefix('ods/')
    name = Path(test).name
    for pattern in patterns:
        if pattern == rel or fnmatch.fnmatch(rel, pattern):
            return True
        if not Path(pattern).suffix and rel.startswith(pattern.rstrip('/') + '/'):
            return True  # a directory run such as `pytest tests/pixel_inference`
    return any(name in text for path, text in texts.items() if path != test)


def listed(path):
    entries = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            entries.append('ods/' + line.split()[0])
    return entries


unwired = [t for t in tests if not named_by_runner(t)]
if '--check' not in sys.argv:
    print(f'{len(tests)} tests; {len(unwired)} not named by any workflow, Makefile or runner')
    for t in unwired:
        print('  ' + t)
    sys.exit(0)

suite, not_run = listed(SUITE), listed(NOT_RUN)
problems = []
for entry in suite + not_run:
    if entry not in tests:
        problems.append(f'{entry} is listed but is not a tracked test file')
for entry in set(suite) & set(not_run):
    problems.append(f'{entry} is listed in both ci-suite.txt and ci-not-run.txt')
for entry in not_run:
    reason = next(line for line in NOT_RUN.read_text(encoding='utf-8').splitlines()
                  if line.strip().startswith(entry.removeprefix('ods/')))
    if len(reason.split(None, 1)) < 2:
        problems.append(f'{entry} has no reason in ci-not-run.txt')
for test in unwired:
    if test not in suite and test not in not_run:
        problems.append(f'{test} is not run by CI: add it to a workflow, ods/tests/ci-suite.txt '
                        'or, with a reason, ods/tests/ci-not-run.txt')
if problems:
    print('\n'.join('[FAIL] ' + p for p in problems))
    sys.exit(1)
print(f'[PASS] {len(tests)} tests: {len(tests) - len(unwired)} named by CI runners, '
      f'{len(suite)} in ci-suite.txt, {len(not_run)} recorded as not run')
