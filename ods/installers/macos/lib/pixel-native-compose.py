"""Two-phase native infrastructure startup for the owner-side installer.

The caller supplies its resolved Compose runner, selected project/environment
and private dashboard credential. No gateway is activated by this module.
"""
import json
import re
import secrets
import time


SERVICES = ('pixel-native-ingress', 'pixel-workspace-preview', 'pixel-edge')
INGRESS_NAME = 'ods-pixel-native-ingress'
EDGE_NAME = 'ods-pixel-edge'
PREVIEW_NAME = 'ods-pixel-workspace-preview'
TRANSITION_PROBE = '''import json,sys,urllib.request
payload=json.load(sys.stdin)
binding=payload.get('binding')
request=urllib.request.Request('http://127.0.0.1:9595/v1/transition'+('/acquire' if binding else ''),
    data=json.dumps(binding).encode() if binding else None,
    headers={'Authorization':'Bearer '+payload['key'],'Content-Type':'application/json'})
with urllib.request.urlopen(request,timeout=3) as response:
    value=response.read(65537)
    if len(value)>65536: raise ValueError('oversized response')
    sys.stdout.buffer.write(value)
'''


def start_infrastructure(run, *, dashboard_key, admission=None):
    """Start Docker-side listeners before the native gateway can be healthy."""
    if not isinstance(dashboard_key, str) or not re.fullmatch('[a-f0-9]{64}', dashboard_key):
        raise ValueError('native-compose-dashboard-key-required')
    if admission is not None and (type(admission) is not dict or set(admission) != {'token', 'revision'}
            or any(type(value) is not str or not re.fullmatch('[a-f0-9]{64}', value)
                for value in admission.values())):
        raise ValueError('native-compose-approved-admission-required')
    result = run('up', '-d', '--build', *SERVICES, timeout=240)
    if result.returncode:
        raise ValueError('native-compose-start-failed')
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        result = run('exec', '-T', 'pixel-edge', 'python3', '-c', TRANSITION_PROBE,
            input=json.dumps({'key': dashboard_key, **({'binding': admission} if admission else {})}), timeout=5)
        if result.returncode == 0:
            try:
                if len(result.stdout) > 65536:
                    raise ValueError()
                value = json.loads(result.stdout)
                if (value.get('capability') == 'available'
                        and value.get('phase') == ('held' if admission else 'idle')
                        and value.get('streams') == 0 and value.get('admission_blocked') is bool(admission)
                        and re.fullmatch('[a-f0-9]{64}', str(value.get('revision', '')))
                        and (not admission or value['revision'] == admission['revision'])):
                    return {'phase': 'infrastructure-ready', 'requiresGatewayActivation': True,
                        **({'admissionHeld': True} if admission else {})}
            except (ValueError, AttributeError):
                pass
        time.sleep(0.2)
    raise ValueError('native-compose-admission-unavailable')


def wait_ready(run, *, services=SERVICES):
    """Require all native Docker services healthy after protected activation."""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        result = run('ps', '--format', 'json', *services, timeout=10)
        if result.returncode == 0:
            try:
                if len(result.stdout) > 65536:
                    raise ValueError()
                body = result.stdout.strip()
                rows = json.loads(body) if body.startswith('[') else [json.loads(line) for line in body.splitlines()]
                if (type(rows) is list and len(rows) == len(services)
                        and all(type(row) is dict for row in rows)
                        and {row.get('Service') for row in rows} == set(services)
                        and all(row.get('State') == 'running' and row.get('Health') == 'healthy' for row in rows)):
                    return {'phase': 'docker-ready'}
            except (ValueError, TypeError):
                pass
        time.sleep(1)
    raise ValueError('native-compose-health-timeout')


def _docker(run, *args):
    result = run(*args, timeout=45)
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError('native-ingress-docker-command-failed')
    return result.stdout


def _inspect_ingress(run, identity):
    if not re.fullmatch('[a-f0-9]{64}', identity):
        raise ValueError('native-ingress-container-id-required')
    rows = json.loads(_docker(run, 'inspect', identity))
    if type(rows) is not list or len(rows) != 1 or rows[0].get('Id') != identity:
        raise ValueError('native-ingress-container-changed')
    return rows[0]


