#!/usr/bin/env python3
"""Keep every test either executed by CI or listed with a reason.

CI runs tests by explicit path, so a test that no runner executes never runs and
silently rots. A test under ods/tests or ods/extensions/services/*/tests counts as
covered only when CI executes it:

- a workflow step runs it (bash/sh without -n, python on the file, pytest,
  unittest, node, bats, pwsh, or the script itself), names a directory whose
  runner discovers it (pytest collects test_*.py and *_test.py), or a matching
  glob, resolved against the step's working directory with matrix values expanded;
- it is listed in ods/tests/ci-suite.txt, which the Extended contract suite runs;
- a script that CI executes runs it, transitively.

The Makefile, lint or syntax checks (`bash -n`, shellcheck, py_compile) and mere
mentions in comments or prose do not count; no workflow runs `make`. A test that
is not executed must be recorded, with a reason, in ods/tests/ci-not-run.txt.

  list-unwired-tests.py           report tests that CI does not execute
  list-unwired-tests.py --check   fail on tests that are neither executed nor recorded
"""
import fnmatch
import posixpath
import re
import shlex
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
SUITE = ROOT / 'ods/tests/ci-suite.txt'
NOT_RUN = ROOT / 'ods/tests/ci-not-run.txt'
WRAPPERS = {'sudo', 'env', 'time', 'exec', 'command', 'nice'}
SCRIPT_SUFFIXES = ('.sh', '.py', '.mjs', '.js', '.cjs', '.bats', '.ps1')


# ---------------------------------------------------------------- command parsing

def _resolve(wd, token):
    token = token.strip('"\'')
    if token.startswith('$') and '/' in token:
        # "$SCRIPT_DIR/test-x.sh", "$ROOT/tests/*.bats": resolved by the caller against
        # the script's directory, ods/ and the repository root.
        rest = token.split('/', 1)[1]
        return 'VAR:' + rest if rest.endswith(SCRIPT_SUFFIXES) or '*' in rest else None
    if not token or token.startswith(('-', '$')):
        return None
    base = '' if token.startswith(('ods/', '.github/', '/')) else wd
    return posixpath.normpath(posixpath.join(base, token)) if base else posixpath.normpath(token)


def _segments(text):
    for line in text.replace('\\\n', ' ').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        for segment in re.split(r'&&|\|\||;|\|', stripped):
            if segment.strip():
                yield segment.strip()


def _words(segment):
    try:
        words = shlex.split(segment, comments=True)
    except ValueError:
        words = segment.split()
    while words and (re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', words[0]) or words[0] in WRAPPERS
                     or words[0] in ('if', 'then', 'else', 'do', 'while', '!', '(', '{')):
        if words[0] == 'env':
            words = words[1:]
            while words and (words[0].startswith('-') or '=' in words[0]):
                words = words[1:]
            continue
        words = words[1:]
    if words and words[0] == 'timeout':
        words = words[1:]
        while words and (words[0].startswith('-') or re.match(r'^\d+[smh]?$', words[0])):
            words = words[1:]
    return words


def _executed_args(words):
    """(kind, args, bare): the runner, the tokens it executes, and whether it ran bare."""
    if not words:
        return None, [], False
    cmd, args = words[0], words[1:]
    located = re.fullmatch(r'\$\((?:command -v|which)\s+(\S+)\)', cmd)
    if located:
        cmd = located.group(1)
    interpreter = re.fullmatch(r'\$\{?([A-Za-z_]*?(PYTHON|PY|BASH|NODE)[A-Za-z0-9_]*)\}?', cmd)
    if interpreter:
        cmd = {'PYTHON': 'python3', 'PY': 'python3', 'BASH': 'bash', 'NODE': 'node'}[interpreter.group(2)]
    name = posixpath.basename(cmd)
    if name in ('bash', 'sh', 'zsh'):
        if '-n' in args or '-c' in args:
            return None, [], False
        return 'bash', [a for a in args if not a.startswith('-')][:1], False
    if re.fullmatch(r'python(3(\.\d+)?)?', name):
        if args[:1] == ['-m'] and len(args) > 1:
            module, rest = args[1], args[2:]
            if module == 'pytest':
                targets = [a for a in rest if not a.startswith('-')]
                return 'pytest', targets, not targets
            if module == 'unittest':
                if rest[:1] == ['discover']:
                    if '-s' in rest:
                        return 'unittest', [rest[rest.index('-s') + 1]], False
                    return 'unittest', [], True
                return 'unittest', [a for a in rest if not a.startswith('-')], False
            return None, [], False
        if '-c' in args:
            return None, [], False
        return 'python', [a for a in args if not a.startswith('-')][:1], False
    if name == 'pytest':
        targets = [a for a in args if not a.startswith('-')]
        return 'pytest', targets, not targets
    if name == 'node':
        targets = [a for a in args if not a.startswith('-')]
        if '--test' in args:
            return 'node', targets, not targets
        return 'exec', [t for t in targets[:1] if t.endswith(('.mjs', '.js', '.cjs'))], False
    if name == 'bats':
        return 'bats', [a for a in args if not a.startswith('-')], False
    if name in ('pwsh', 'powershell'):
        if '-File' in args:
            return 'pwsh', [args[args.index('-File') + 1]], False
        return 'pwsh', [a for a in args if not a.startswith('-') and a.endswith('.ps1')][:1], False
    if name == 'npm' and (args[:1] == ['test'] or args[:2] == ['run', 'test']):
        return 'npm', [], True
    if cmd.startswith(('./', '../', 'ods/', 'tests/')) and cmd.endswith(SCRIPT_SUFFIXES):
        return 'exec', [cmd], False
    return None, [], False


