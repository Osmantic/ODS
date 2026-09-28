#!/usr/bin/env python3
"""Stop existing ODS containers without evaluating an untrusted Compose recipe.

Used only for recovery after normal Compose validation fails. Ownership comes
from Docker's existing Compose labels, not names supplied by a changed recipe.
No container is created, no lifecycle hook is run, and no data is deleted.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def _path(value):
    return os.path.normcase(os.path.realpath(value))


def _docker(arguments, timeout=30):
    result = subprocess.run(['docker', *arguments], capture_output=True, text=True,
                            encoding='utf-8', timeout=timeout)
    if result.returncode:
        raise ValueError('Docker recovery command failed: ' + (result.stderr or 'no diagnostic')[:500])
    return result.stdout


def stop_owned_containers(install_dir, services=None):
    root = Path(install_dir).resolve(strict=True)
    services = list(services or [])
    if not root.is_dir() or any(not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name) for name in services):
        raise ValueError('Invalid installation or service identity')
    ids = _docker(['ps', '--all', '--quiet', '--no-trunc', '--filter',
                   'label=com.docker.compose.project.working_dir']).split()
    if any(not re.fullmatch(r'[a-f0-9]{64}', value) for value in ids):
        raise ValueError('Docker returned invalid container identities')
    owned = []
    base_files = {_path(root / name) for name in ('docker-compose.base.yml', 'docker-compose.yml')}
    for offset in range(0, len(ids), 100):
        batch = ids[offset:offset + 100]
        rows = json.loads(_docker(['inspect', *batch]))
        if (not isinstance(rows, list) or len(rows) != len(batch)
                or {row.get('Id') for row in rows if isinstance(row, dict)} != set(batch)):
            raise ValueError('Docker inspection did not match the requested containers')
        for row in rows:
            config = row.get('Config')
            if not isinstance(config, dict):
                raise ValueError('Docker returned invalid container configuration')
            labels = config.get('Labels') or {}
            if not isinstance(labels, dict):
                raise ValueError('Docker returned invalid ownership labels')
            working_dir = labels.get('com.docker.compose.project.working_dir')
            config_files = labels.get('com.docker.compose.project.config_files', '')
            service = labels.get('com.docker.compose.service', '')
            project = labels.get('com.docker.compose.project', '')
            if not all(isinstance(value, str) for value in (working_dir, config_files, service, project)):
                continue
            if not os.path.isabs(working_dir) or _path(working_dir) != _path(root):
                continue
            # A matching project name alone is insufficient: native Windows and
            # WSL installations may both have used the old default project ods.
            if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project):
                continue
            if not any(os.path.isabs(name) and _path(name) in base_files for name in config_files.split(',')):
                continue
            if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', service) or (services and service not in services):
                continue
            owned.append(row['Id'])
    if owned:
        # A legacy restart: always must not resurrect the rejected runtime when
        # Docker restarts. A reviewed Compose recreate restores its policy.
        _docker(['update', '--restart=no', *owned])
        # No --time: each container keeps its own stop_grace_period, so a
        # database with a 60 s grace is not killed after 10 s. Docker stops the
        # containers in parallel, so the bound covers the longest grace.
        _docker(['stop', *owned], timeout=300)
    return owned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--service', action='append', default=[])
    args = parser.parse_args()
    try:
        stopped = stop_owned_containers(args.install_dir, args.service)
        print(f'Stopped {len(stopped)} verified ODS containers and disabled their automatic restart. Data and containers are preserved; repair and recreate the recipe before starting again.')
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'ODS container recovery failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