def _named_ingress(run, name):
    rows = _docker(run, 'ps', '-a', '--no-trunc', '--filter', 'name=^/' + name + '$', '--format', '{{.ID}}').splitlines()
    if len(rows) > 1 or any(not re.fullmatch('[a-f0-9]{64}', row) for row in rows):
        raise ValueError('native-ingress-name-ambiguous')
    return rows[0] if rows else None


def _ingress_selection(image, user, project, transaction):
    if (any(type(value) is not str for value in (image, user, project, transaction))
            or not re.fullmatch('sha256:[a-f0-9]{64}', image)
            or not re.fullmatch('[1-9][0-9]*:[0-9]+', user)
            or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,127}', project)
            or not re.fullmatch('[a-f0-9]{64}', transaction)):
        raise ValueError('qualified-native-ingress-selection-required')


def legacy_storage_override(*, ingress, preview, edge, project, workspace):
    """Reuse inspected volumes, including transition state, without copying data.

    The coordinator must hold admission and stop the old writer before starting
    a replacement against this storage. External volumes survive Compose down.
    """
    if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,127}', project):
        raise ValueError('qualified-native-project-required')
    for document, name, service in ((ingress, INGRESS_NAME, None),
            (preview, PREVIEW_NAME, 'pixel-workspace-preview'),
            (edge, EDGE_NAME, 'pixel-edge')):
        labels = document.get('Config', {}).get('Labels') or {}
        if (not re.fullmatch('[a-f0-9]{64}', document.get('Id', ''))
                or document.get('Name') != '/' + name
                or service is None and (labels.get('com.docker.compose.project')
                    or labels.get('com.docker.compose.service'))
                or service is not None and (labels.get('com.docker.compose.project') != project
                    or labels.get('com.docker.compose.service') != service)):
            raise ValueError('qualified-legacy-storage-container-required')
    def mount(document, destination, writable, kind='volume'):
        matches = [value for value in document.get('Mounts', [])
            if value.get('Destination') == destination]
        if (len(matches) != 1 or matches[0].get('Type') != kind
                or matches[0].get('RW') is not writable):
            raise ValueError('qualified-legacy-storage-mount-required')
        value = matches[0].get('Name' if kind == 'volume' else 'Source', '')
        if kind == 'volume' and not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]*', value):
            raise ValueError('qualified-legacy-volume-required')
        return value
    if mount(preview, '/workspace', False, 'bind') != str(workspace):
        raise ValueError('legacy-workspace-mount-mismatch')
    runtime = mount(ingress, '/runtime', True)
    previews = mount(preview, '/previews', True)
    preview_runtime = mount(preview, '/run/ods-pixel-preview', True)
    transitions = mount(edge, '/pixel-transition-state', True)
    if (mount(edge, '/pixel-runtime', False) != runtime
            or mount(edge, '/pixel-preview-runtime', False) != preview_runtime
            or len({runtime, previews, preview_runtime, transitions}) != 4):
        raise ValueError('legacy-storage-topology-mismatch')
    return {'volumes': {name: {'external': True, 'name': volume} for name, volume in (
        ('pixel-native-runtime', runtime), ('pixel-native-previews', previews),
        ('pixel-native-preview-runtime', preview_runtime), ('pixel-transition-state', transitions))}}


def retain_legacy_ingress(run, *, identity, image, user, project, transaction, checkpoint):
    """Retain the exact unmanaged container; never delete its data or image.

    Call only with a qualified replacement configuration and held admission.
    The owner coordinator persists checkpoints privately before each mutation.
    """
    _ingress_selection(image, user, project, transaction)
    before = _inspect_ingress(run, identity)
    labels = before['Config'].get('Labels') or {}
    if (before['Name'] != '/' + INGRESS_NAME or before['Image'] != image
            or before['Config']['User'] != user or type(before['State']['Running']) is not bool
            or labels.get('com.docker.compose.project') or labels.get('com.docker.compose.service')):
        raise ValueError('qualified-unmanaged-ingress-required')
    backup = INGRESS_NAME + '-before-' + transaction[:12]
    if _named_ingress(run, backup) is not None:
        raise ValueError('native-ingress-backup-name-exists')
    record = {'schemaVersion': 1, 'name': INGRESS_NAME, 'backup': backup, 'transaction': transaction,
        'project': project, 'image': image, 'user': user, 'previousId': identity,
        'previousRunning': before['State']['Running'], 'requiresRecovery': True}
    checkpoint(dict(record, phase='stopping-legacy'))
    if record['previousRunning']:
        _docker(run, 'stop', '--time', '20', identity)
    stopped = _inspect_ingress(run, identity)
    if stopped['State']['Running'] is not False or stopped['Name'] != '/' + INGRESS_NAME:
        raise ValueError('native-ingress-stop-unconfirmed')
    checkpoint(dict(record, phase='renaming-legacy'))
    _docker(run, 'rename', identity, backup)
    if _inspect_ingress(run, identity)['Name'] != '/' + backup:
        raise ValueError('native-ingress-retention-unconfirmed')
    record['phase'] = 'legacy-retained'
    checkpoint(record)
    return record


