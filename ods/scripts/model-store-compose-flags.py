#!/usr/bin/env python3
"""Refresh only ODS model-store overlays in an existing Compose argument list."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'extensions/services/dashboard-api'))
from env_values import parse_env_value
from model_stores import active_compose_overlay, validated_compose_overlay


def resolve_flags(install_dir, flags):
    root = Path(install_dir).resolve()
    if not isinstance(flags, list) or any(not isinstance(value, str) or any(c in value for c in '\x00\r\n') for value in flags):
        raise ValueError('Invalid saved Compose arguments')
    overlay = validated_compose_overlay(root)
    if overlay is None:
        if (root/'data/model-stores.json').exists():
            raise ValueError('Registered model stores have no matching Compose overlay')
        return flags
    identifier = 'default'
    env_file = root / '.env'
    if env_file.is_file():
        for line in env_file.read_text(encoding='utf-8', errors='replace').splitlines():
            key, separator, value = line.partition('=')
            k = key.strip()
            if k.startswith('export '):
                k = k[7:].strip()
            if separator and k == 'ODS_ACTIVE_MODEL_STORE':
                identifier = parse_env_value(value)
    active = active_compose_overlay(root, identifier)
    managed = {root/'.model-stores.compose.json', root/'data/.active-model-store.compose.json'}
    def is_managed(value):
        path = Path(value)
        return (path if path.is_absolute() else root/path).absolute() in managed
    result, index = [], 0
    while index < len(flags):
        value = flags[index]
        if value in ('-f', '--file') and index+1 < len(flags) and is_managed(flags[index+1]):
            index += 2
            continue
        if value.startswith('--file=') and is_managed(value[len('--file='):]):
            index += 1
            continue
        result.append(value)
        index += 1
    result += ['-f', str(overlay.relative_to(root)).replace('\\', '/')]
    if active is not None:
        result += ['-f', str(active.relative_to(root)).replace('\\', '/')]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True, type=Path)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--flags', help='Whitespace-delimited flags used by the shell CLI')
    inputs.add_argument('--json-stdin', action='store_true')
    parser.add_argument('--format', choices=('json', 'flags'), default='json')
    args = parser.parse_args()
    try:
        if args.json_stdin:
            raw = sys.stdin.read(131073)
            if len(raw) > 131072: raise ValueError('Saved Compose arguments are too large')
            flags = json.loads(raw)
        else:
            flags = args.flags.split()
        result = resolve_flags(args.install_dir, flags)
        print(json.dumps(result) if args.format == 'json' else ' '.join(result))
    except (OSError, ValueError) as error:
        print(f'Model-store Compose configuration is unavailable: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__': sys.exit(main())
