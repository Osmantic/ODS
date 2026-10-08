#!/usr/bin/env python3
"""Retire only unused, empty Pixel socket projections after Docker teardown.

Docker Desktop can leave stacked shared tmpfs binds in the owner's WSL distro.
Those mounts survive removal of the containers and make later systemd mount
namespace setup increasingly expensive. Never detach a live runtime directory,
use lazy/forced unmounts, or clean an unrelated Docker Desktop projection.
"""

import argparse
import json
import os
import posixpath
from pathlib import Path
import re
import stat
import subprocess
import sys


BASE = Path('/mnt/wsl/ods-portal-runtime')
WSL = Path('/mnt/wsl')
UNITS = ('ods-pixel-wsl-runtime-bridge.service', 'pixel-ingress.service',
         'pixel-workspace-preview.service')


class Refusal(RuntimeError):
    pass


def run(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise Refusal(f'{args[0]} {args[1]} failed (exit {result.returncode})')
    return result.stdout


def docker(*args):
    # sudo's context can differ from the installing owner's. Inspect only the
    # local WSL integration socket, and separately match its engine identity.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('DOCKER_')}
    result = subprocess.run(['docker', '--host', 'unix:///var/run/docker.sock', *args],
                            text=True, capture_output=True, timeout=30, env=environment)
    if result.returncode:
        raise Refusal(f'Local Docker inspection failed (exit {result.returncode})')
    return result.stdout


def unescape(value):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def read_mounts():
    rows = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        try:
            left, right = line.split(' - ', 1)
            fields, tail = left.split(), right.split()
            rows.append(dict(id=int(fields[0]), parent=int(fields[1]),
                             device=fields[2], root=unescape(fields[3]),
                             target=unescape(fields[4]), fs=tail[0],
                             propagation=fields[6:]))
        except (ValueError, IndexError) as error:
            raise Refusal('Cannot parse the mount table') from error
    if len({row['id'] for row in rows}) != len(rows):
        raise Refusal('Duplicate mount identity')
    return rows


def directory(path, *, empty=False):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o755 or path.resolve() != path):
        raise Refusal(f'Unexpected directory ownership, permissions, or link: {path}')
    if empty and any(path.iterdir()):
        raise Refusal(f'Runtime projection is not empty: {path}')


def plan(rows, base=None, wsl=None):
    """Validate all layers and peers visible in this WSL mount namespace.

    Docker consumers in its separate namespace are checked through the pinned
    local engine. This does not assert visibility into arbitrary namespaces.
    """
    base = BASE if base is None else base
    wsl = WSL if wsl is None else wsl
    sources = {str(base / name) for name in ('ingress', 'preview')}
    roots = {str((base / name).relative_to(wsl)): str(base / name)
             for name in ('ingress', 'preview')}
    roots = {'/' + root: source for root, source in roots.items()}
    device = os.stat(wsl).st_dev
    device = f'{os.major(device)}:{os.minor(device)}'
    parents = [row for row in rows if row['target'] == str(wsl)]
    if (len(parents) != 1 or parents[0]['fs'] != 'tmpfs'
            or parents[0]['device'] != device or parents[0]['root'] != '/'):
        raise Refusal('Cannot establish the shared WSL tmpfs identity')
    propagation = parents[0]['propagation']
    if len(propagation) != 1 or not re.fullmatch(r'shared:[0-9]+', propagation[0]):
        raise Refusal('Unexpected WSL mount propagation')
    directory(base)
    if any(row['target'] == str(base) for row in rows):
        raise Refusal('The Pixel runtime base is itself mounted')
    for source in sources:
        path = Path(source)
        if path.exists() or path.is_symlink():
            directory(path, empty=True)
    selected = []
    for row in rows:
        in_source = any(row['target'] == source or row['target'].startswith(source + '/')
                        for source in sources)
        has_root = row['device'] == device and row['root'] in roots
        if not in_source and not has_root:
            continue
        if row['device'] != device or row['fs'] != 'tmpfs' or row['root'] not in roots:
            raise Refusal('Foreign mount at a Pixel runtime target')
        if row['propagation'] != propagation:
            raise Refusal('Unexpected propagation peer for a Pixel runtime mount')
        source = roots[row['root']]
        proxy = re.fullmatch(re.escape(str(wsl))
                             + r'/docker-desktop-bind-mounts/[^/]+/[0-9a-f]{64}',
                             row['target'])
        if row['target'] != source and not proxy:
            raise Refusal('Unexpected peer of a Pixel runtime mount')
        target = Path(row['target'])
        directory(target, empty=True)
        # Same tmpfs is insufficient: only this exact directory inode is ours.
        if (target.stat().st_dev, target.stat().st_ino) != (
                Path(source).stat().st_dev, Path(source).stat().st_ino):
            raise Refusal('Projection does not match its exact source directory')
        selected.append(row)
    if len(selected) > 16384:
        raise Refusal('Runtime mount count exceeds the bounded cleanup limit')
    # A foreign layer stacked on one of the discovered proxy paths could hide
    # lower owned mounts. Validate the entire stack, not just matching rows.
    targets = {row['target'] for row in selected}
    selected_ids = {row['id'] for row in selected}
    if any(row['id'] not in selected_ids and any(
            row['target'] == target or row['target'].startswith(target + '/')
            for target in targets) for row in rows):
        raise Refusal('Foreign mount on a Docker Desktop projection')
    return selected