def restore_legacy_ingress(run, record, *, checkpoint):
    """Restore the retained ID without touching the separate Compose ingress."""
    _ingress_selection(record['image'], record['user'], record['project'], record['transaction'])
    if (type(record.get('schemaVersion')) is not int or record['schemaVersion'] != 1
            or record.get('name') != INGRESS_NAME or type(record.get('previousRunning')) is not bool
            or record.get('backup') != INGRESS_NAME + '-before-' + record['transaction'][:12]):
        raise ValueError('native-ingress-recovery-record-invalid')
    record = dict(record)
    old = _inspect_ingress(run, record['previousId'])
    if (old['Image'] != record['image'] or old['Config']['User'] != record['user']
            or old['Name'] not in ('/' + INGRESS_NAME, '/' + record['backup'])
            or not record['previousRunning'] and old['State']['Running'] is not False):
        raise ValueError('retained-native-ingress-changed')
    selected = _named_ingress(run, INGRESS_NAME)
    if selected is not None and selected != record['previousId']:
        raise ValueError('native-ingress-name-occupied')
    checkpoint(dict(record, phase='restoring-legacy'))
    if old['Name'] != '/' + INGRESS_NAME:
        _docker(run, 'rename', record['previousId'], INGRESS_NAME)
    if record['previousRunning']:
        _docker(run, 'start', record['previousId'])
    restored = _inspect_ingress(run, record['previousId'])
    if restored['Name'] != '/' + INGRESS_NAME or restored['State']['Running'] != record['previousRunning']:
        raise ValueError('native-ingress-restore-unconfirmed')
    record.update(phase='restored', requiresRecovery=False)
    checkpoint(record)
    return record


def _edge_transition(run, identity, dashboard_key, *, binding=None, release=False):
    if (not re.fullmatch('[a-f0-9]{64}', identity)
            or not re.fullmatch('[a-f0-9]{64}', dashboard_key)):
        raise ValueError('qualified-native-edge-required')
    program = TRANSITION_PROBE.replace('/acquire', '/release') if release else TRANSITION_PROBE
    result = run('exec', '-i', identity, 'python3', '-c', program,
        input=json.dumps({'key': dashboard_key, **({'binding': binding} if binding else {})}), timeout=10)
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError('native-edge-transition-request-failed')
    value = json.loads(result.stdout)
    held = binding is not None and not release
    if (type(value) is not dict or value.get('capability') != 'available'
            or value.get('phase') != ('held' if held else 'idle')
            or value.get('streams') != 0 or value.get('admission_blocked') is not held
            or not re.fullmatch('[a-f0-9]{64}', str(value.get('revision', '')))
            or held and value['revision'] != binding['revision']):
        raise ValueError('native-edge-transition-unconfirmed')
    return value


def _managed_edge(run, project):
    identity = _named_ingress(run, EDGE_NAME)
    if identity is None:
        raise ValueError('native-managed-edge-required')
    document = _inspect_ingress(run, identity)
    labels = document.get('Config', {}).get('Labels') or {}
    if (labels.get('com.docker.compose.project') != project
            or labels.get('com.docker.compose.service') != 'pixel-edge'
            or document.get('State', {}).get('Running') is not True):
        raise ValueError('native-managed-edge-required')
    return identity


