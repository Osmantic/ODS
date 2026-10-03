"""Discover installed definitions absent from the shipped extension catalog."""
import json
import re
from pathlib import Path

import jsonschema
import yaml


def merge_local_catalog(catalog, directory, schema_path, *, proposals_only=False):
    """Read definitions only; lifecycle status remains the caller's responsibility."""
    result = list(catalog)
    seen = {entry['id'] for entry in result}
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        return result
    try:
        schema = json.loads(Path(schema_path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return result
    validator = jsonschema.Draft202012Validator(schema)
    for child in sorted(directory.iterdir()):
        identifier = child.name
        if identifier in seen or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', identifier):
            continue
        if child.is_symlink() or not child.is_dir():
            continue
        manifest = child / 'manifest.yaml'
        compose = child / 'compose.yaml'
        disabled = child / 'compose.yaml.disabled'
        if any(path.is_symlink() for path in (manifest, compose, disabled)):
            continue
        if not compose.is_file() and not disabled.is_file():
            continue
        try:
            if proposals_only:
                provenance_path = child / 'upstream.json'
                if provenance_path.is_symlink() or provenance_path.stat().st_size > 65536:
                    continue
                provenance = json.loads(provenance_path.read_text(encoding='utf-8'))
                if not isinstance(provenance, dict) or provenance.get('origin') != 'github-proposal':
                    continue
            if manifest.stat().st_size > 262144:
                continue
            document = yaml.safe_load(manifest.read_text(encoding='utf-8'))
            if not validator.is_valid(document):
                continue
            service = document['service']
            if service['id'] != identifier or service.get('compose_file') != 'compose.yaml':
                continue
            entry = {key: service[key] for key in (
                'id', 'name', 'description', 'category', 'gpu_backends', 'compose_file',
                'depends_on', 'port', 'external_port_default', 'startup_check', 'startup_timeout'
            ) if key in service}
            entry.setdefault('description', '')
            entry.setdefault('category', 'optional')
            entry['health_endpoint'] = service.get('health', '')
            # Runtime values and secret defaults never belong in catalog output.
            entry['env_vars'] = [{key: value for key, value in field.items()
                                  if key in {'key', 'description', 'required', 'secret'}}
                                 for field in service.get('env_vars', [])]
            entry['tags'] = document.get('tags', [])
            entry['features'] = document.get('features', [])
            entry['configuration_scope'] = 'declared-environment-keys'
            result.append(entry)
            seen.add(identifier)
        except (OSError, ValueError, TypeError, KeyError, RecursionError, yaml.YAMLError):
            # One incomplete installation cannot hide the shipped catalog.
            continue
    return result
