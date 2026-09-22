"""Atomic publication of validated configuration only; never starts applications."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import yaml

from extension_github import repository_identity
from extension_source_build import verify_source_receipts

RECOGNIZED_OPEN_SOURCE_LICENSES = frozenset({
    'MIT', 'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', 'ISC', 'Zlib', 'Unlicense',
    'MPL-2.0', 'EPL-1.0', 'EPL-2.0', 'BSL-1.0', '0BSD', 'Artistic-2.0',
    'GPL-2.0', 'GPL-3.0', 'AGPL-3.0', 'LGPL-2.1', 'LGPL-3.0',
    'GPL-2.0-only', 'GPL-2.0-or-later', 'GPL-3.0-only', 'GPL-3.0-or-later',
    'AGPL-3.0-only', 'AGPL-3.0-or-later', 'LGPL-2.1-only', 'LGPL-2.1-or-later',
    'LGPL-3.0-only', 'LGPL-3.0-or-later',
})

def recipe_digest(candidate):
    return hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def package_receipt(candidate):
    return {'schemaVersion': 1, 'extensionId': candidate['manifest']['service']['id'],
            'recipeDigest': recipe_digest(candidate), 'state': 'available',
            'installationStarted': False, 'registered': False, 'runtimeVerified': False}


def verify_package(directory, candidate, *, compose_name='compose.yaml'):
    """An existing exact package is reusable, never silently repaired or overwritten."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Invalid recipe package')
    if compose_name not in {'compose.yaml', 'compose.yaml.disabled'}:
        raise ValueError('Invalid recipe Compose filename')
    names = {'manifest.yaml', compose_name, 'upstream.json'}
    if {path.name for path in directory.iterdir()} != names:
        raise ValueError('Recipe package has unexpected files')
    for name in names:
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 524288:
            raise ValueError('Invalid recipe package file')
    if (yaml.safe_load((directory / 'manifest.yaml').read_text(encoding='utf-8')) != candidate['manifest']
            or yaml.safe_load((directory / compose_name).read_text(encoding='utf-8')) != candidate['compose']):
        raise ValueError('Recipe package changed')
    provenance = json.loads((directory / 'upstream.json').read_text(encoding='utf-8'))
    if (provenance.get('recipeDigest') != recipe_digest(candidate)
            or provenance.get('repository') != candidate['repository']
            or provenance.get('commit') != candidate['commit']
            or provenance.get('origin') != 'github-proposal'):
        raise ValueError('Recipe provenance changed')
    verify_source_receipts(candidate, provenance.get('sourceFiles'))
    return provenance


def publish_package(library, candidate, validation, evidence):
    """Caller holds the extension mutation lock and supplies fresh validation/evidence."""
    library = Path(library)
    identifier = candidate['manifest']['service']['id']
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', identifier):
        raise ValueError('Invalid extension identifier')
    digest = recipe_digest(candidate)
    if (validation.get('valid') is not True or validation.get('errors') != []
            or validation.get('recipeDigest') != digest
            or validation.get('existingExtensionIds') != []
            or validation.get('validationScope') != 'static-manifest-and-compose'):
        raise ValueError('Current recipe validation required')
    if (repository_identity(evidence.get('repository')).lower() != repository_identity(candidate['repository']).lower()
            or evidence.get('commit') != candidate['commit']
            or evidence.get('existingExtensionIds') != []
            or evidence.get('evidenceScope') != 'repository-documents-at-commit'):
        raise ValueError('Matching repository evidence required')
    if library.is_symlink() or not library.is_dir():
        raise ValueError('Invalid recipe library')
    if (evidence.get('licenseIdentifier') not in RECOGNIZED_OPEN_SOURCE_LICENSES
            or not isinstance(evidence.get('licenseText'), str) or not evidence['licenseText'].strip()):
        raise ValueError('Repository license requires review')
    verify_source_receipts(candidate, evidence.get('sourceFiles'))
    destination = library / identifier
    if destination.exists() or destination.is_symlink():
        verify_package(destination, candidate)
    else:
        provenance = {'origin': 'github-proposal', 'repository': candidate['repository'],
                      'commit': candidate['commit'], 'recipeDigest': digest,
                      'licenseIdentifier': evidence.get('licenseIdentifier'),
                      'licenseEvidenceScope': 'repository-documents-at-commit', 'runtimeVerified': False}
        if evidence.get('sourceFiles'):
            provenance['sourceFiles'] = evidence['sourceFiles']
        # Temporary directories are hidden from discovery; publish all files
        # together on the same filesystem. Never copy over an existing target.
        with tempfile.TemporaryDirectory(prefix='.github-recipe-', dir=library) as temporary:
            staged = Path(temporary) / identifier
            staged.mkdir()
            documents = {'manifest.yaml': yaml.safe_dump(candidate['manifest'], sort_keys=False),
                         'compose.yaml': yaml.safe_dump(candidate['compose'], sort_keys=False),
                         'upstream.json': json.dumps(provenance, sort_keys=True)}
            for name, content in documents.items():
                with (staged / name).open('x', encoding='utf-8', newline='\n') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            verify_package(staged, candidate)
            if destination.exists() or destination.is_symlink():
                raise ValueError('Extension appeared during preparation')
            os.rename(staged, destination)
    return package_receipt(candidate)
