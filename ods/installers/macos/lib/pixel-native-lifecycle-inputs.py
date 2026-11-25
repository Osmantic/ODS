"""Bind the local inputs of already policy-validated Compose builds.

This does not interpret Dockerfiles or build images. Compose remains the build
authority. Contexts are conservatively hashed, including ignored source files;
the installation-root context uses its shipped deny-all/allow-list policy so
runtime data and model stores are never traversed.
"""
import hashlib
import os
from pathlib import Path
import re
import stat


def file_hash(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def root_allow_list(path):
    rules = [line.strip() for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith('#')]
    if not rules or rules[0] not in ('*', '**'):
        raise ValueError('native-lifecycle-root-build-policy-required')
    selected = []
    for rule in rules[1:]:
        if rule in ('**/__pycache__', '**/__pycache__/', '**/*.pyc'):
            continue
        if not rule.startswith('!'):
            raise ValueError('native-lifecycle-root-build-policy-required')
        relative = rule[1:].removesuffix('/**').rstrip('/')
        if not relative or any(part in ('', '.', '..') or not re.fullmatch('[A-Za-z0-9_.-]+', part)
                for part in relative.split('/')):
            raise ValueError('native-lifecycle-root-build-policy-required')
        selected.append(relative)
    return selected


def capture(install_dir, document, services):
    result = {}
    for name in services:
        build = document['services'][name].get('build')
        if not build:
            continue
        context = str(build['context']).replace('$$', '$')
        if context.startswith('https://github.com/'):
            # The existing extension policy verifies recipe provenance and
            # this immutable commit before this helper sees the definition.
            if not re.fullmatch(r'https://github.com/[^?#]+\.git#[a-f0-9]{40}(?::[^?#]+)?', context):
                raise ValueError('native-lifecycle-immutable-build-context-required')
            result[name] = {'remote': context}
            continue
        root = Path(context)
        root = root if root.is_absolute() else install_dir / root
        if root.resolve(strict=True) != root or not root.is_dir():
            raise ValueError('native-lifecycle-local-build-context-required')
        dockerfile = root / str(build.get('dockerfile', 'Dockerfile')).replace('$$', '$')
        roots = [root]
        extra = [dockerfile]
        for ignore in (root / '.dockerignore', Path(str(dockerfile) + '.dockerignore')):
            if ignore.exists(): extra.append(ignore)
        if root == install_dir:
            roots = []
            for ignore in extra[1:]:
                roots.extend(root / relative for relative in root_allow_list(ignore))
            if not roots:
                raise ValueError('native-lifecycle-root-build-policy-required')
        entries = {}
        def visit(path):
            info = path.lstat()
            relative = str(path.relative_to(root))
            if stat.S_ISLNK(info.st_mode):
                entries[relative] = {'link': os.readlink(path)}
            elif stat.S_ISDIR(info.st_mode):
                entries[relative] = {'directoryMode': stat.S_IMODE(info.st_mode)}
                for child in sorted(path.iterdir()): visit(child)
            elif stat.S_ISREG(info.st_mode):
                entries[relative] = {'sha256': file_hash(path), 'mode': stat.S_IMODE(info.st_mode)}
            else:
                raise ValueError('native-lifecycle-build-input-type-required')
        for path in sorted(set(roots + extra)):
            if path.exists(): visit(path)
            else: entries[str(path.relative_to(root))] = {'absent': True}
        result[name] = {'context': str(root), 'entries': entries}
    return result
