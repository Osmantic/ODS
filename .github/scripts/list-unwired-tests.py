#!/usr/bin/env python3
"""Keep every test either running in CI or listed with a reason.

CI runs tests by explicit path, so a test that no runner names never runs and
silently rots. A test under ods/tests counts as covered when a workflow, the
Makefile or a runner script names it (directly, by directory or by glob), when it
is listed in ods/tests/ci-suite.txt, or when ods/tests/ci-not-run.txt records why
it is not run.

A test under ods/extensions/services/*/tests counts as covered when a workflow
step's test command (pytest, node --test, npm test, bash or python on the file)
names it, its directory or a matching glob, resolved against the step's working
directory (including matrix-expanded ones), or when ci-not-run.txt records it.

  list-unwired-tests.py           report tests that no runner names
  list-unwired-tests.py --check   fail on tests that are neither covered nor listed
"""
import fnmatch
import posixpath
import re
import shlex
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
service_tests = [p for p in files
                 if re.match(r'ods/extensions/services/[^/]+/tests/', p)
                 and '/fixtures/' not in p and '/node_modules/' not in p
                 and re.search(r'(/test[-_][^/]*\.(py|sh|mjs|js))$|(\.test\.(mjs|js|ts|jsx|tsx))$|(_test\.py)$', p)]
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
        directory = pattern.rstrip('/')
        # A directory run names a subdirectory, such as `pytest tests/pixel_inference`;
        # a bare `tests/` in prose or a comment is not a runner.
        if '/' in directory and not Path(directory).suffix and rel.startswith(directory + '/'):
            return True
    return any(name in text for path, text in texts.items() if path != test)


TEST_COMMAND = re.compile(r'pytest|unittest|node\s+--test|npm\s+(run\s+)?test|vitest|\bbash\s|\bpython3?\s+[^-\s]')
COMMAND_WORDS = {'python', 'python3', 'pytest', 'node', 'bash', 'npm', 'run', 'test', 'sudo', 'env', 'timeout'}


def matrix_values(job, key):
    """Values a job's matrix gives `key`, from list axes and include entries."""
    matrix = (job.get('strategy') or {}).get('matrix') or {}
    values = list(matrix.get(key) or []) if isinstance(matrix.get(key), list) else []
    values += [entry[key] for entry in matrix.get('include') or [] if isinstance(entry, dict) and key in entry]
    return [str(value) for value in values]


def expand(job, text):
    """Expand ${{ matrix.KEY }} in text; anything else unresolvable yields nothing."""
    keys = re.findall(r'\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}', text)
    if not keys:
        return [] if '${{' in text else [text]
    results = [text]
    for key in set(keys):
        values = matrix_values(job, key)
        results = [r.replace(m, value) for r in results for value in values
                   for m in set(re.findall(r'\$\{\{\s*matrix\.' + re.escape(key) + r'\s*\}\}', r))]
    return [r for r in results if '${{' not in r]


def service_coverage():
    """(paths, directories, globs) named by workflow test commands, repo-relative."""
    import yaml  # the inventory job installs PyYAML

    paths, directories, globs = set(), set(), set()
    for path in files:
        if not (path.startswith('.github/workflows/') and path.endswith(('.yml', '.yaml'))):
            continue
        document = yaml.safe_load(texts[path]) or {}
        for job in (document.get('jobs') or {}).values():
            default_wd = ((job.get('defaults') or {}).get('run') or {}).get('working-directory') or ''
            for step in job.get('steps') or []:
                run = step.get('run') or ''
                for wd in expand(job, step.get('working-directory') or default_wd):
                    wd = wd.rstrip('/')
                    for line in run.replace('\\\n', ' ').splitlines():
                        if 'pip install' in line or not TEST_COMMAND.search(line):
                            continue
                        for line_text in expand(job, line):
                            try:
                                tokens = shlex.split(line_text, comments=True)
                            except ValueError:
                                tokens = line_text.split()
                            named = []
                            for token in tokens:
                                if token.startswith(('-', '$')) or token in COMMAND_WORDS:
                                    continue
                                if '/' not in token and not token.endswith(('.py', '.mjs', '.js', '.sh')) and token != 'tests':
                                    continue
                                base = '' if token.startswith(('ods/', '.github/')) else wd
                                named.append(posixpath.normpath(posixpath.join(base, token)))
                            for item in named:
                                (globs if '*' in item else paths).add(item)
                            if not named and wd and re.search(r'pytest|npm\s+(run\s+)?test|vitest', line_text):
                                directories.add(wd)
    return paths, directories, globs


def service_test_run(test, coverage):
    paths, directories, globs = coverage
    return (test in paths or any(test.startswith(d + '/') for d in paths | directories)
            or any(fnmatch.fnmatch(test, g) for g in globs))


def listed(path):
    entries = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            entries.append('ods/' + line.split()[0])
    return entries


coverage = service_coverage()
unwired = [t for t in tests if not named_by_runner(t)]
unwired_services = [t for t in service_tests if not service_test_run(t, coverage)]
if '--check' not in sys.argv:
    print(f'{len(tests)} tests; {len(unwired)} not named by any workflow, Makefile or runner')
    for t in unwired:
        print('  ' + t)
    print(f'{len(service_tests)} service tests; {len(unwired_services)} not run by any workflow')
    for t in unwired_services:
        print('  ' + t)
    sys.exit(0)

suite, not_run = listed(SUITE), listed(NOT_RUN)
problems = []
for entry in suite:
    if entry not in tests:
        problems.append(f'{entry} is listed but is not a tracked test file')
for entry in not_run:
    if entry not in tests and entry not in service_tests:
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
for test in unwired_services:
    if test not in not_run:
        problems.append(f'{test} is not run by CI: run it from a workflow (for a whole service, '
                        '.github/workflows/test-service-suites.yml) or, with a reason, list it in '
                        'ods/tests/ci-not-run.txt')
if problems:
    print('\n'.join('[FAIL] ' + p for p in problems))
    sys.exit(1)
print(f'[PASS] {len(tests)} tests: {len(tests) - len(unwired)} named by CI runners, '
      f'{len(suite)} in ci-suite.txt, {len(not_run)} recorded as not run; '
      f'{len(service_tests)} service tests: {len(service_tests) - len(unwired_services)} run by workflows')
