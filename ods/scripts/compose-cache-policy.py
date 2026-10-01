#!/usr/bin/env python3
"""Revalidate saved user-extension fragments before trusting Compose's cache.

The cache is an argument list, not an authorization receipt. Reuse the dynamic
resolver's policy verbatim, including provenance and accelerator exceptions,
without invoking Bash, discovering other services or starting any container.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys


def _files(flags):
    if not isinstance(flags, list) or any(not isinstance(value, str) or
                                         any(c in value for c in '\x00\r\n') for value in flags):
        raise ValueError('Invalid saved Compose arguments')
    index = 0
    while index < len(flags):
        value = flags[index]
        if value in ('-f', '--file'):
            if index + 1 == len(flags):
                raise ValueError('Saved Compose file argument is missing')
            yield flags[index + 1]
            index += 2
        elif value.startswith('--file='):
            yield value[len('--file='):]
            index += 1
        else:
            index += 1


def validate_flags(install_dir, flags):
    root = pathlib.Path(install_dir).resolve()
    user_root = root / 'data/user-extensions'
    canonical_user_root = user_root.resolve()
    fragments = []
    for name in _files(flags):
        path = pathlib.Path(name)
        path = pathlib.Path(os.path.abspath(path if path.is_absolute() else root / path))
        # Data may live on another disk. Allow that installation-wide mapping,
        # but reject aliases to individual recipes, including aliases outside
        # user-extensions which otherwise look like trusted core fragments.
        try:
            relative = path.relative_to(user_root)
        except ValueError:
            if path.resolve().is_relative_to(canonical_user_root):
                raise ValueError('Cached extension aliases are not permitted; refresh the saved stack')
            continue
        if (len(relative.parts) != 2 or path.parent.is_symlink() or path.is_symlink()
                or path.resolve() != canonical_user_root / relative):
            raise ValueError('Cached extension path must be an owned regular recipe fragment')
        if not path.is_file():
            raise ValueError('Cached extension is disabled or missing; refresh the saved stack')
        fragments.append(path)
    if not fragments:
        return flags

    try:
        import yaml
    except ImportError as error:
        raise ValueError('PyYAML is required to validate saved extensions; repair the ODS Python runtime') from error
    # A candidate uninstaller can validate an older installation. Load policy
    # from this helper's release, while resolving recipe data against root.
    resolver = pathlib.Path(__file__).resolve().with_name('resolve-compose-stack.sh')
    try:
        source = resolver.read_text(encoding='utf-8')
        start = source.index('_LOOPBACK_VAR_DEFAULT_RE = re.compile(')
        end = source.index('def _load_compose_mapping(', start)
    except (OSError, ValueError) as error:
        raise ValueError('Installed Compose security policy is missing; repair the ODS installation') from error
    namespace = {'script_dir': root, 'pathlib': pathlib, 're': re, 'os': os,
                 'json': json, 'yaml': yaml, 'sys': sys}
    # This is installed ODS code, never recipe text. The same delimited policy
    # is used by the resolver and its adversarial tests; avoid a third rule set.
    exec(compile(source[start:end], str(resolver), 'exec'), namespace)

    projections = []
    scanned = set()
    documents = []
    for path in fragments:
        if path.name.startswith('.ods-build-context-'):
            projections.append(path)
            continue
        directory = path.parent
        manifest_path = next((directory / name for name in
                              ('manifest.yaml', 'manifest.yml', 'manifest.json')
                              if (directory / name).is_file()), None)
        service = {}
        if manifest_path is not None:
            if manifest_path.is_symlink():
                raise ValueError('Cached extension manifest must not be a symlink')
            manifest = namespace['_compose_policy_load'](manifest_path.read_text(encoding='utf-8'))
            if not isinstance(manifest, dict) or not isinstance(manifest.get('service'), dict):
                raise ValueError('Cached extension manifest is invalid')
            service = manifest['service']
        base = namespace['_extension_base_path'](directory, service, directory.name)
        if base is None or base not in fragments:
            raise ValueError('Cached extension base is disabled or omitted; refresh the saved stack')
        trusted = namespace['_library_recipe_trusted'](directory)
        accelerator = {'compose.nvidia.yaml': 'nvidia', 'compose.amd.yaml': 'amd'}.get(path.name)
        ok, problems = namespace['_scan_user_compose_content'](
            path, trusted, accelerator, extension_id=directory.name)
        if not ok:
            raise ValueError(f'Cached extension {directory.name} requires review: ' + '; '.join(problems)
                             + f". To recover, run 'ods disable {directory.name}' (it stops the extension safely"
                             + ' and keeps its data), then reinstall it from the dashboard Extensions page.')
        scanned.add(str(path.resolve()))
        documents.append((path, namespace['_compose_policy_load'](path.read_text(encoding='utf-8'))))

    problems = namespace['_source_runtime_merge_problems'](documents)
    if problems:
        raise ValueError('Saved source extension requires review: ' + '; '.join(problems))

    for projection in projections:
        original = projection.parent / projection.name.removeprefix('.ods-build-context-').removesuffix('.json')
        key = str(original.resolve())
        expected = namespace['_extension_build_contexts'].get(key)
        if key not in scanned or not expected:
            raise ValueError('Cached extension build projection has no validated source recipe')
        actual = namespace['_compose_policy_load'](projection.read_text(encoding='utf-8'))
        if actual != {'services': expected}:
            raise ValueError('Cached extension build projection changed; refresh the saved stack')
    return flags


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--flags')
    inputs.add_argument('--arguments', nargs=argparse.REMAINDER)
    parser.add_argument('--format', choices=('flags', 'json'), default='json')
    args = parser.parse_args()
    try:
        if args.arguments is not None:
            flags = args.arguments
        elif args.flags is not None:
            flags = args.flags.split()
        else:
            # PowerShell 5.1 can prepend a UTF-8 BOM to native pipeline input.
            # Decode the wire format explicitly, independently of the locale.
            raw = sys.stdin.buffer.read(131073)
            if len(raw) > 131072:
                raise ValueError('Saved Compose arguments are too large')
            flags = json.loads(raw.decode('utf-8-sig'))
        validate_flags(args.install_dir, flags)
        print(' '.join(flags) if args.format == 'flags' else json.dumps(flags))
    except (OSError, ValueError) as error:
        print(f'Compose security validation failed: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
