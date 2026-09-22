"""Identity of an existing integration; never copies or executes its files."""
import hashlib
import json
import re
from pathlib import Path

import yaml

from extension_github import existing_recipes, repository_identity


def _compose_identity(content, directory):
    document = yaml.safe_load(content)
    if not isinstance(document, dict) or not isinstance(document.get('services'), dict):
        raise ValueError('Invalid integration Compose')
    for service in document['services'].values():
        if not isinstance(service, dict):
            raise ValueError('Invalid integration service')
        build = service.get('build')
        if isinstance(build, str):
            build = service['build'] = {'context': build}
        if not isinstance(build, dict) or not isinstance(build.get('context'), str):
            continue
        context = build['context']
        if context.startswith(('https://', 'http://', 'git://', 'ssh://', 'git@')):
            continue
        resolved = (directory / context).resolve()
        if not resolved.is_relative_to(directory.resolve()):
            raise ValueError('Integration build context escapes definition')
        build['context'] = './' + resolved.relative_to(directory.resolve()).as_posix()
    return json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def integration_identity(repository, extension_id, roots):
    if not isinstance(extension_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', extension_id):
        raise ValueError('Invalid integration identity')
    roots = tuple(Path(root) for root in roots)
    if extension_id not in existing_recipes(repository_identity(repository), *roots):
        raise ValueError('Integration does not match repository')
    directory = next(root / extension_id for root in roots
                     if (root / extension_id).exists() or (root / extension_id).is_symlink())
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Invalid integration directory')
    # Include scripts and auxiliary configuration, not just the display manifest.
    # Enabling/disabling renames Compose without changing the selected definition.
    files, total = {}, 0
    pending = [directory]
    entries = 0
    while pending:
        parent = pending.pop()
        for path in parent.iterdir():
            entries += 1
            if entries > 1024 or path.is_symlink():
                raise ValueError('Integration definition requires inspection')
            if path.parent == directory and path.name == '.ods-library-receipt.json' and path.is_file():
                continue  # Installer bookkeeping, never part of the executable definition.
            if path.is_dir():
                pending.append(path)
                continue
            if not path.is_file():
                raise ValueError('Invalid integration definition file')
            size = path.stat().st_size
            total += size
            if size > 8 * 1024 * 1024 or total > 32 * 1024 * 1024:
                raise ValueError('Integration definition exceeds identity limits')
            name = path.relative_to(directory).as_posix()
            if name == 'compose.yaml.disabled':
                name = 'compose.yaml'
            if name in files:
                raise ValueError('Ambiguous integration Compose definition')
            with path.open('rb') as stream:
                content = stream.read(size + 1)
            if len(content) != size:
                raise ValueError('Integration changed during observation')
            if name == 'compose.yaml':
                content = _compose_identity(content, directory)
            files[name] = hashlib.sha256(content).hexdigest()
    if not {'manifest.yaml', 'compose.yaml', 'upstream.json'} <= files.keys():
        raise ValueError('Incomplete integration definition')
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'extensionId': extension_id, 'definitionDigest': digest}
