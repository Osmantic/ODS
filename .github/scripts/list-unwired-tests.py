#!/usr/bin/env python3
"""List ods/tests files that no CI workflow, Makefile or runner script executes.

CI runs tests by explicit path, so a test that is never named anywhere never
runs. With --paths, print one repository-relative path per line.
"""
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

files = subprocess.check_output(['git', 'ls-files'], text=True).split()
tests = [p for p in files
         if p.startswith('ods/tests/') and '/fixtures/' not in p
         and re.search(r'/test[-_][^/]*\.(sh|py|mjs|bats)$', p)]
runner_files = [p for p in files
                if p.startswith('.github/workflows/') or p == 'ods/Makefile'
                or (p.startswith(('ods/tests/', 'ods/scripts/', '.github/scripts/'))
                    and p.endswith(('.sh', '.py', '.mjs'))
                    and not p.startswith('.github/scripts/list-unwired-tests'))]
texts = {p: Path(p).read_text(encoding='utf-8', errors='replace') for p in runner_files}

# Directory runs and globs used by runners (relative to ods/ or the repo root).
patterns = set()
for text in texts.values():
    for match in re.finditer(r'(?:ods/)?tests/[A-Za-z0-9_./*-]+', text):
        patterns.add(match.group(0).removeprefix('ods/').rstrip('.'))


def wired(test):
    rel = test.removeprefix('ods/')
    name = Path(test).name
    for pattern in patterns:
        if pattern == rel or fnmatch.fnmatch(rel, pattern):
            return True
        if not Path(pattern).suffix and rel.startswith(pattern.rstrip('/') + '/'):
            return True  # a directory run such as `pytest tests/pixel_inference`
    # Referenced by bare name from another runner (excluding the test itself).
    return any(name in text for path, text in texts.items() if path != test)


unwired = [t for t in tests if not wired(t)]
if '--paths' in sys.argv:
    print('\n'.join(unwired))
else:
    print(f'{len(tests)} tests; {len(unwired)} not run by any workflow, Makefile or runner')
    for t in unwired:
        print('  ' + t)