def ensure_unused(selected, engine_id):
    info = json.loads(docker('info', '--format', '{{json .}}'))
    if info.get('ID') != engine_id or info.get('OperatingSystem') != 'Docker Desktop':
        raise Refusal('Local Docker Desktop engine differs from the uninstall engine')
    for unit in UNITS:
        state = run('systemctl', 'show', unit, '--property=ActiveState', '--value').strip()
        if state not in ('inactive', 'failed'):
            raise Refusal(f'Pixel service is not stopped: {unit}')
    paths = {str(BASE), *(row['target'] for row in selected)}

    def check_source(source):
        source = posixpath.normpath(source)
        for alias in ('/run/desktop/mnt/host/wsl', '/mnt/host/wsl'):
            if source == alias or source.startswith(alias + '/'):
                source = str(WSL) + source[len(alias):]
                break
        if not source.startswith('/'):
            raise Refusal('Cannot establish a container bind source')
        if any(source == path or source.startswith(path + '/')
               or path.startswith(source.rstrip('/') + '/') for path in paths):
            raise Refusal('A Docker container still references a Pixel runtime projection')

    def check_options(options):
        device = options.get('device', '')
        bind = any(option in ('bind', 'rbind')
                   for option in options.get('o', '').split(','))
        if bind and not device.startswith('/'):
            raise Refusal('Cannot establish a local volume bind device')
        if device.startswith('/'):
            check_source(device)

    ids = docker('ps', '--all', '--quiet', '--no-trunc').split()
    if any(not re.fullmatch(r'[0-9a-f]{64}', value) for value in ids):
        raise Refusal('Cannot establish Docker container identities')
    volumes = set()
    for offset in range(0, len(ids), 64):
        batch = ids[offset:offset + 64]
        containers = json.loads(docker('inspect', *batch))
        if (not isinstance(containers, list)
                or {container.get('Id') for container in containers} != set(batch)):
            raise Refusal('Docker inspection did not match the selected containers')
        for container in containers:
            host = container.get('HostConfig', {})
            mounts = list(container.get('Mounts', [])) + list(host.get('Mounts', []))
            # A created, never-started container may have no resolved Mounts.
            for bind in host.get('Binds') or []:
                if bind.startswith('/'):
                    mounts.append({'Type': 'bind', 'Source': bind.split(':', 1)[0]})
                else:
                    volumes.add(bind.split(':', 1)[0])
            for mount in mounts:
                if mount.get('Type') == 'volume':
                    driver = (mount.get('VolumeOptions') or {}).get('DriverConfig') or {}
                    if driver.get('Options'):
                        if driver.get('Name', 'local') != 'local':
                            raise Refusal('Cannot inspect an inline volume driver')
                        check_options(driver['Options'])
                    name = mount.get('Name') or mount.get('Source')
                    # Inline options are checked even when a created anonymous
                    # volume does not yet have a resolved Mounts entry/name.
                    if name:
                        volumes.add(name)
                    continue
                if mount.get('Type') != 'bind':
                    continue
                # Refuse stopped containers too: they could restart using a
                # retired projection. Also catch binds of a parent directory.
                check_source(mount.get('Source', ''))
    for name in sorted(volumes):
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', name):
            raise Refusal('Cannot establish a referenced Docker volume identity')
        records = json.loads(docker('volume', 'inspect', name))
        if (not isinstance(records, list) or len(records) != 1
                or records[0].get('Name') != name or records[0].get('Driver') != 'local'):
            raise Refusal('Cannot inspect a local Docker volume consumer')
        volume = records[0]
        check_source(volume['Mountpoint'])
        check_options(volume.get('Options') or {})


def cleanup(engine_id, *, apply=False):
    if 'microsoft' not in Path('/proc/sys/kernel/osrelease').read_text().lower():
        return dict(skipped='not WSL', removedMounts=0)
    if not BASE.exists() and not BASE.is_symlink():
        return dict(skipped='no Pixel runtime projections', removedMounts=0)
    if os.geteuid() != 0:
        raise Refusal('Root is required to inspect and retire runtime projections')
    selected = plan(read_mounts())
    if not selected:
        return dict(apply=apply, initialMounts=0, remainingMounts=0,
                    unmountCalls=0, removedMounts=0)
    ensure_unused(selected, engine_id)
    initial = len(selected)
    calls = 0
    if apply:
        while selected:
            if calls >= 256:
                raise Refusal('Bounded ordinary unmount limit exhausted')
            ensure_unused(selected, engine_id)
            if plan(read_mounts()) != selected:
                raise Refusal('Runtime mounts changed during cleanup preflight')
            # Unmounting a source also removes its propagated peer layers.
            source_rows = [row for row in selected
                           if row['target'] in (str(BASE / 'ingress'), str(BASE / 'preview'))]
            target = (source_rows or selected)[-1]['target']
            run('umount', '--', target)
            calls += 1
            remaining = plan(read_mounts())
            if any(row not in selected for row in remaining):
                raise Refusal('Runtime mount identities changed during unmount')
            if len(remaining) >= len(selected):
                raise Refusal('Unmount did not reduce the verified mount set')
            selected = remaining
    return dict(apply=apply, initialMounts=initial, remainingMounts=len(selected),
                unmountCalls=calls, removedMounts=initial - len(selected))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--engine-id', required=True,
                        help='Docker engine ID observed by the uninstalling owner')
    args = parser.parse_args()
    try:
        print(json.dumps(cleanup(args.engine_id, apply=args.apply)))
    except (Refusal, OSError, ValueError, TypeError, KeyError,
            subprocess.TimeoutExpired) as error:
        print(f'Pixel WSL runtime cleanup refused: {error}', file=sys.stderr)
        sys.exit(1)
