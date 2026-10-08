#!/usr/bin/env python3
"""Validate every extension library recipe the way ODS composes it.

Each recipe, alone and with each GPU overlay, is checked with `docker compose
config` on top of the core stack (docker-compose.base.yml) plus the compose files
of the services its manifest depends on, transitively. The ${VAR:?...} secrets
the Dashboard generates when it installs a recipe get placeholder values.
`docker compose config` checks structure, references and interpolation; it does
not pull or run anything.
"""
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml

ODS = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()) / 'ods'
LIBRARY = ODS / 'extensions/library/services'
BUNDLED = ODS / 'extensions/services'
REQUIRED = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*):\?')


def service_dir(name):
    for root in (LIBRARY, BUNDLED):
        if (root / name / 'compose.yaml').is_file():
            return root / name
    return None


def dependencies(directory, seen=None):
    """Service directories this one depends on, transitively, in a stable order."""
    seen = set() if seen is None else seen
    manifest = directory / 'manifest.yaml'
    if not manifest.is_file():
        return []
    service = (yaml.safe_load(manifest.read_text(encoding='utf-8')) or {}).get('service') or {}
    found = []
    for name in service.get('depends_on') or []:
        target = service_dir(str(name))
        if target is None or target in seen:
            continue
        seen.add(target)
        found += dependencies(target, seen) + [target]
    return found


def main():
    env = dict(os.environ)
    for path in [ODS / 'docker-compose.base.yml', *LIBRARY.rglob('compose*.yaml'), *BUNDLED.rglob('compose*.yaml')]:
        for name in REQUIRED.findall(path.read_text(encoding='utf-8')):
            env.setdefault(name, 'ci-placeholder')

    total, failed = 0, []
    for directory in sorted(p for p in LIBRARY.iterdir() if (p / 'compose.yaml').is_file()):
        files = ['docker-compose.base.yml']
        files += [str((d / 'compose.yaml').relative_to(ODS)) for d in dependencies(directory)]
        files.append(str((directory / 'compose.yaml').relative_to(ODS)))
        for overlay in (None, 'compose.nvidia.yaml', 'compose.amd.yaml'):
            if overlay and not (directory / overlay).is_file():
                continue
            stack = files + ([str((directory / overlay).relative_to(ODS))] if overlay else [])
            label = directory.name + (f' + {overlay}' if overlay else '')
            total += 1
            command = ['docker', 'compose'] + [arg for f in stack for arg in ('-f', f)] + ['config', '--quiet']
            result = subprocess.run(command, cwd=ODS, env=env, capture_output=True, text=True)
            if result.returncode != 0:
                failed.append(label)
                print(f'::error::{label}: {(result.stderr or result.stdout).strip()}')
    print(f'Validated {total} recipe configurations; {len(failed)} failed.')
    for label in failed:
        print('  ' + label)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
