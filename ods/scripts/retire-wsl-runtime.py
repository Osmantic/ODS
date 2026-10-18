#!/usr/bin/env python3
"""Check, then retire owned Windows startup before deleting a WSL install."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import re
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


def _tree_bytes(path: Path) -> int:
    """Bytes of the regular files under path; links are not followed."""
    total = 0
    for folder, _dirs, files in os.walk(path):
        for name in files:
            try:
                info = os.lstat(os.path.join(folder, name))
            except FileNotFoundError:
                # A file removed while the folder is measured has no size.
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def _gb(size: int) -> str:
    return f"{size / 1e9:.1f} GB"


_PAGING_ENTRY = re.compile(r"^(?P<drive>[A-Za-z?]:)\\\S+(\s+(?P<initial>[0-9]+)\s+(?P<maximum>[0-9]+))?$")


def _paging_description(entries, automatic: bool) -> str:
    """The page file Windows had before ODS, in words (windows-settings.ps1)."""
    if automatic:
        return "managed automatically by Windows"
    parts = []
    for entry in [str(item).strip() for item in entries if str(item).strip()]:
        match = _PAGING_ENTRY.match(entry)
        if not match:
            parts.append(f"'{entry}'")
            continue
        drive = match["drive"]
        initial = int(match["initial"] or 0)
        maximum = int(match["maximum"] or 0)
        if drive == "?:":
            parts.append("managed automatically by Windows")
        elif initial <= 0 or maximum <= 0:
            parts.append(f"system managed on {drive}")
        elif initial == maximum:
            parts.append(f"a fixed {initial} MB page file on {drive}")
        else:
            parts.append(f"{initial} to {maximum} MB on {drive}")
    return ", ".join(parts) or "no page file"


def _windows_settings(path: Path) -> dict:
    """windows-settings.json, which Windows setup writes only when it changes a setting."""
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        # An unreadable record names nothing to undo; uninstall still succeeds.
        return {}
    return value if isinstance(value, dict) else {}


def windows_kept_summary(root: Path, values: dict, managed: dict) -> list:
    """What uninstall leaves on Windows, one line each, only when it applies.

    The Windows model store and llama.cpp with their sizes, the page file
    Windows setup set, and the script that undoes ODS's WSL memory limit.
    Paths come from the managed runtime's own model store, so only folders
    this installation owns are measured.
    """
    windows_store = managed.get("modelStoreWindowsPath") if isinstance(managed, dict) else None
    if not isinstance(windows_store, str):
        return []
    store_windows = PureWindowsPath(windows_store)
    if [part.casefold() for part in store_windows.parts[-3:]] != ["ods", "lemonade", "models"]:
        return []
    try:
        store = wsl_lemonade.model_store(root, values, managed)
    except (OSError, ValueError):
        # The Windows drive is not mounted here: nothing to measure or name.
        return []
    ods_windows = store_windows.parent.parent
    ods_dir = store.parent.parent
    lines = []
    kept = []
    size = _tree_bytes(store)
    if size:
        kept.append(f"ODS's model files ({store_windows}, {_gb(size)})")
    llama = ods_dir / "llama.cpp"
    if llama.is_dir() and not llama.is_symlink():
        size = _tree_bytes(llama)
        if size:
            kept.append(f"llama.cpp ({ods_windows / 'llama.cpp'}, {_gb(size)})")
    if kept:
        lines.append("Windows keeps " + " and ".join(kept)
                     + " so a reinstall needs no download; delete the folders to free the space.")
    settings = _windows_settings(ods_dir / "windows-settings.json")
    page_file = settings.get("PageFile")
    if isinstance(page_file, dict) and isinstance(page_file.get("SetGB"), int) and page_file["SetGB"] > 0:
        before = _paging_description(page_file.get("Original") or [], bool(page_file.get("OriginalAutomatic")))
        lines.append(f"ODS set the page file to {page_file['SetGB']} GB (it was {before}); to change it, see System "
                     "Properties > Advanced > Performance > Settings > Advanced > Virtual memory.")
    restore = ods_dir / "restore-windows-settings.ps1"
    if isinstance(settings.get("WslConfig"), dict) and restore.is_file() and not restore.is_symlink():
        lines.append("ODS limited WSL memory; to undo that, run in PowerShell: "
                     f"& \"{ods_windows / 'restore-windows-settings.ps1'}\"")
    return lines


def retire(install_dir: Path, *, validate_only: bool = False, kept_summary: bool = False) -> dict:
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
    result = {'state': 'retired', 'startup': startup['state']}
    if kept_summary:
        result['windowsKept'] = windows_kept_summary(root, runtime_values, managed) if managed and managed['managed'] else []
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, required=True)
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--kept-summary', type=Path,
                        help='write what stays on Windows to this file, one line each (uninstall prints it last)')
    args = parser.parse_args()
    try:
        result = retire(args.install_dir, validate_only=args.validate_only, kept_summary=args.kept_summary is not None)
        kept = result.pop('windowsKept', None)
        if args.kept_summary is not None:
            args.kept_summary.write_text(''.join(f'{line}\n' for line in kept or []), encoding='utf-8')
        print(json.dumps(result))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        state = 'installation retained' if args.validate_only else 'installation files retained; startup may already be disabled'
        print(f'Windows startup retirement failed; {state}: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