def _managed_ingress(run, *, project, image, user, volume):
    rows = _docker(run, 'ps', '-a', '--no-trunc', '--filter',
        'label=com.docker.compose.project=' + project, '--filter',
        'label=com.docker.compose.service=pixel-native-ingress', '--format', '{{.ID}}').splitlines()
    if len(rows) > 1:
        raise ValueError('ambiguous-native-managed-ingress')
    if not rows:
        return None
    document = _inspect_ingress(run, rows[0])
    labels = document.get('Config', {}).get('Labels') or {}
    mounts = [item for item in document.get('Mounts', []) if item.get('Destination') == '/runtime']
    if (document.get('Image') != image or document.get('Config', {}).get('User') != user
            or labels.get('com.docker.compose.project') != project
            or labels.get('com.docker.compose.service') != 'pixel-native-ingress'
            or len(mounts) != 1 or mounts[0].get('Type') != 'volume'
            or mounts[0].get('Name') != volume or mounts[0].get('RW') is not True):
        raise ValueError('native-managed-ingress-selection-changed')
    return document


def migrate_infrastructure(run, docker_run, *, ingress, image, user, project,
                           transaction, storage, workspace, dashboard_key, checkpoint):
    """Swap Docker writers under a persisted admission hold, leaving it held.

    Callers supply the qualified Compose selection and a durable private journal
    writer. They must arrange rollback of the previous Compose definitions before
    invoking this operation. Failures never infer rollback or release admission.
    """
    _ingress_selection(image, user, project, transaction)
    old_edge = _managed_edge(docker_run, project)
    preview = _named_ingress(docker_run, PREVIEW_NAME)
    if preview is None:
        raise ValueError('native-managed-preview-required')
    observed = legacy_storage_override(ingress=_inspect_ingress(docker_run, ingress),
        edge=_inspect_ingress(docker_run, old_edge), preview=_inspect_ingress(docker_run, preview),
        project=project, workspace=workspace)
    if observed != storage:
        raise ValueError('native-migration-storage-changed')
    ingress_selection = dict(project=project, image=image, user=user,
        volume=storage['volumes']['pixel-native-runtime']['name'])
    if _managed_ingress(docker_run, **ingress_selection) is not None:
        raise ValueError('existing-native-managed-ingress-requires-review')
    before = _edge_transition(docker_run, old_edge, dashboard_key)
    binding = {'token': secrets.token_hex(32), 'revision': before['revision']}
    record = {'schemaVersion': 1, 'phase': 'acquiring-admission', 'requiresRecovery': True,
        'transaction': transaction, 'project': project, 'previousEdge': old_edge, 'binding': binding,
        'previousIngress': ingress, 'ingressSelection': ingress_selection,
        'storage': storage, 'workspace': str(workspace)}
    checkpoint(dict(record))
    try:
        _edge_transition(docker_run, old_edge, dashboard_key, binding=binding)
        record['phase'] = 'admission-held'
        checkpoint(dict(record))
        def retain_checkpoint(value):
            record.update(phase='retaining-legacy-ingress', legacyIngress=value)
            checkpoint(dict(record))
        retain_legacy_ingress(docker_run, identity=ingress, image=image, user=user,
            project=project, transaction=transaction, checkpoint=retain_checkpoint)
        record['phase'] = 'starting-native-infrastructure'
        checkpoint(dict(record))
        start_infrastructure(run, dashboard_key=dashboard_key, admission=binding)
        candidate = _managed_ingress(docker_run, **ingress_selection)
        if candidate is None or candidate.get('State', {}).get('Running') is not True:
            raise ValueError('native-managed-ingress-unavailable')
        record['ingress'] = candidate['Id']
        replacement_edge = _managed_edge(docker_run, project)
        _edge_transition(docker_run, replacement_edge, dashboard_key, binding=binding)
        record.update(phase='verifying-native-infrastructure', edge=replacement_edge)
        checkpoint(dict(record))
        wait_ready(run)
        record['phase'] = 'infrastructure-ready-held'
        checkpoint(dict(record))
    except BaseException:
        record['failedPhase'] = record['phase']
        record['phase'] = 'recovery-required'
        checkpoint(dict(record))
        raise
    return record


