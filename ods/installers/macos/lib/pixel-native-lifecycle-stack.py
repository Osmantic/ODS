"""Freeze an installed CLI operation's selected definitions and image identities.

This is an admission transaction receipt, not a rollback engine. Compose builds
missing images for start/update under the hold; restart retains no-build policy.
An unconfirmed preparation needs explicit review before choosing fresh images.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_identity(path, *, absent=False):
    path = Path(path)
    if absent and not os.path.lexists(path):
        return None
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or path.resolve(strict=True) != path
            or info.st_size > 16 * 1024 * 1024):
        raise ValueError('native-lifecycle-input-file-required')
    return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}


def build_inputs():
    spec = importlib.util.spec_from_file_location('lifecycle_build_inputs',
        Path(__file__).with_name('pixel-native-lifecycle-inputs.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capture(install_dir, *, files, document, bind_build_inputs=True):
    paths = {install_dir / '.env', install_dir / '.compose-flags', *files}
    for category in ('configs', 'secrets'):
        for value in document.get(category, {}).values():
            if value.get('file'):
                # Compose config JSON escapes literal dollars for replay.
                path = Path(value['file'].replace('$$', '$'))
                paths.add(path if path.is_absolute() else install_dir / path)
    return {'files': {str(path): file_identity(path, absent=path.name == '.compose-flags')
        for path in sorted(paths)}, 'definitionSHA256': digest(document),
        'buildInputs': build_inputs().capture(install_dir, document, active_services(document, '')) if bind_build_inputs else None}


def service_replicas(definition):
    """Consume Compose's resolved scaling, including macOS's disabled llama.

    Compose config rejects conflicting scale/deploy.replicas before this helper
    runs. Keep that refusal for an invalid supplied document instead of choosing
    one spelling as authority.
    """
    scale = definition.get('scale')
    replicas = definition.get('deploy', {}).get('replicas')
    if scale is not None and replicas is not None and scale != replicas:
        raise ValueError('native-lifecycle-resolved-scale-conflict')
    value = scale if scale is not None else replicas if replicas is not None else 1
    if type(value) is not int or value < 0:
        raise ValueError('native-lifecycle-resolved-scale-required')
    return value


def active_services(document, service):
    definitions = document['services']
    if service and service not in definitions:
        raise ValueError('native-lifecycle-service-selection-required')
    if service:
        selected = {service} if service_replicas(definitions[service]) != 0 else set()
    else:
        selected = {name for name, value in definitions.items()
            if not value.get('profiles') and service_replicas(value) != 0}
    pending = list(selected)
    while pending:
        name = pending.pop()
        if name not in definitions:
            raise ValueError('native-lifecycle-service-selection-required')
        for dependency in definitions[name].get('depends_on', {}):
            if dependency not in selected and service_replicas(definitions[dependency]) != 0:
                selected.add(dependency)
                pending.append(dependency)
    if 'pixel-native-ingress' not in selected:
        raise ValueError('native-lifecycle-ingress-selection-required')
    if any(not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]*', name) for name in selected):
        raise ValueError('native-lifecycle-service-name-required')
    return sorted(selected)


def image_id(docker_run, reference, *, missing=False):
    result = docker_run('image', 'inspect', '--format', '{{.Id}}', reference, timeout=30)
    value = result.stdout.strip()
    if missing and result.returncode:
        return None
    if result.returncode or not re.fullmatch('sha256:[a-f0-9]{64}', value):
        raise ValueError('native-lifecycle-local-image-required')
    return value


def start_requires_pull(definition, docker_run):
    if definition.get('build') or definition.get('pull_policy') == 'never':
        return False
    reference = definition['image']
    policy = definition.get('pull_policy', 'missing')
    # Honor refresh policies before pinning the activation image. Time-based
    # policies conservatively refresh once; a retry uses the recorded image.
    refresh = policy in ('always', 'daily', 'weekly') or policy.startswith('every_')
    return (refresh or reference.endswith(':latest') and '@sha256:' not in reference
        or image_id(docker_run, reference, missing=True) is None)


def freeze(install_dir, directory, record, *, action, service, read_selection, run, docker_run, checkpoint,
           retry_preparation=False):
    files, document = read_selection()
    if document.get('name') != record['project']:
        raise ValueError('native-lifecycle-project-selection-changed')
    inputs = capture(install_dir, files=files, document=document, bind_build_inputs=action != 'restart')
    if 'lifecycleStack' in record:
        saved = record['lifecycleStack']
        if saved['inputs'] != inputs:
            raise ValueError('native-lifecycle-stack-inputs-changed')
        if saved['phase'] == 'selected':
            validate_saved(saved, docker_run=docker_run)
            return saved
        previous = record.get('preparationCommand', {})
        safe_terminal_retry = previous.get('outcome') == 'failed' and saved.get('retrySameInputs') is True
        if not retry_preparation and not safe_terminal_retry:
            raise ValueError('native-lifecycle-image-selection-unconfirmed')
        saved['explicitFreshPreparation'] = bool(retry_preparation)
        checkpoint(record)
    else:
        services = active_services(document, service)
        saved = {'phase': 'selecting-images', 'inputs': inputs, 'services': services,
            'project': record['project'], 'action': action, 'service': service, 'installDir': str(install_dir)}
        record['lifecycleStack'] = saved
        checkpoint(record)
    services = saved['services']
    if action == 'update' and not saved.get('pullComplete'):
        pull = [name for name in services if name != 'pixel-native-ingress']
        references = [document['services'][name].get('image', '') for name in pull
            if not document['services'][name].get('build')]
        saved['retrySameInputs'] = all('@sha256:' in value or value.startswith('sha256:') for value in references)
        checkpoint(record)
        result = run('pull', '--ignore-buildable', *pull, timeout=600)
        if result.returncode:
            raise ValueError('native-lifecycle-image-preparation-failed')
        saved['pullComplete'] = True
        checkpoint(record)
    if action != 'restart':
        build = [name for name in services if document['services'][name].get('build') and (
            document['services'][name].get('pull_policy') == 'build' or image_id(docker_run,
                document['services'][name].get('image') or record['project'] + '-' + name, missing=True) is None)]
        if build:
            saved['retrySameInputs'] = True
            checkpoint(record)
            result = run('build', *build, timeout=1800)
            if result.returncode:
                raise ValueError('native-lifecycle-image-preparation-failed')
        # Preserve ordinary start's missing/latest/refresh-policy pulls. Update
        # already performed its selected pull above, so do not pull twice.
        pull = [name for name in services if (action == 'start' and start_requires_pull(document['services'][name], docker_run))
            or (action == 'update' and not document['services'][name].get('build') and image_id(docker_run,
                document['services'][name]['image'], missing=True) is None)]
        if pull:
            saved['retrySameInputs'] = all('@sha256:' in document['services'][name]['image']
                or document['services'][name]['image'].startswith('sha256:') for name in pull)
            checkpoint(record)
            result = run('pull', '--ignore-buildable', *pull, timeout=600)
            if result.returncode:
                raise ValueError('native-lifecycle-image-preparation-failed')
    fresh_files, fresh_document = read_selection()
    if capture(install_dir, files=fresh_files, document=fresh_document, bind_build_inputs=action != 'restart') != inputs:
        raise ValueError('native-lifecycle-stack-inputs-changed')
    pinned = copy.deepcopy(document)
    images = {}
    for name in services:
        definition = pinned['services'][name]
        reference = definition.get('image') or record['project'] + '-' + name
        images[name] = image_id(docker_run, reference)
        definition['image'] = images[name]
    path = directory / 'lifecycle.compose.json'
    body = json.dumps(pinned, sort_keys=True).encode()
    body_hash = hashlib.sha256(body).hexdigest()
    previous_hash = file_identity(path)['sha256'] if path.exists() else None
    if previous_hash is not None and previous_hash not in (saved.get('sha256'), saved.get('previousRecipeSHA256')):
        raise ValueError('native-lifecycle-frozen-stack-changed')
    saved.update(path=str(path), sha256=body_hash, images=images, previousRecipeSHA256=previous_hash)
    checkpoint(record)
    temporary = directory / ('lifecycle-' + secrets.token_hex(8) + '.tmp')
    with temporary.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    saved['phase'] = 'selected'
    checkpoint(record)
    return saved


def validate_saved(saved, *, docker_run=None):
    for path, expected in saved['inputs']['files'].items():
        if file_identity(path, absent=expected is None) != expected:
            raise ValueError('native-lifecycle-stack-inputs-changed')
    if file_identity(saved['path'])['sha256'] != saved['sha256']:
        raise ValueError('native-lifecycle-frozen-stack-changed')
    document = json.loads(Path(saved['path']).read_text())
    if (saved['inputs']['buildInputs'] is not None and build_inputs().capture(Path(saved['installDir']),
            document, active_services(document, '')) != saved['inputs']['buildInputs']):
        raise ValueError('native-lifecycle-build-inputs-changed')
    if docker_run is not None:
        for expected in saved['images'].values():
            if image_id(docker_run, expected) != expected:
                raise ValueError('native-lifecycle-frozen-image-changed')


def verify_result(saved, docker_run, install_dir):
    validate_saved(saved, docker_run=docker_run)
    prefix = ('compose', '--project-directory', str(install_dir), '--project-name', saved['project'],
        '--env-file', '/dev/null', '-f', saved['path'])
    result = docker_run(*prefix, 'config', '--hash', '*', timeout=30)
    if result.returncode:
        raise ValueError('native-lifecycle-frozen-hashes-required')
    hashes = dict(line.split() for line in result.stdout.splitlines())
    if any(not re.fullmatch('[a-f0-9]{64}', str(hashes.get(name, ''))) for name in saved['services']):
        raise ValueError('native-lifecycle-frozen-hashes-required')
    document = json.loads(Path(saved['path']).read_text())
    for name in saved['services']:
        result = docker_run('ps', '-a', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + saved['project'],
            '--filter', 'label=com.docker.compose.service=' + name, '--format', '{{.ID}}', timeout=30)
        identity = result.stdout.strip()
        if result.returncode or not re.fullmatch('[a-f0-9]{64}', identity):
            raise ValueError('native-lifecycle-selected-container-required')
        result = docker_run('inspect', identity, timeout=30)
        if result.returncode:
            raise ValueError('native-lifecycle-selected-container-required')
        rows = json.loads(result.stdout)
        if len(rows) != 1 or rows[0].get('Id') != identity:
            raise ValueError('native-lifecycle-selected-container-required')
        observed = rows[0]
        labels = observed['Config']['Labels']
        state = observed['State']
        if (labels.get('com.docker.compose.project') != saved['project']
                or labels.get('com.docker.compose.service') != name
                or labels.get('com.docker.compose.config-hash') != hashes.get(name)
                or observed['Image'] != saved['images'][name]):
            raise ValueError('native-lifecycle-selected-container-changed')
        definition = document['services'][name]
        completed = (definition.get('restart', 'no') == 'no' and state.get('Status') == 'exited'
            and state.get('ExitCode') == 0)
        has_health = definition.get('healthcheck') and not definition['healthcheck'].get('disable')
        if not completed and (state.get('Status') != 'running'
                or has_health and state.get('Health', {}).get('Status') != 'healthy'):
            raise ValueError('native-lifecycle-selected-container-not-ready')
    return True
