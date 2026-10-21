"""Refresh only native ingress under the existing Edge admission protocol.

An immutable config digest is selected with the private environment transaction.
The exact previous Compose definition is retained for rollback, including older
bind-based ingress. Interrupted attempts require explicit --rollback; readiness
polling never provisions credentials or starts services.
"""
import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
SERVICE = 'pixel-native-ingress'


def helper(name):
    spec = importlib.util.spec_from_file_location('ingress_refresh_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_write(path, body):
    with Path(path).open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def selected_ingress(docker_run, project):
    compose = helper('pixel-native-compose')
    rows = compose._docker(docker_run, 'ps', '-a', '--no-trunc', '--filter',
        'label=com.docker.compose.project=' + project, '--filter',
        'label=com.docker.compose.service=' + SERVICE, '--format', '{{.ID}}').splitlines()
    if len(rows) > 1:
        raise ValueError('ambiguous-native-managed-ingress')
    return compose._inspect_ingress(docker_run, rows[0]) if rows else None


def edge_ready(docker_run, *, project, dashboard_key, source, allow_start=False):
    """CLI start/restart may start its exact stopped Edge before taking a hold.

    Never replace the definition here. A failed CLI start can leave this owned
    dependency running, just as the normal Compose start does; no ingress or
    config selection has changed yet, and no readiness is claimed on failure.
    """
    compose = helper('pixel-native-compose')
    try:
        return compose._managed_edge(docker_run, project)
    except ValueError:
        if not allow_start:
            raise
    identity = compose._named_ingress(docker_run, compose.EDGE_NAME)
    if identity is None:
        raise ValueError('native-managed-edge-required')
    before = compose._inspect_ingress(docker_run, identity)
    labels = before['Config'].get('Labels') or {}
    if (labels.get('com.docker.compose.project') != project
            or labels.get('com.docker.compose.service') != 'pixel-edge'
            or before['State'].get('Running') is not False
            or before['State'].get('Status') not in ('created', 'exited')):
        raise ValueError('native-owned-stopped-edge-required')
    native_idle(source)
    compose._docker(docker_run, 'start', identity)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        current = compose._inspect_ingress(docker_run, identity)
        # Docker may reorder Mounts when starting a stopped container. Preserve
        # every complete entry and duplicate; only collection order is ignored.
        current_mounts = sorted(json.dumps(value, sort_keys=True) for value in current['Mounts'])
        before_mounts = sorted(json.dumps(value, sort_keys=True) for value in before['Mounts'])
        if (any(current[key] != before[key] for key in ('Id', 'Image', 'Config', 'Name'))
                or current_mounts != before_mounts):
            raise ValueError('native-edge-definition-changed')
        try:
            if compose._managed_edge(docker_run, project) != identity:
                raise ValueError('native-edge-identity-changed')
            compose._edge_transition(docker_run, identity, dashboard_key)
            return identity
        except ValueError:
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    raise ValueError('native-edge-start-not-ready')


def native_idle(source):
    body = helper('pixel-native-env').snapshot(source)[0]
    gateway = json.loads(body)['gateway']
    port, token = gateway['port'], gateway['auth']['token']
    if type(port) is not int or not 1 <= port <= 65535 or type(token) is not str or not token:
        raise ValueError('native-ingress-gateway-selection-required')
    request = urllib.request.Request('http://127.0.0.1:' + str(port) + '/pixel-ods/access-runtime',
        headers={'Authorization': 'Bearer ' + token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10) as response:
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536:
            raise ValueError('native-ingress-idle-proof-failed')
    value = json.loads(raw)
    if (value.get('available') is not True or value.get('phase') != 'idle'
            or type(value.get('active')) is not int or value['active'] != 0):
        raise ValueError('native-ingress-active')


def snapshot_previous(run, docker_run, directory, *, install_dir, project, document, observed, source):
    """Accept a rendered recipe only if Compose hashes it exactly like the container."""
    compose = helper('pixel-native-compose')
    labels = observed['Config'].get('Labels') or {}
    if (labels.get('com.docker.compose.project') != project
            or labels.get('com.docker.compose.service') != SERVICE
            or not re.fullmatch('[a-f0-9]{64}', labels.get('com.docker.compose.config-hash', ''))
            or observed['Image'] != document['services'][SERVICE]['image']
            or observed['Config']['User'] != document['services'][SERVICE]['user']):
        raise ValueError('native-ingress-previous-selection-changed')
    previous = copy.deepcopy(document)
    definition = previous['services'][SERVICE]
    actual_env = dict(value.split('=', 1) for value in observed['Config']['Env'] if '=' in value)
    old_path = actual_env.get('PIXEL_GATEWAY_TOKEN_FILE')
    if old_path == '/run/gateway.json':
        # Compatibility is limited to the exact old two-field bind definition;
        # its actual config-hash must still match after this transformation.
        definition['environment']['PIXEL_GATEWAY_TOKEN_FILE'] = old_path
        definition['volumes'] = [value for value in definition['volumes']
            if value['target'] != '/run/pixel-config']
        # Existing config JSON is already replay-escaped. Only this newly
        # injected host path needs escaping before Compose reads it again.
        definition['volumes'].append({'type': 'bind', 'source': str(source).replace('$', '$$'),
            'target': old_path, 'read_only': True, 'bind': {'create_host_path': False}})
    elif not re.fullmatch(r'/run/pixel-config/[a-f0-9]{64}\.json', str(old_path)):
        raise ValueError('native-ingress-previous-config-required')
    else:
        definition['environment']['PIXEL_GATEWAY_TOKEN_FILE'] = old_path
    candidate = directory / 'previous-resolved.json'
    private_write(candidate, json.dumps(previous).encode())
    prefix = ('compose', '--project-directory', str(install_dir), '--project-name', project,
        '--env-file', '/dev/null', '-f', str(candidate))
    expected = SERVICE + ' ' + labels['com.docker.compose.config-hash']
    if compose._docker(docker_run, *prefix, 'config', '--hash', SERVICE).strip() != expected:
        raise ValueError('native-ingress-previous-definition-changed')
    rollback = {'name': project, 'services': {SERVICE: definition}, 'volumes': {}, 'networks': {}}
    for field in ('build', 'depends_on', 'pull_policy'):
        definition.pop(field, None)
    for mount in definition.get('volumes', []):
        matches = [value for value in observed['Mounts'] if value['Destination'] == mount['target']]
        if (len(matches) != 1 or matches[0]['Type'] != mount['type']
                or matches[0]['RW'] is not (not mount.get('read_only', False))):
            raise ValueError('native-ingress-previous-mount-changed')
        if mount['type'] == 'volume':
            name = previous['volumes'][mount['source']]['name']
            if matches[0]['Name'] != name:
                raise ValueError('native-ingress-previous-volume-changed')
            rollback['volumes'][mount['source']] = {'name': name, 'external': True}
        elif (mount['type'] != 'bind' or matches[0]['Source'] !=
                helper('pixel-native-ingress-config').rendered_literal(mount['source'])):
            raise ValueError('native-ingress-previous-bind-changed')
    for alias in definition.get('networks', {}):
        name = previous['networks'][alias]['name']
        if name not in observed['NetworkSettings']['Networks']:
            raise ValueError('native-ingress-previous-network-changed')
        rollback['networks'][alias] = {'name': name, 'external': True}
    if compose._inspect_ingress(docker_run, observed['Id']) != observed:
        # State/health may change independently. Require stable configuration,
        # mounts and identity, not an identical last-healthcheck timestamp.
        current = compose._inspect_ingress(docker_run, observed['Id'])
        # Docker may reorder Mounts between inspections. Compare every complete
        # entry, retaining duplicates and all fields, independently of order.
        current_mounts = sorted(json.dumps(value, sort_keys=True) for value in current['Mounts'])
        observed_mounts = sorted(json.dumps(value, sort_keys=True) for value in observed['Mounts'])
        if (any(current[key] != observed[key] for key in ('Id', 'Image', 'Config', 'Name'))
                or current_mounts != observed_mounts):
            raise ValueError('native-ingress-previous-container-changed')
    body = json.dumps(rollback, sort_keys=True).encode()
    path = directory / 'rollback.compose.json'
    private_write(path, body)
    return {'path': str(path), 'sha256': hashlib.sha256(body).hexdigest(), 'previousId': observed['Id'],
        'configHash': labels['com.docker.compose.config-hash'], 'image': observed['Image'],
        'user': observed['Config']['User']}


def verify_current(docker_run, record):
    observed = selected_ingress(docker_run, record['project'])
    if observed is None:
        return  # A failed recreate may have removed its predecessor.
    labels = observed['Config'].get('Labels') or {}
    recipe = record['rollback']
    if (labels.get('com.docker.compose.project') != record['project']
            or labels.get('com.docker.compose.service') != SERVICE
            or observed['Image'] != recipe['image'] or observed['Config']['User'] != recipe['user']
            or labels.get('com.docker.compose.config-hash') not in
                {recipe['configHash'], record.get('candidateHash', recipe['configHash'])}):
        raise ValueError('native-ingress-recovery-container-changed')


def rollback(run, docker_run, record, *, install_dir, dashboard_key, checkpoint):
    compose, environment = helper('pixel-native-compose'), helper('pixel-native-env')
    if record['phase'] in ('operating', 'operation-failed', 'releasing', 'ready', 'rolled-back'):
        raise ValueError('native-ingress-unreleased-refresh-required')
    native_idle(record['source'])
    edge = compose._managed_edge(docker_run, record['project'])
    verify_current(docker_run, record)
    if record['phase'] == 'releasing-rollback':
        compose.wait_ready(run, services=(SERVICE,))
        compose._edge_transition(docker_run, edge, dashboard_key, binding=record['binding'], release=True)
        record['phase'] = 'rolled-back'
        checkpoint(record)
        return
    compose._edge_transition(docker_run, edge, dashboard_key, binding=record['binding'])
    snapshot = record['rollback']
    body = environment.snapshot(snapshot['path'])[0]
    if hashlib.sha256(body).hexdigest() != snapshot['sha256']:
        raise ValueError('native-ingress-rollback-definition-changed')
    if record.get('selection'):
        helper('pixel-native-ingress-config').restore_environment(install_dir / '.env', record['selection'])
    record['phase'] = 'rolling-back'
    checkpoint(record)
    result = docker_run('compose', '--project-directory', str(install_dir), '--project-name', record['project'],
        '--env-file', '/dev/null', '-f', snapshot['path'], 'up', '-d', '--no-deps', '--force-recreate',
        '--wait', '--wait-timeout', '90', SERVICE, timeout=120)
    if result.returncode:
        raise ValueError('native-ingress-rollback-health-failed')
    # An originally broken bind may remain broken: never release/claim ready on
    # a failed restore. The old recipe and hold remain available for review.
    record['phase'] = 'releasing-rollback'
    checkpoint(record)
    compose._edge_transition(docker_run, edge, dashboard_key, binding=record['binding'], release=True)
    record['phase'] = 'rolled-back'
    checkpoint(record)


def operation_selection(operation, operation_kind, operation_key):
    if operation is None:
        if operation_kind is not None or operation_key is not None:
            raise ValueError('native-ingress-operation-required')
        return None
    if (not callable(operation) or operation_kind not in
            ('cli-start', 'cli-restart', 'cli-update', 'finalize-clients', 'retained-recovery')
            or not re.fullmatch('[a-f0-9]{64}', str(operation_key))):
        raise ValueError('native-ingress-operation-selection-required')
    return {'kind': operation_kind, 'key': operation_key}


def edge_storage(docker_run, edge):
    selected = helper('pixel-native-compose')._inspect_ingress(docker_run, edge)
    mounts = [value for value in selected['Mounts'] if value.get('Destination') == '/pixel-transition-state']
    if (len(mounts) != 1 or mounts[0].get('Type') != 'volume'
            or mounts[0].get('RW') is not True or not mounts[0].get('Name')):
        raise ValueError('native-lifecycle-transition-volume-required')
    return mounts[0]


def finish_operation(run, docker_run, record, *, operation, dashboard_key, checkpoint):
    """A caller's final work is part of the same persisted admission hold.

    Failure cannot be undone by restoring ingress alone. Leave the exact
    operation held; an explicit retry of that same operation revalidates its
    own selection and resumes here, without a new config transaction.
    """
    compose = helper('pixel-native-compose')
    native_idle(record['source'])
    verify_current(docker_run, record)
    source = helper('pixel-native-env').snapshot(record['source'])[0]
    if hashlib.sha256(source).hexdigest() != record['selection']['sha256']:
        raise ValueError('native-ingress-operation-source-changed')
    edge = compose._managed_edge(docker_run, record['project'])
    if edge_storage(docker_run, edge) != record['edgeTransitionVolume']:
        raise ValueError('native-lifecycle-transition-volume-changed')
    compose._edge_transition(docker_run, edge, dashboard_key, binding=record['binding'])
    record['phase'] = 'operating'
    checkpoint(record)
    try:
        result = operation(record['selection'], record, checkpoint)
        if helper('pixel-native-env').snapshot(record['source'])[0] != source:
            raise ValueError('native-ingress-operation-source-changed')
        # The callback may replace Edge. The persisted transition volume must
        # still answer to this exact binding before any release is possible.
        edge = compose._managed_edge(docker_run, record['project'])
        if edge_storage(docker_run, edge) != record['edgeTransitionVolume']:
            raise ValueError('native-lifecycle-transition-volume-changed')
        compose._edge_transition(docker_run, edge, dashboard_key, binding=record['binding'])
        current = selected_ingress(docker_run, record['project'])
        if (current is None or current['Config']['Labels'].get('com.docker.compose.config-hash')
                != record['candidateHash']):
            raise ValueError('native-lifecycle-ingress-selection-unconfirmed')
        compose.wait_ready(run)
        native_idle(record['source'])
        validator = getattr(operation, 'validate_result', None)
        if validator is not None:
            validator(record)
        record['phase'] = 'releasing'
        checkpoint(record)
        compose._edge_transition(docker_run, edge, dashboard_key, binding=record['binding'], release=True)
        record['phase'] = 'ready'
        checkpoint(record)
        return result
    except BaseException:
        if record['phase'] not in ('releasing', 'ready'):
            record['phase'] = 'operation-failed'
            checkpoint(record)
        raise


def refresh(run, docker_run, *, install_dir, directory, project, dashboard_key, allow_stopped_edge=False,
            operation=None, operation_kind=None, operation_key=None):
    compose, environment = helper('pixel-native-compose'), helper('pixel-native-env')
    selected_operation = operation_selection(operation, operation_kind, operation_key)
    selected = run('--profile', 'native-config-maintenance', 'config', '--format', 'json', timeout=30)
    if selected.returncode or len(selected.stdout) > 4 * 1024 * 1024:
        raise ValueError('native-ingress-compose-required')
    document = json.loads(selected.stdout)
    source = Path(helper('pixel-native-ingress-config').rendered_literal(
        document['services']['pixel-native-config-write']['labels']['org.osmantic.ods.native-config-source']))
    source_before = environment.snapshot(source)
    expected = hashlib.sha256(source_before[0]).hexdigest()
    observed = selected_ingress(docker_run, project)
    if observed is None:
        raise ValueError('native-ingress-existing-container-required')
    labels = observed['Config'].get('Labels') or {}
    if (labels.get('com.docker.compose.project') != project
            or labels.get('com.docker.compose.service') != SERVICE
            or observed['Config']['User'] != document['services'][SERVICE]['user']
            or observed['Image'] != document['services'][SERVICE]['image']):
        raise ValueError('native-ingress-previous-selection-changed')
    target = 'PIXEL_GATEWAY_TOKEN_FILE=/run/pixel-config/' + expected + '.json'
    if (selected_operation is None and target in observed['Config']['Env']
            and document['services'][SERVICE]['environment']['PIXEL_GATEWAY_TOKEN_FILE'] == target.split('=', 1)[1]):
        hashed = run('config', '--hash', SERVICE, timeout=30)
        if hashed.returncode or hashed.stdout.strip() != SERVICE + ' ' + str(labels.get('com.docker.compose.config-hash')):
            raise ValueError('native-ingress-current-definition-changed')
        # Revalidate/reuse immutable bytes without restarting anything. This
        # also supports a normal CLI start after the containers were stopped.
        staged = helper('pixel-native-ingress-config').stage(run, source, user=observed['Config']['User'],
            docker_run=docker_run, install_dir=install_dir, document=document)
        if staged['sha256'] != expected or environment.snapshot(source) != source_before:
            raise ValueError('native-ingress-config-source-changed')
        return {'phase': 'unchanged', 'sha256': expected,
            'environmentAfterSha256': hashlib.sha256(environment.snapshot(install_dir / '.env')[0]).hexdigest()}
    if os.path.lexists(directory / 'refresh.json'):
        raise ValueError('native-ingress-refresh-journal-requires-review')
    recipe = snapshot_previous(run, docker_run, directory, install_dir=install_dir, project=project,
        document=document, observed=observed, source=source)
    native_idle(source)
    edge = edge_ready(docker_run, project=project, dashboard_key=dashboard_key, source=source,
        allow_start=allow_stopped_edge)
    transition = compose._edge_transition(docker_run, edge, dashboard_key)
    record = {'schemaVersion': 1, 'phase': 'acquiring', 'project': project, 'source': str(source),
        'rollback': recipe, 'binding': {'token': secrets.token_hex(32), 'revision': transition['revision']}}
    if selected_operation is not None:
        record['operation'] = selected_operation
        record['edgeTransitionVolume'] = edge_storage(docker_run, edge)
    def checkpoint(value):
        temporary = directory / '.refresh.tmp'
        private_write(temporary, (json.dumps(value, sort_keys=True) + '\n').encode())
        os.replace(temporary, directory / 'refresh.json')
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    checkpoint(record)
    try:
        compose._edge_transition(docker_run, edge, dashboard_key, binding=record['binding'])
        native_idle(source)
        if environment.snapshot(source) != source_before:
            raise ValueError('native-ingress-config-source-changed')
        record['phase'] = 'held'
        checkpoint(record)
        def selected_checkpoint(value):
            record['selection'] = value
            checkpoint(record)
        record['selection'] = helper('pixel-native-ingress-config').prepare(run,
            docker_run=docker_run,
            environment_path=install_dir / '.env', backup=directory / 'environment.before',
            checkpoint=selected_checkpoint)
        record['phase'] = 'recreating'
        hashed = run('config', '--hash', SERVICE, timeout=30)
        fields = hashed.stdout.strip().split()
        if hashed.returncode or len(fields) != 2 or fields[0] != SERVICE or not re.fullmatch('[a-f0-9]{64}', fields[1]):
            raise ValueError('native-ingress-candidate-definition-required')
        record['candidateHash'] = fields[1]
        checkpoint(record)
        # The CLI body owns its one requested activation. Python callers need
        # ingress refreshed before their client/protected finalization body.
        if selected_operation is None or not operation_kind.startswith('cli-'):
            result = run('up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '90', SERVICE, timeout=120)
            if result.returncode:
                raise ValueError('native-ingress-refresh-health-failed')
        if environment.snapshot(source) != source_before:
            raise ValueError('native-ingress-config-source-changed')
        if selected_operation is not None:
            result = finish_operation(run, docker_run, record, operation=operation,
                dashboard_key=dashboard_key, checkpoint=checkpoint)
            return {'phase': 'ready', 'sha256': record['selection']['sha256'],
                'environmentAfterSha256': record['selection']['environmentAfterSha256'],
                'operationResult': result}
        compose._edge_transition(docker_run, edge, dashboard_key, binding=record['binding'])
        record['phase'] = 'releasing'
        checkpoint(record)
        compose._edge_transition(docker_run, edge, dashboard_key, binding=record['binding'], release=True)
        record['phase'] = 'ready'
        checkpoint(record)
        return {'phase': 'ready', 'sha256': record['selection']['sha256'],
            'environmentAfterSha256': record['selection']['environmentAfterSha256']}
    except BaseException:
        # Never roll back across a release that might have admitted new work.
        if record['phase'] not in ('operating', 'operation-failed', 'releasing', 'ready'):
            rollback(run, docker_run, record, install_dir=install_dir, dashboard_key=dashboard_key, checkpoint=checkpoint)
        raise


def attempt_directory(preparation):
    """Called with the existing owner-selection lock held."""
    environment = helper('pixel-native-env')
    pointer = preparation / 'ingress-refresh-current.json'
    if os.path.lexists(pointer):
        previous = json.loads(environment.snapshot(pointer)[0])
        name = previous.get('directory', '')
        if not re.fullmatch('ingress-refresh-[a-f0-9]{32}', name):
            raise ValueError('native-ingress-refresh-pointer-invalid')
        journal = preparation / name / 'refresh.json'
        if (not journal.exists() or json.loads(environment.snapshot(journal)[0]).get('phase')
                not in ('ready', 'rolled-back', 'not-started')):
            raise ValueError('native-ingress-refresh-journal-requires-review')
    directory = preparation / ('ingress-refresh-' + secrets.token_hex(16))
    directory.mkdir(mode=0o700)
    temporary = preparation / '.ingress-refresh-current.tmp'
    private_write(temporary, json.dumps({'directory': directory.name}).encode())
    os.replace(temporary, pointer)
    return directory


def refresh_locked(run, docker_run, *, install_dir, project, dashboard_key, allow_stopped_edge=False,
                   operation=None, operation_kind=None, operation_key=None):
    """The retained-recovery caller already owns preparation/.selection.lock."""
    preparation = install_dir / 'data/pixel-native/preparation'
    selected_operation = operation_selection(operation, operation_kind, operation_key)
    pointer = preparation / 'ingress-refresh-current.json'
    if selected_operation is not None and os.path.lexists(pointer):
        environment = helper('pixel-native-env')
        previous = json.loads(environment.snapshot(pointer)[0])
        name = previous.get('directory', '')
        if not re.fullmatch('ingress-refresh-[a-f0-9]{32}', name):
            raise ValueError('native-ingress-refresh-pointer-invalid')
        journal = preparation / name / 'refresh.json'
        record = json.loads(environment.snapshot(journal)[0])
        if record.get('phase') in ('operating', 'operation-failed'):
            if (record.get('operation') != selected_operation or record.get('project') != project
                    or Path(record['rollback']['path']) != journal.parent / 'rollback.compose.json'):
                raise ValueError('native-ingress-operation-selection-changed')
            def checkpoint(value):
                temporary = journal.parent / '.refresh.tmp'
                private_write(temporary, (json.dumps(value, sort_keys=True) + '\n').encode())
                os.replace(temporary, journal)
            result = finish_operation(run, docker_run, record, operation=operation,
                dashboard_key=dashboard_key, checkpoint=checkpoint)
            return {'phase': 'ready', 'sha256': record['selection']['sha256'],
                'environmentAfterSha256': record['selection']['environmentAfterSha256'],
                'operationResult': result}
    directory = attempt_directory(preparation)
    try:
        result = refresh(run, docker_run, install_dir=install_dir, directory=directory,
            project=project, dashboard_key=dashboard_key, allow_stopped_edge=allow_stopped_edge,
            operation=operation, operation_kind=operation_kind, operation_key=operation_key)
    except BaseException:
        if not (directory / 'refresh.json').exists():
            private_write(directory / 'refresh.json', b'{"phase":"not-started"}\n')
        raise
    if result['phase'] == 'unchanged':
        private_write(directory / 'refresh.json', b'{"phase":"ready","unchanged":true}\n')
    return result


def refresh_installed(install_dir, *, restore=False, finish_release=False, selection_locked=False,
                      allow_stopped_edge=False, operation=None, operation_kind=None, operation_key=None,
                      lifecycle_action=None, lifecycle_service='', lifecycle_bash=None, retry_image_preparation=False):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    install_dir = Path(install_dir).resolve(strict=True)
    installer = helper('pixel-macos-access-install')
    compose = helper('pixel-native-compose')
    owner = pwd.getpwuid(os.getuid())
    _, source_env, _, _, _, _ = installer._source_gateway(
        installer._launchd.GATEWAY_PLIST, owner.pw_name, 18789)
    transport = {key: source_env[name] for key, name in (
        ('docker', 'PIXEL_HISTORY_DOCKER'), ('project', 'PIXEL_HISTORY_PROJECT'),
        ('image', 'PIXEL_HISTORY_IMAGE'), ('user', 'PIXEL_HISTORY_USER'))}
    installer._native_transport_environment(transport, owner)
    endpoint = source_env.get('DOCKER_HOST', '')
    if not endpoint.startswith('unix:///') or not Path(endpoint[7:]).is_socket():
        raise ValueError('installed-local-docker-socket-required')
    process_env = {key: value for key, value in os.environ.items()
        if key not in ('DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH', 'DOCKER_HOST')}
    process_env['DOCKER_HOST'] = endpoint
    tokens = helper('pixel-native-finalize').compose_flags(install_dir, process_env)
    original = [install_dir / value for value in tokens[1::2]]
    resolved = [install_dir / value for value in helper('pixel-native-stack').resolve_files(install_dir, tokens[1::2])]
    command = [transport['docker'], 'compose', '--project-directory', str(install_dir),
        '--project-name', transport['project'], '--env-file', str(install_dir / '.env')]
    for path in resolved:
        command.extend(['-f', str(path)])
    def docker_run(*args, timeout=30, input=None):
        return subprocess.run([transport['docker'], *args], cwd=install_dir, env=process_env,
            capture_output=True, text=True, timeout=timeout, input=input)
    def run(*args, timeout=30, input=None):
        compose.validate_stack(install_dir, original)
        compose.validate_stack(install_dir, resolved)
        return subprocess.run([*command, *args], cwd=install_dir, env=process_env,
            capture_output=True, text=True, timeout=timeout, input=input)
    def read_lifecycle_selection():
        fresh_tokens = helper('pixel-native-finalize').compose_flags(install_dir, process_env)
        fresh_files = helper('pixel-native-stack').resolve_files(install_dir, fresh_tokens[1::2])
        if fresh_tokens != tokens or [install_dir / value for value in fresh_files] != resolved:
            raise ValueError('native-lifecycle-stack-selection-changed')
        result = run('config', '--format', 'json', timeout=30)
        if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
            raise ValueError('native-lifecycle-stack-definition-required')
        return [*original, *resolved], json.loads(result.stdout)
    def prepare(*args, record, checkpoint, directory, descriptor, timeout):
        compose.validate_stack(install_dir, original)
        compose.validate_stack(install_dir, resolved)
        code = helper('pixel-native-lifecycle').execute([*command, *args], env=process_env,
            descriptor=descriptor, record=record, field='preparationCommand', checkpoint=checkpoint,
            directory=directory, timeout=timeout)
        return subprocess.CompletedProcess(args, code, '', '')
    run.prepare = prepare
    preparation = install_dir / 'data/pixel-native/preparation'
    info = preparation.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or preparation.resolve() != preparation:
        raise ValueError('private-native-preparation-required')
    descriptor = os.open(preparation / '.selection.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError('private-native-selection-lock-required')
        if not selection_locked:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        key = installer._env_file(install_dir / '.env')['DASHBOARD_API_KEY']
        if lifecycle_action is not None:
            if operation is not None or selection_locked or restore or finish_release:
                raise ValueError('native-ingress-cli-operation-conflict')
            lifecycle = helper('pixel-native-lifecycle')
            operation, operation_kind, operation_key = lifecycle.cli_operation(
                install_dir=install_dir, action=lifecycle_action, service=lifecycle_service,
                bash=lifecycle_bash, descriptor=descriptor, process_env=process_env,
                read_selection=read_lifecycle_selection, run=run, docker_run=docker_run,
                retry_image_preparation=retry_image_preparation, docker_binary=transport['docker'])
        elif retry_image_preparation:
            raise ValueError('native-lifecycle-action-required-for-preparation-retry')
        if not restore and not finish_release:
            return refresh_locked(run, docker_run, install_dir=install_dir,
                project=transport['project'], dashboard_key=key, allow_stopped_edge=allow_stopped_edge,
                operation=operation, operation_kind=operation_kind, operation_key=operation_key)
        environment = helper('pixel-native-env')
        pointer = json.loads(environment.snapshot(preparation / 'ingress-refresh-current.json')[0])
        name = pointer.get('directory', '')
        if not re.fullmatch('ingress-refresh-[a-f0-9]{32}', name):
            raise ValueError('native-ingress-refresh-pointer-invalid')
        directory = preparation / name
        record = json.loads(environment.snapshot(directory / 'refresh.json')[0])
        if record.get('project') != transport['project'] or Path(record['rollback']['path']) != directory / 'rollback.compose.json':
            raise ValueError('native-ingress-refresh-selection-changed')
        def checkpoint(value):
            temporary = directory / '.refresh.tmp'
            private_write(temporary, json.dumps(value).encode())
            os.replace(temporary, directory / 'refresh.json')
        if finish_release:
            if record.get('phase') != 'releasing':
                raise ValueError('native-ingress-release-pending-required')
            native_idle(record['source'])
            verify_current(docker_run, record)
            edge = compose._managed_edge(docker_run, record['project'])
            if record.get('operation'):
                if (hashlib.sha256(environment.snapshot(record['source'])[0]).hexdigest() != record['selection']['sha256']
                        or edge_storage(docker_run, edge) != record['edgeTransitionVolume']):
                    raise ValueError('native-lifecycle-release-selection-changed')
                current = selected_ingress(docker_run, record['project'])
                if current is None or current['Config']['Labels'].get('com.docker.compose.config-hash') != record['candidateHash']:
                    raise ValueError('native-lifecycle-ingress-selection-unconfirmed')
                if record['operation']['kind'].startswith('cli-'):
                    saved = record['lifecycleStack']
                    authority = record['childAuthority']
                    lifecycle = helper('pixel-native-lifecycle')
                    source = authority['source']
                    if lifecycle.source_binding(install_dir, Path(source['bash']), saved['action'], saved['service'])[1] != authority['sourceKey']:
                        raise ValueError('native-lifecycle-source-changed')
                    files, document = read_lifecycle_selection()
                    stack = helper('pixel-native-lifecycle-stack')
                    if stack.capture(install_dir, files=files, document=document,
                            bind_build_inputs=saved['action'] != 'restart') != saved['inputs']:
                        raise ValueError('native-lifecycle-stack-selection-changed')
                    stack.verify_result(saved, docker_run, install_dir)
                compose.wait_ready(run)
            else:
                compose.wait_ready(run, services=(SERVICE,))
            compose._edge_transition(docker_run, edge, key, binding=record['binding'], release=True)
            record['phase'] = 'ready'
            checkpoint(record)
        else:
            rollback(run, docker_run, record, install_dir=install_dir, dashboard_key=key, checkpoint=checkpoint)
        return {'phase': record['phase']}
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--start-stopped-edge', action='store_true',
        help='For an explicit CLI start/restart only: start the exact owned stopped Edge before acquiring admission')
    parser.add_argument('--lifecycle-action', choices=('start', 'restart', 'update'))
    parser.add_argument('--lifecycle-service', choices=('', SERVICE), default='')
    parser.add_argument('--lifecycle-bash')
    parser.add_argument('--retry-image-preparation', action='store_true',
        help='After reviewing a failed/unconfirmed preparation log, explicitly allow fresh image preparation from the unchanged inputs')
    recovery = parser.add_mutually_exclusive_group()
    recovery.add_argument('--rollback', action='store_true')
    recovery.add_argument('--finish-release', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(refresh_installed(args.install_dir, restore=args.rollback,
            finish_release=args.finish_release, allow_stopped_edge=args.start_stopped_edge,
            lifecycle_action=args.lifecycle_action, lifecycle_service=args.lifecycle_service,
            lifecycle_bash=args.lifecycle_bash, retry_image_preparation=args.retry_image_preparation)))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        print('Native ingress refresh stopped. Preserve the private ingress-refresh journal. '
            'A failed lifecycle remains held; review its private log and explicitly retry the same operation. '
            'If image preparation is unconfirmed, prove the prior child stopped, review its log, then repeat this command with --retry-image-preparation to allow fresh preparation. '
            'A release with an unknown reply requires --finish-release. '
            'Use --rollback only for a configuration-only attempt that never began final lifecycle work.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