def restore_migration_infrastructure(previous_run, docker_run, record, *, dashboard_key, checkpoint):
    """Restore approved Compose definitions while retaining admission custody.

    previous_run must use the digest-verified private rollback Compose snapshot.
    A handover whose release has begun must finish that release, never roll back
    across potentially admitted requests.
    """
    phases = {'acquiring-admission', 'admission-held', 'retaining-legacy-ingress',
        'starting-native-infrastructure', 'verifying-native-infrastructure',
        'infrastructure-ready-held', 'restoring-previous-infrastructure', 'releasing-rollback-admission'}
    phase = record.get('failedPhase') if record.get('phase') == 'recovery-required' else record.get('phase')
    if phase not in phases or record.get('requiresRecovery') is not True:
        raise ValueError('unreleased-native-handover-required')
    record = dict(record)
    if phase != 'releasing-rollback-admission':
        record['phase'] = 'restoring-previous-infrastructure'
        checkpoint(dict(record))
        # Bring back the transition endpoint even if the replacement Edge failed.
        # Its approved definition uses the same held transition volume.
        if previous_run('up', '-d', '--no-deps', 'pixel-edge', timeout=120).returncode:
            raise ValueError('previous-native-edge-restore-failed')
        edge = _managed_edge(docker_run, record['project'])
        _edge_transition(docker_run, edge, dashboard_key, binding=record['binding'])
        candidate = _managed_ingress(docker_run, **record['ingressSelection'])
        if candidate is not None:
            _docker(docker_run, 'stop', '--time', '20', candidate['Id'])
            if _inspect_ingress(docker_run, candidate['Id'])['State']['Running'] is not False:
                raise ValueError('candidate-native-ingress-stop-unconfirmed')
        if record.get('legacyIngress'):
            def retain_checkpoint(value):
                record['legacyIngress'] = value
                checkpoint(dict(record))
            restore_legacy_ingress(docker_run, record['legacyIngress'], checkpoint=retain_checkpoint)
        if previous_run('up', '-d', '--no-deps', 'pixel-workspace-preview', timeout=120).returncode:
            raise ValueError('previous-native-preview-restore-failed')
        preview = _named_ingress(docker_run, PREVIEW_NAME)
        if preview is None:
            raise ValueError('previous-native-preview-unavailable')
        observed = legacy_storage_override(ingress=_inspect_ingress(docker_run, record['previousIngress']),
            edge=_inspect_ingress(docker_run, edge), preview=_inspect_ingress(docker_run, preview),
            project=record['project'], workspace=record['workspace'])
        if observed != record['storage']:
            raise ValueError('restored-native-storage-mismatch')
        record['edge'] = edge
        _edge_transition(docker_run, edge, dashboard_key, binding=record['binding'])
    elif _managed_edge(docker_run, record['project']) != record['edge']:
        raise ValueError('native-migration-edge-changed')
    wait_ready(previous_run, services=('pixel-edge', 'pixel-workspace-preview'))
    record['phase'] = 'releasing-rollback-admission'
    checkpoint(dict(record))
    _edge_transition(docker_run, record['edge'], dashboard_key, binding=record['binding'], release=True)
    record.update(phase='infrastructure-rolled-back', requiresRecovery=False)
    checkpoint(dict(record))
    return record


def finish_migration_infrastructure(run, docker_run, record, *, dashboard_key, checkpoint):
    """Release a qualified Docker handover, retrying only its recorded token.

    A ready Docker handover is not a completed native runtime migration. The
    protected runtime executor subsequently takes its own admission hold.
    """
    if (record.get('phase') not in ('infrastructure-ready-held', 'releasing-admission')
            or record.get('requiresRecovery') is not True
            or type(record.get('binding')) is not dict
            or set(record['binding']) != {'token', 'revision'}
            or any(type(value) is not str or not re.fullmatch('[a-f0-9]{64}', value)
                for value in record['binding'].values())):
        raise ValueError('held-native-infrastructure-required')
    record = dict(record)
    if _managed_edge(docker_run, record['project']) != record['edge']:
        raise ValueError('native-migration-edge-changed')
    wait_ready(run)
    if record['phase'] == 'infrastructure-ready-held':
        _edge_transition(docker_run, record['edge'], dashboard_key, binding=record['binding'])
        record['phase'] = 'releasing-admission'
        checkpoint(dict(record))
    # After a lost release reply, reacquiring could conflict with the advanced
    # revision. Repeat the original release, which the Edge protocol remembers.
    _edge_transition(docker_run, record['edge'], dashboard_key, binding=record['binding'], release=True)
    record.update(phase='infrastructure-ready', requiresRecovery=False)
    checkpoint(dict(record))
    return record
