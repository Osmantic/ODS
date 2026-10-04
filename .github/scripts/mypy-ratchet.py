#!/usr/bin/env python3
"""Fail CI on new mypy errors without requiring the existing ones to be fixed first.

Reads mypy's output for one target on stdin and compares it with the reviewed
baseline. An error is identified by file, error code and message, not by line
number, so edits that only move existing errors do not fail. A count above the
baseline fails; a count below it is reported so the baseline can be lowered.

  mypy-ratchet.py --target NAME < mypy-output.txt           compare
  mypy-ratchet.py --target NAME --update < mypy-output.txt  rewrite NAME's baseline
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

BASELINE = Path(__file__).resolve().parents[1] / 'mypy-baseline.json'
ERROR = re.compile(r'^(?P<path>[^:\n]+):(?P<line>\d+)(?::\d+)?: error: (?P<message>.*?)(?:  \[(?P<code>[a-z0-9-]+)\])?$')


def errors(text):
    """Yield (key, path, line, message) for each mypy error line."""
    for line in text.splitlines():
        match = ERROR.match(line.rstrip())
        if not match:
            continue
        path = match['path'].replace('\\', '/')
        # Some messages cite other lines ("defined on line 45"); edits move those too.
        message = re.sub(r'\bline \d+\b', 'line N', match['message'])
        yield f"{path} [{match['code'] or 'misc'}] {message}", path, int(match['line']), match['message']


def compare(found, allowed):
    """Return (new, fixed): keys whose count rose above, or fell below, the baseline."""
    new = {key: count - allowed.get(key, 0) for key, count in found.items() if count > allowed.get(key, 0)}
    fixed = {key: count - found.get(key, 0) for key, count in allowed.items() if count > found.get(key, 0)}
    return new, fixed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--target', required=True)
    parser.add_argument('--baseline', type=Path, default=BASELINE)
    parser.add_argument('--prefix', default='', help="mypy's working directory, for annotation paths")
    parser.add_argument('--update', action='store_true')
    args = parser.parse_args()

    text = sys.stdin.read()
    parsed = list(errors(text))
    found = Counter(key for key, *_ in parsed)
    summary = re.search(r'^Found (\d+) errors? in', text, re.MULTILINE)
    if summary and int(summary.group(1)) != len(parsed):
        raise SystemExit(f'Parsed {len(parsed)} errors but mypy reported {summary.group(1)}; fix the parser')

    baseline = json.loads(args.baseline.read_text(encoding='utf-8')) if args.baseline.exists() else {}
    if args.update:
        baseline[args.target] = dict(sorted(found.items()))
        args.baseline.write_text(json.dumps(baseline, indent=1, sort_keys=True) + '\n', encoding='utf-8',
                                 newline='\n')
        print(f'{args.target}: baseline set to {len(parsed)} errors')
        return 0

    if args.target not in baseline:
        raise SystemExit(f'No baseline for {args.target}; add one with --update')
    new, fixed = compare(found, baseline[args.target])
    prefix = args.prefix.rstrip('/') + '/' if args.prefix else ''
    for key, path, line, message in parsed:
        if new.get(key):
            print(f'::error file={prefix}{path},line={line}::{message}')
    print(f'{args.target}: {len(parsed)} errors; {sum(new.values())} new, '
          f'{sum(fixed.values())} fixed (baseline {sum(baseline[args.target].values())})')
    if fixed:
        print(f'{args.target}: fewer errors than the baseline. Lower it with --update '
              f'so they cannot come back:')
        for key in sorted(fixed):
            print(f'  fixed: {key}')
    if new:
        print(f'{args.target}: new mypy errors. Fix them; the baseline only records reviewed debt.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
