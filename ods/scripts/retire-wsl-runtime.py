#!/usr/bin/env python3
"""Check, then retire owned Windows startup before deleting a WSL install."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'bin'))
sys.path.insert(0, str(SOURCE / 'extensions/services/dashboard-api'))
from env_values import parse_env_value
from model_switchboard import wsl_lemonade

# The Lemonade migration writes the round-F keys. The WSL bridge
# (bin/model_switchboard) still reads the Lemonade-era names, so the bridge
# gets both, filled from whichever the .env holds (compatibility, one release).
_BRIDGE_ALIASES = {
    'ODS_HOST_LLM_TRANSPORT': 'LEMONADE_HOST_TRANSPORT',
    'NATIVE_LLM_BASE_URL': 'LEMONADE_BASE_URL',
    'NATIVE_LLM_CONTAINER_BASE_URL': 'LEMONADE_CONTAINER_BASE_URL',
}
_KEYS = {*_BRIDGE_ALIASES, *_BRIDGE_ALIASES.values(),
         'AMD_INFERENCE_PORT', 'ODS_WINDOWS_SYSTEM_DIRECTORY', 'ODS_WSL_STATE_ROOT'}


def _with_bridge_aliases(values: dict) -> dict:
    for key, legacy in _BRIDGE_ALIASES.items():
        if values.get(key) and not values.get(legacy):
            values[legacy] = values[key]
        elif values.get(legacy) and not values.get(key):
            values[key] = values[legacy]
    return values


def _owner(root: Path) -> None:
    uid = os.getuid()
    if uid == 0:
        raise ValueError('Run the WSL uninstaller as the Linux installation owner, without sudo; it requests sudo when needed')
    for path, directory in ((root, True), (root / '.env', False)):
        info = path.lstat()
        kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if (not kind or path.resolve() != path or info.st_uid != uid or info.st_mode & 0o022
                or (not directory and info.st_nlink != 1)):
            raise ValueError('The WSL installation and .env must be private files owned by the current Linux user')


def _environment(root: Path) -> dict:
    path = root / '.env'
    if not path.exists() and not path.is_symlink():
        return {}
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.resolve() != path:
        raise ValueError('Refusing redirected installation environment during Windows startup detection')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('Installation environment changed during Windows startup detection')
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError('Installation environment exceeds its bounded contract')
    values = {}
    for line in content.decode('utf-8').splitlines():
        key, separator, value = line.partition('=')
        if separator and key.strip() in _KEYS:
            values[key.strip()] = parse_env_value(value)
    return _with_bridge_aliases(values)


def retire(install_dir: Path, *, validate_only: bool = False) -> dict:
    if platform.system() != 'Linux' or 'microsoft' not in platform.release().casefold():
        return {'state': 'not_wsl'}
    root = Path(install_dir).absolute()
    values = _environment(root)
    metadata = root / 'data/wsl-lemonade-runtime.json'
    registered = metadata.exists() or metadata.is_symlink()
    if not {'ODS_WINDOWS_SYSTEM_DIRECTORY', 'ODS_WSL_STATE_ROOT'}.intersection(values) and not registered:
        # Linux-origin and old WSL installations never registered Windows
        # startup. Do not require interop, strict new permissions or an owner
        # migration merely to uninstall those versions.
        return {'state': 'unmanaged_legacy', 'reason': 'no_windows_management_contract'}
    _owner(root)
    if 'ODS_WINDOWS_SYSTEM_DIRECTORY' in values and not values['ODS_WINDOWS_SYSTEM_DIRECTORY']:
        raise ValueError('The registered Windows system directory is empty; restore it before uninstalling')
    managed = None
    runtime_values = values
    routed = wsl_lemonade.candidate(values)
    if registered and not routed:
        # Routing can move to an API or the cloud while the owned Windows task
        # stays registered. Custody comes from that task's user, distro,
        # install root and immutable plan, not from the current endpoint, so
        # probe it with a control-only environment. Nothing here changes the
        # installation's routing or is written to its .env.
        runtime_values = _with_bridge_aliases({
            **{key: values[key] for key in ('ODS_WINDOWS_SYSTEM_DIRECTORY', 'ODS_WSL_STATE_ROOT')
               if key in values},
            'ODS_HOST_LLM_TRANSPORT': 'model-router',
        })
    if routed or registered:
        managed = wsl_lemonade.status(root, runtime_values)
        if not managed['managed'] and registered:
            raise ValueError('The registered Windows runtime no longer belongs to this installation')
    # All Windows ownership checks precede the first mutation. Disable and
    # settle sign-in startup before stopping Lemonade or retiring Pixel, so a
    # boot coordinator cannot restart services during their removal.
    startup = wsl_lemonade.disable_startup(root, values, validate_only=True, retire_relay=True)
    if validate_only:
        return {'state': 'validated', 'startup': startup['state']}
    startup = wsl_lemonade.disable_startup(root, values, retire_relay=True)
    if managed and managed['managed']:
        wsl_lemonade.stop(root, runtime_values, managed['planDigest'])
    return {'state': 'retired', 'startup': startup['state']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(retire(args.install_dir, validate_only=args.validate_only)))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        state = 'installation retained' if args.validate_only else 'installation files retained; startup may already be disabled'
        print(f'Windows startup retirement failed; {state}: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
