#!/usr/bin/env python3
"""Keep owner-private and fleet-specific text out of what every user receives.

Pixel copies its workspace template into each new workspace and reads those
files as instructions; the agent and stack templates are offered to every user.
None of them may carry a maintainer's name, test machines, private workflow
terms, personal home paths or LAN addresses.

Pixel 4.3.29 removed the retired block that earlier templates carried in
AGENTS.md and MEMORY.md (see vendor/PIXEL-SOURCE-PROVENANCE.md). The installer's
guidance migration (installers/lib/pixel-workspace-guidance.py) still removes it
from workspaces created from older releases. The shipped template must already
be what that migration produces: it fails here if the migration would change it.
"""
import base64
import importlib.util
import os
from pathlib import Path
import re
import sys

ODS = Path(__file__).resolve().parents[1]
SEEDED = ('vendor/pixel/workspace-template', 'agents/templates', 'templates')
MIGRATED = {'vendor/pixel/workspace-template/AGENTS.md', 'vendor/pixel/workspace-template/MEMORY.md'}

# Names are decoded at run time, as in the retired-name guard, so this file
# does not itself spread the text it rejects.
MAINTAINER = base64.b64decode('bWljaGFlbA==').decode('ascii')
MARKERS = {
    'maintainer name': re.compile(r'\b' + MAINTAINER + r'\b', re.IGNORECASE),
    'fleet host': re.compile(r'\b(?:tower[\s_-]?[0-9]|strixy)\b', re.IGNORECASE),
    'private model workflow': re.compile(r'\bdsv4\b|local-first operating contract|local-work-ledger', re.IGNORECASE),
    'personal home path': re.compile(r'/home/(?!agent/|user/|node/|ubuntu/)[A-Za-z][\w.-]*/'
                                     r'|/Users/(?!Shared/)[A-Za-z][\w.-]*/'
                                     r'|\b[A-Za-z]:\\Users\\(?!Public\b)[A-Za-z][\w.-]*'),
    'LAN address': re.compile(r'\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b(?!/)'),
}


def load_guidance():
    spec = importlib.util.spec_from_file_location(
        'workspace_guidance', ODS / 'installers/lib/pixel-workspace-guidance.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def findings(text):
    for number, line in enumerate(text.splitlines(), 1):
        for label, pattern in MARKERS.items():
            if pattern.search(line):
                yield number, label


def self_test():
    positive = [MAINTAINER.title() + "'s settled procedure", 'Run it on Tower2', 'the strixy lane',
                'DSV4 leads', 'Local-First Operating Contract', 'pixel-local-work-ledger',
                '/home/alex/notes', '/Users/alex/src', r'C:\Users\alex\ods', 'http://192.168.1.40:3000',
                '10.0.4.17', '172.20.1.9']
    negative = ['Tower defense', '/home/agent/.openclaw', '/Users/Shared/ods', r'C:\Users\Public',
                'block 10.0.0.0/8 and 192.168.0.0/16', 'version 10.1.2', 'localhost:3000']
    if not all(list(findings(sample)) for sample in positive):
        raise SystemExit('[FAIL] seeded-content guard misses a private marker form')
    if any(list(findings(sample)) for sample in negative):
        raise SystemExit('[FAIL] seeded-content guard rejects ordinary product text')


def main():
    self_test()
    guidance = load_guidance()
    failures, checked = [], 0
    for top in SEEDED:
        for parent, _directories, names in os.walk(ODS / top):
            for name in sorted(names):
                path = Path(parent) / name
                relative = path.relative_to(ODS).as_posix()
                body = path.read_bytes()
                if relative in MIGRATED:
                    status = guidance.transform(name, body)[1]
                    if status not in ('current', 'unchanged'):
                        failures.append(f'{relative}: the installer migration would change the shipped '
                                        f'template ({status}); the retired block must not return')
                        continue
                checked += 1
                for number, label in findings(body.decode('utf-8', errors='replace')):
                    failures.append(f'{relative}:{number}: {label}')
    if failures:
        print('[FAIL] owner-private or fleet-specific text in seeded user content:')
        for failure in failures:
            print('  ' + failure)
        sys.exit(1)
    print(f'[PASS] {checked} seeded files carry no owner-private or fleet-specific text')


if __name__ == '__main__':
    main()
