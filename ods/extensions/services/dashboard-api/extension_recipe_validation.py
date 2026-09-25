"""Static checks for model-proposed recipes. No installation or trust grant."""
import hashlib
import copy
import json
import re
import tempfile
from pathlib import Path

import jsonschema
import yaml

from extension_github import repository_identity
from extension_install_plan import ID
from extension_source_build import source_builds


def validate_recipe(candidate, schema, reserved_ids, scan_compose):
    if not isinstance(candidate, dict) or set(candidate) != {'repository', 'commit', 'manifest', 'compose'}:
        raise ValueError('Recipe requires repository, commit, manifest and compose')
    repository = repository_identity(candidate['repository'])
    if not isinstance(candidate['commit'], str) or not re.fullmatch(r'[a-f0-9]{40}', candidate['commit']):
        raise ValueError('Recipe requires an immutable commit')
    encoded = json.dumps(candidate, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(encoded) > 262144:
        raise ValueError('Recipe exceeds the content limit')
    errors = []

    def error(code, path):
        if len(errors) < 32:
            errors.append({'code': code, 'path': path})

    manifest, compose = candidate['manifest'], candidate['compose']
    # Schema diagnostics expose the schema path, never rejected values (which
    # may include a credential the model should not have put in the recipe).
    for issue in jsonschema.Draft202012Validator(schema).iter_errors(manifest):
        error('manifest-schema', 'manifest/' + '/'.join(map(str, issue.absolute_schema_path)))
    service = manifest.get('service', {}) if isinstance(manifest, dict) else {}
    target = service.get('id') if isinstance(service, dict) else None
    valid_id = isinstance(target, str) and ID.fullmatch(target)
    if not valid_id:
        error('invalid-extension-id', 'manifest/service/id')
    elif target in reserved_ids:
        error('extension-already-exists', 'manifest/service/id')
    if isinstance(service, dict) and (service.get('type') != 'docker' or service.get('compose_file') != 'compose.yaml'):
        error('unsupported-installation-kind', 'manifest/service')
    if valid_id:
        prefix = re.sub(r'[^A-Z0-9]', '_', target.upper()) + '_'
        allowed_variables = {'BIND_ADDRESS', 'TZ'}
        declarations = service.get('env_vars', [])
        keys = [item.get('key') for item in declarations if isinstance(item, dict)] if isinstance(declarations, list) else []
        keys += [service.get(field) for field in ('host_env', 'external_port_env') if service.get(field)]
        for key in keys:
            if not isinstance(key, str) or not key.startswith(prefix):
                error('configuration-ownership-required', 'manifest/service/env_vars')
            else:
                allowed_variables.add(key)
        # The proposed package contains only manifest and Compose, not scripts.
        # Never resolve a model-selected hook against existing host files.
        if service.get('setup_hook') or service.get('hooks'):
            error('hook-files-review-required', 'manifest/service')

        def inspect_variables(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    inspect_variables(key)
                    inspect_variables(item)
            elif isinstance(value, list):
                for item in value:
                    inspect_variables(item)
            elif isinstance(value, str):
                # Compose treats $$ as an escaped dollar. Match it first so
                # container-side variables are not mistaken for host access.
                for braced, plain in re.findall(r'\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)|\$([A-Za-z_][A-Za-z0-9_]*)', value):
                    variable = braced or plain
                    if variable and variable not in allowed_variables:
                        error('undeclared-host-variable', 'compose')
        inspect_variables(compose)
    services = compose.get('services') if isinstance(compose, dict) else None
    if isinstance(compose, dict) and set(compose) - {'services', 'volumes', 'networks', 'version'}:
        error('compose-external-directives-review-required', 'compose')
    if not isinstance(services, dict) or not 1 <= len(services) <= 16:
        error('compose-services-required', 'compose/services')
    elif valid_id:
        try:
            reviewed_builds = {entry['service'] for entry in source_builds(candidate)}
        except (ValueError, KeyError, TypeError):
            reviewed_builds = set()
        if target not in services:
            error('primary-service-missing', 'compose/services')
        for name, definition in services.items():
            if (not isinstance(name, str) or not ID.fullmatch(name)
                    or not (name == target or name.startswith(target + '-')) or name in reserved_ids):
                error('service-name-conflict', 'compose/services')
            if not isinstance(definition, dict):
                error('invalid-service-definition', 'compose/services')
                continue
            image = definition.get('image')
            if name not in reviewed_builds and (not isinstance(image, str) or not re.fullmatch(r'[A-Za-z0-9._:/-]+@sha256:[a-f0-9]{64}', image)):
                error('image-digest-required', 'compose/services/image')
            if 'build' in definition and name not in reviewed_builds:
                error('source-build-review-required', 'compose/services/build')
            if any(key in definition for key in ('extends', 'env_file', 'secrets', 'configs',
                   'volumes_from', 'use_api_socket', 'provider', 'develop', 'post_start',
                   'pre_stop', 'credential_spec')):
                error('external-service-directives-review-required', 'compose/services')
            if definition.get('container_name') not in (None, 'ods-' + str(name)):
                error('container-name-conflict', 'compose/services/container_name')
        primary = services.get(target, {})
        health = primary.get('healthcheck') if isinstance(primary, dict) else None
        one_shot = service.get('port') == 0 and service.get('startup_check') is False
        command = primary.get('command') if isinstance(primary, dict) else None
        if one_shot:
            if (not isinstance(command, list) or not command
                    or not all(isinstance(arg, str) and arg and '\0' not in arg for arg in command)):
                error('verification-command-required', 'compose/services/command')
            if isinstance(primary, dict) and primary.get('restart') not in (None, 'no'):
                error('one-shot-restart-forbidden', 'compose/services/restart')
        elif (not isinstance(health, dict) or health.get('disable') is True or not health.get('test')
                or health.get('test') in ('NONE', ['NONE'])):
            error('healthcheck-required', 'compose/services/healthcheck')
        for group in ('volumes', 'networks'):
            definitions = compose.get(group, {})
            if not isinstance(definitions, dict):
                error('invalid-resource-definitions', 'compose/' + group)
                continue
            for name, definition in definitions.items():
                if group == 'networks' and name == 'ods-network':
                    if definition != {'external': True, 'name': 'ods-network'}:
                        error('ods-network-definition-required', 'compose/networks')
                    continue
                if not isinstance(name, str) or not name.startswith(target + '-'):
                    error('resource-name-conflict', 'compose/' + group)
                if definition is not None and (not isinstance(definition, dict) or
                        any(key in definition for key in ('external', 'name', 'driver_opts'))):
                    error('external-resource-review-required', 'compose/' + group)
    if not errors:
        with tempfile.TemporaryDirectory(prefix='ods-recipe-validation-') as temporary:
            path = Path(temporary) / 'compose.yaml'
            # Source-build options were checked separately above. Keep all
            # container directives subject to the untrusted Compose scanner.
            scanned = copy.deepcopy(compose)
            for definition in scanned['services'].values():
                definition.pop('build', None)
            path.write_text(yaml.safe_dump(scanned, sort_keys=False), encoding='utf-8')
            # Uses the existing untrusted-upload boundary, not curated-library
            # privileges. The caller returns a sanitized policy diagnostic.
            if not scan_compose(path):
                error('compose-policy-rejected', 'compose')
    return {'schemaVersion': 1, 'repository': 'https://github.com/' + repository,
            'commit': candidate['commit'], 'recipeDigest': hashlib.sha256(encoded).hexdigest(),
            'validationScope': 'static-manifest-and-compose', 'valid': not errors,
            'errors': errors, 'provenanceVerified': False, 'runtimeVerified': False,
            'installationStarted': False, 'registered': False}