def executed(text, wd=''):
    """(kind, target) pairs a shell snippet executes, resolved against wd."""
    found = set()
    for loop in re.finditer(r'for\s+(\w+)\s+in\s+([^;\n]+?)\s*;?\s*(?:\n\s*)?do\b(.*?)\bdone\b', text, re.S):
        var, items, body = loop.groups()
        runner = re.search(r'\b(bash|sh|python3?|pytest|node\s+--test)\s+[^\n]*["\']?\$\{?' + var + r'\b', body)
        if runner:
            kind = {'sh': 'bash', 'python3': 'python'}.get(runner.group(1), runner.group(1).split()[0])
            for item in items.split():
                resolved = _resolve(wd, item)
                if resolved:
                    found.add((kind, resolved))
    for segment in _segments(text):
        kind, args, bare = _executed_args(_words(segment))
        if kind is None:
            continue
        if bare and wd:
            found.add((kind, wd))
        for arg in args:
            resolved = _resolve(wd, arg)
            if resolved:
                found.add((kind, resolved))
    return found


def _discovers(kind, path):
    name = posixpath.basename(path)
    if kind == 'pytest':
        return bool(re.fullmatch(r'test_.*\.py|.*_test\.py', name))
    if kind == 'unittest':
        return bool(re.fullmatch(r'test[A-Za-z0-9_]*\.py', name))
    if kind == 'node':
        return bool(re.fullmatch(r'.*[._-]test\.[cm]?js|test-.*\.[cm]?js|test\.[cm]?js', name))
    if kind == 'npm':
        return bool(re.search(r'\.test\.(m?js|jsx|ts|tsx)$', name))
    if kind == 'bats':
        return name.endswith('.bats')
    return False


def runs(kind, target, path):
    """Whether a run of `target` by `kind` executes `path`."""
    if target == path:
        return True
    if '*' in target:
        return fnmatch.fnmatch(path, target)
    if path.startswith(target.rstrip('/') + '/'):
        return _discovers(kind, path)
    return False


# ---------------------------------------------------------------- the inventory

def matrix_values(job, key):
    matrix = (job.get('strategy') or {}).get('matrix') or {}
    values = list(matrix.get(key)) if isinstance(matrix.get(key), list) else []
    values += [entry[key] for entry in matrix.get('include') or [] if isinstance(entry, dict) and key in entry]
    return [str(value) for value in values]


def expand(job, text):
    """Expand ${{ matrix.KEY }}; text with any other expression is dropped."""
    keys = re.findall(r'\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}', text)
    if not keys:
        return [] if '${{' in text else [text]
    results = [text]
    for key in set(keys):
        pattern = r'\$\{\{\s*matrix\.' + re.escape(key) + r'\s*\}\}'
        results = [re.sub(pattern, value, r) for r in results for value in matrix_values(job, key)]
    return [r for r in results if '${{' not in r]


def listed(path):
    return ['ods/' + line.split()[0] for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.strip().startswith('#')]


