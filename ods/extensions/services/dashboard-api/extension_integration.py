"""Bounded recipe guidance for integrating an extension into real project code.

Declarations are evidence, not verified connectivity or completed integration.
Never project environment values, Compose secrets, or installation commands.
"""
from pathlib import Path
import re

import yaml


def integration_guidance(service_id, roots):
    if not isinstance(service_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}', service_id):
        raise ValueError('Invalid extension identity')
    for root in roots:
        directory = Path(root) / service_id
        if directory.is_symlink():
            raise ValueError('Symlinked extension definition')
        if not directory.exists():
            continue
        manifest_path = next((directory / name for name in ('manifest.yaml', 'manifest.yml')
                              if (directory / name).exists()), None)
        if manifest_path is None or manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 1048576:
            raise ValueError('Extension manifest unavailable')
        manifest = yaml.safe_load(manifest_path.read_text(encoding='utf-8'))
        service = manifest.get('service') if isinstance(manifest, dict) else None
        if not isinstance(service, dict) or service.get('id') != service_id:
            raise ValueError('Extension identity mismatch')
        # Keep the actual installed definition first, even when incomplete.
        # Falling back to library docs could describe a different version.
        declared = {}
        for key in ('type', 'container_name', 'default_host', 'host_env', 'external_port_env'):
            value = service.get(key)
            if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
                declared[key] = value
        for key in ('port', 'external_port_default'):
            value = service.get(key)
            if type(value) is int and 1 <= value <= 65535:
                declared[key] = value
        readme = directory / 'README.md'
        text, truncated = None, False
        if readme.is_symlink():
            raise ValueError('Symlinked extension documentation')
        if readme.exists():
            if not readme.is_file():
                raise ValueError('Invalid extension documentation')
            with readme.open('rb') as stream:
                content = stream.read(24001)
            truncated = len(content) > 24000
            text = content[:24000].decode('utf-8', errors='replace')
            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        description = service.get('description')
        return {'schemaVersion': 1, 'extensionId': service_id,
                'scope': 'recipe-integration-guidance', 'contentTrust': 'untrusted-recipe-evidence',
                'description': description[:2000] if isinstance(description, str) else '',
                'declaredConnection': declared, 'documentation': text, 'documentationTruncated': truncated,
                'connectivityVerified': False, 'projectIntegrationVerified': False}
    return None