def inventory(root=ROOT):
    """(tests, covered): every tracked test file and the predicate for CI execution."""
    import yaml  # the inventory job installs PyYAML

    files = subprocess.check_output(['git', 'ls-files'], cwd=root, text=True).split()
    tests = [p for p in files
             if (p.startswith('ods/tests/') and '/fixtures/' not in p
                 and re.search(r'/test[-_][^/]*\.(sh|py|mjs|bats)$', p))
             or (re.match(r'ods/extensions/services/[^/]+/tests/', p)
                 and '/fixtures/' not in p and '/node_modules/' not in p
                 and re.search(r'(/test[-_][^/]*\.(py|sh|mjs|js))$|(\.test\.(mjs|js|ts|jsx|tsx))$|(_test\.py)$', p))]
    by_name = defaultdict(list)
    for path in files:
        by_name[Path(path).name].append(path)
    found = set()

    def absorb(result, base=''):
        for kind, target in result:
            if target.startswith('VAR:'):
                rest = target[4:]
                for prefix in {base, 'ods', ''}:
                    found.add((kind, f'{prefix}/{rest}' if prefix else rest))
                if '*' not in rest:
                    found.update((kind, path) for path in by_name.get(Path(rest).name, []))
            else:
                found.add((kind, target))

    for path in files:
        if path.startswith('.github/workflows/') and path.endswith(('.yml', '.yaml')):
            document = yaml.safe_load((root / path).read_text(encoding='utf-8')) or {}
            for job in (document.get('jobs') or {}).values():
                default_wd = ((job.get('defaults') or {}).get('run') or {}).get('working-directory') or ''
                for step in job.get('steps') or []:
                    run = re.sub(r'\$\{?GITHUB_WORKSPACE\}?/', '', step.get('run') or '')
                    for wd in expand(job, step.get('working-directory') or default_wd):
                        for text in expand(job, run):
                            absorb(executed(text, wd.rstrip('/')))
    found.update(('suite', path) for path in listed(root / 'ods/tests/ci-suite.txt'))

    def covered(path):
        return any(runs(kind, target, path) for kind, target in found)

    runners = [p for p in files if p.endswith(('.sh', '.py', '.bats'))
               and p.startswith(('ods/', '.github/scripts/'))]
    texts, done, changed = {}, set(), True
    while changed:
        changed = False
        for runner in runners:
            if runner in done or not covered(runner):
                continue
            done.add(runner)
            text = texts.setdefault(runner, (root / runner).read_text(encoding='utf-8', errors='replace'))
            directory = posixpath.dirname(runner)
            before = len(found)
            # A script's relative paths depend on the directory it changes into:
            # usually ods/ or its own directory.
            for base in {directory, 'ods', ''}:
                absorb(executed(text, base), directory)
            changed = changed or len(found) != before
    return tests, covered


def main():
    tests, covered = inventory()
    unexecuted = [t for t in tests if not covered(t)]
    if '--check' not in sys.argv:
        print(f'{len(tests)} tests; {len(unexecuted)} not executed by CI')
        for test in unexecuted:
            print('  ' + test)
        return 0
    suite, not_run = listed(SUITE), listed(NOT_RUN)
    problems = []
    for entry in suite:
        if entry not in tests or not entry.startswith('ods/tests/'):
            problems.append(f'{entry} is in ci-suite.txt but is not a tracked test under ods/tests')
    for entry in not_run:
        if entry not in tests:
            problems.append(f'{entry} is in ci-not-run.txt but is not a tracked test file')
        elif covered(entry):
            problems.append(f'{entry} is in ci-not-run.txt but CI executes it; remove the entry')
        line = next(line for line in NOT_RUN.read_text(encoding='utf-8').splitlines()
                    if line.strip().startswith(entry.removeprefix('ods/')))
        if len(line.split(None, 1)) < 2:
            problems.append(f'{entry} has no reason in ci-not-run.txt')
    for entry in set(suite) & set(not_run):
        problems.append(f'{entry} is listed in both ci-suite.txt and ci-not-run.txt')
    for test in unexecuted:
        if test not in not_run:
            problems.append(f'{test} is not executed by CI: run it from a workflow step or '
                            'ods/tests/ci-suite.txt, or record why in ods/tests/ci-not-run.txt')
    if problems:
        print('\n'.join('[FAIL] ' + problem for problem in problems))
        return 1
    print(f'[PASS] {len(tests)} tests: {len(tests) - len(unexecuted)} executed by CI '
          f'({len(suite)} through ci-suite.txt), {len(not_run)} recorded as not run')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
