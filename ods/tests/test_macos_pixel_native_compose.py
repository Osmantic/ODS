import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('native_compose',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-compose.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('fault', [None, 'image', 'user', 'project', 'volume', 'duplicate', 'absent'])
def test_managed_writer_selection_checks_identity_before_stop(fault):
    image = 'sha256:' + 'a' * 64
    document = {'Id': 'b' * 64, 'Image': 'wrong' if fault == 'image' else image,
        'Config': {'User': '0:0' if fault == 'user' else '501:20', 'Labels': {
            'com.docker.compose.project': 'other' if fault == 'project' else 'ods',
            'com.docker.compose.service': 'pixel-native-ingress'}},
        'Mounts': [{'Destination': '/runtime', 'Type': 'volume', 'RW': True,
            'Name': 'other' if fault == 'volume' else 'history'}]}
    def run(*args, **kwargs):
        if args[0] == 'ps':
            body = '' if fault == 'absent' else 'b' * 64 + ('\n' + 'c' * 64 if fault == 'duplicate' else '')
        else:
            assert args == ('inspect', 'b' * 64)
            body = json.dumps([document])
        return SimpleNamespace(returncode=0, stdout=body)
    kwargs = dict(project='ods', image=image, user='501:20', volume='history')
    if fault in (None, 'absent'):
        assert module._managed_ingress(run, **kwargs) == (None if fault == 'absent' else document)
    else:
        with pytest.raises(ValueError): module._managed_ingress(run, **kwargs)


@pytest.mark.parametrize('fault', [None, 'released', 'edge', 'stop', 'preview', 'storage', 'health', 'release-retry'])
def test_docker_recovery_stops_new_writer_before_restoring_old(monkeypatch, fault):
    calls, journal = [], []
    record = {'phase': 'releasing-rollback-admission' if fault == 'release-retry' else 'recovery-required',
        'failedPhase': 'starting-native-infrastructure', 'requiresRecovery': True, 'project': 'ods',
        'edge': 'a' * 64, 'previousIngress': 'b' * 64, 'workspace': '/workspace', 'storage': {'saved': True},
        'legacyIngress': {'saved': True}, 'binding': {'token': 'c' * 64, 'revision': 'd' * 64},
        'ingressSelection': {'project': 'ods', 'image': 'sha256:' + 'e' * 64, 'user': '501:20', 'volume': 'history'}}
    if fault == 'released': record.update(phase='infrastructure-ready', requiresRecovery=False)
    def previous(*args, **kwargs):
        calls.append(args[-1])
        return SimpleNamespace(returncode=int((fault == 'edge' and args[-1] == 'pixel-edge')
            or (fault == 'preview' and args[-1] == 'pixel-workspace-preview')))
    monkeypatch.setattr(module, '_managed_edge', lambda *args: 'a' * 64)
    monkeypatch.setattr(module, '_managed_ingress', lambda *args, **kwargs: {'Id': 'e' * 64})
    monkeypatch.setattr(module, '_named_ingress', lambda *args: 'f' * 64)
    def transition(*args, **kwargs): calls.append('release' if kwargs.get('release') else 'hold')
    monkeypatch.setattr(module, '_edge_transition', transition)
    def stop(*args, **kwargs):
        assert args[1:] == ('stop', '--time', '20', 'e' * 64)
        calls.append('stop-new')
        if fault == 'stop': raise ValueError('stop-failed')
    monkeypatch.setattr(module, '_docker', stop)
    monkeypatch.setattr(module, '_inspect_ingress', lambda *args: {'State': {'Running': False}})
    def restore(*args, **kwargs):
        assert calls[-1] == 'stop-new'
        calls.append('restore-old')
        kwargs['checkpoint']({'phase': 'restored'})
    monkeypatch.setattr(module, 'restore_legacy_ingress', restore)
    monkeypatch.setattr(module, 'legacy_storage_override', lambda **kwargs: {} if fault == 'storage' else record['storage'])
    def health(*args, **kwargs):
        assert kwargs['services'] == ('pixel-edge', 'pixel-workspace-preview')
        calls.append('health')
        if fault == 'health': raise ValueError('unhealthy')
    monkeypatch.setattr(module, 'wait_ready', health)
    kwargs = dict(dashboard_key='f' * 64, checkpoint=lambda value: journal.append(dict(value)))
    if fault in (None, 'release-retry'):
        result = module.restore_migration_infrastructure(previous, None, record, **kwargs)
        assert result['phase'] == 'infrastructure-rolled-back' and result['requiresRecovery'] is False
        assert calls == (['health', 'release'] if fault == 'release-retry' else [
            'pixel-edge', 'hold', 'stop-new', 'restore-old', 'pixel-workspace-preview', 'hold', 'health', 'release'])
        assert journal[-2]['phase'] == 'releasing-rollback-admission'
    else:
        with pytest.raises(ValueError): module.restore_migration_infrastructure(previous, None, record, **kwargs)
        assert 'release' not in calls
        if fault == 'released': assert not calls and not journal
        else: assert journal[-1]['requiresRecovery'] is True
        if fault == 'stop': assert 'restore-old' not in calls


@pytest.mark.parametrize('fault', [None, 'edge', 'health', 'lost-release', 'resume-release', 'binding'])
def test_docker_handover_release_preserves_recovery_token(monkeypatch, fault):
    calls, journal = [], []
    record = {'phase': 'releasing-admission' if fault == 'resume-release' else 'infrastructure-ready-held',
        'requiresRecovery': True, 'project': 'ods', 'edge': 'a' * 64,
        'binding': {'token': 'b' * 64, 'revision': 'c' * 64}}
    if fault == 'binding': record['binding']['token'] = 'invalid'
    monkeypatch.setattr(module, '_managed_edge', lambda *args: 'd' * 64 if fault == 'edge' else 'a' * 64)
    def health(*args):
        calls.append('health')
        if fault == 'health': raise ValueError('injected-health')
    monkeypatch.setattr(module, 'wait_ready', health)
    def transition(*args, **kwargs):
        calls.append('release' if kwargs.get('release') else 'acquire')
        assert kwargs['binding'] == record['binding']
        if kwargs.get('release'):
            if fault != 'resume-release': assert journal[-1]['phase'] == 'releasing-admission'
            if fault == 'lost-release': raise ValueError('injected-lost-reply')
    monkeypatch.setattr(module, '_edge_transition', transition)
    kwargs = dict(dashboard_key='e' * 64, checkpoint=lambda value: journal.append(value))
    if fault in ('edge', 'health', 'lost-release', 'binding'):
        with pytest.raises(ValueError): module.finish_migration_infrastructure(None, None, record, **kwargs)
        if fault == 'lost-release':
            assert journal[-1]['requiresRecovery'] is True
            assert journal[-1]['phase'] == 'releasing-admission'
        else: assert not journal
    else:
        result = module.finish_migration_infrastructure(None, None, record, **kwargs)
        assert result['phase'] == 'infrastructure-ready' and result['requiresRecovery'] is False
        assert calls == (['health', 'release'] if fault == 'resume-release' else ['health', 'acquire', 'release'])


@pytest.mark.parametrize('fault', [None, 'storage', 'acquire', 'retain', 'start', 'health'])
def test_docker_migration_orders_writers_under_hold_and_never_releases_on_failure(monkeypatch, fault):
    calls, journal = [], []
    storage = {'volumes': {'pixel-native-runtime': {'name': 'preserved'}}}
    managed_reads = []
    def managed(*args, **kwargs):
        managed_reads.append(True)
        return None if len(managed_reads) == 1 else {'Id': 'b' * 64, 'State': {'Running': True}}
    monkeypatch.setattr(module, '_managed_ingress', managed)
    monkeypatch.setattr(module, '_managed_edge', lambda *args: 'e' * 64)
    monkeypatch.setattr(module, '_named_ingress', lambda *args: 'b' * 64)
    monkeypatch.setattr(module, '_inspect_ingress', lambda *args: {})
    monkeypatch.setattr(module, 'legacy_storage_override', lambda **kwargs:
        {'changed': True} if fault == 'storage' else storage)
    def transition(*args, **kwargs):
        assert not kwargs.get('release')
        calls.append('acquire' if kwargs.get('binding') else 'inspect-admission')
        if kwargs.get('binding'):
            assert journal and journal[-1]['requiresRecovery'] is True
            if fault == 'acquire': raise ValueError('injected-acquire')
        return {'revision': 'f' * 64}
    monkeypatch.setattr(module, '_edge_transition', transition)
    def retain(*args, **kwargs):
        assert calls == ['inspect-admission', 'acquire']
        calls.append('stop-old-writer')
        kwargs['checkpoint']({'phase': 'stopping-legacy', 'previousId': 'a' * 64})
        if fault == 'retain': raise ValueError('injected-retain')
        kwargs['checkpoint']({'phase': 'legacy-retained', 'previousId': 'a' * 64})
    monkeypatch.setattr(module, 'retain_legacy_ingress', retain)
    def start(*args, **kwargs):
        assert calls[-1] == 'stop-old-writer'
        assert kwargs['admission'] == journal[0]['binding']
        calls.append('start-new-writer')
        if fault == 'start': raise ValueError('injected-start')
    monkeypatch.setattr(module, 'start_infrastructure', start)
    def health(*args):
        calls.append('health')
        if fault == 'health': raise ValueError('injected-health')
    monkeypatch.setattr(module, 'wait_ready', health)
    kwargs = dict(ingress='a' * 64, image='sha256:' + 'b' * 64, user='501:20', project='ods',
        transaction='c' * 64, storage=storage, workspace='/owner/workspace', dashboard_key='d' * 64,
        checkpoint=lambda value: journal.append(json.loads(json.dumps(value))))
    if fault:
        with pytest.raises(ValueError): module.migrate_infrastructure(None, None, **kwargs)
        if fault == 'storage':
            assert not calls and not journal
        else:
            assert journal[-1]['phase'] == 'recovery-required'
            assert journal[-1]['requiresRecovery'] is True
            assert journal[-1]['binding'] == journal[0]['binding']
    else:
        result = module.migrate_infrastructure(None, None, **kwargs)
        assert calls == ['inspect-admission', 'acquire', 'stop-old-writer',
            'start-new-writer', 'acquire', 'health']
        assert result['phase'] == 'infrastructure-ready-held'
        assert result['requiresRecovery'] is True


@pytest.mark.parametrize('fault', [None, 'project', 'bind', 'duplicate', 'readonly',
    'workspace', 'edge-volume', 'alias', 'volume-name', 'managed-ingress'])
def test_legacy_storage_preserves_all_volumes_and_rejects_topology_drift(fault):
    def volume(destination, name, writable=True):
        return {'Type': 'volume', 'Destination': destination, 'Name': name, 'RW': writable}
    def container(name, letter, service, mounts):
        return {'Id': letter * 64, 'Name': '/' + name,
            'Config': {'Labels': {} if service is None else {
                'com.docker.compose.project': 'ods', 'com.docker.compose.service': service}},
            'Mounts': mounts}
    ingress = container(module.INGRESS_NAME, 'a', None, [volume('/runtime', 'old-history')])
    preview = container('ods-pixel-workspace-preview', 'b', 'pixel-workspace-preview', [
        volume('/previews', 'old-sites'), volume('/run/ods-pixel-preview', 'old-preview-socket'),
        {'Type': 'bind', 'Destination': '/workspace', 'Source': '/owner/workspace', 'RW': False}])
    edge = container('ods-pixel-edge', 'c', 'pixel-edge', [
        volume('/pixel-runtime', 'old-history', False),
        volume('/pixel-preview-runtime', 'old-preview-socket', False),
        volume('/pixel-transition-state', 'old-transitions')])
    if fault == 'project': edge['Config']['Labels']['com.docker.compose.project'] = 'other'
    if fault == 'bind': ingress['Mounts'][0]['Type'] = 'bind'
    if fault == 'duplicate': ingress['Mounts'].append(dict(ingress['Mounts'][0]))
    if fault == 'readonly': ingress['Mounts'][0]['RW'] = False
    if fault == 'workspace': preview['Mounts'][2]['Source'] = '/other/workspace'
    if fault == 'edge-volume': edge['Mounts'][0]['Name'] = 'other-history'
    if fault == 'alias': edge['Mounts'][2]['Name'] = 'old-history'
    if fault == 'volume-name': ingress['Mounts'][0]['Name'] = '/host/path'
    if fault == 'managed-ingress': ingress['Config']['Labels']['com.docker.compose.project'] = 'ods'
    if fault:
        with pytest.raises(ValueError):
            module.legacy_storage_override(ingress=ingress, preview=preview, edge=edge,
                project='ods', workspace='/owner/workspace')
    else:
        assert module.legacy_storage_override(ingress=ingress, preview=preview, edge=edge,
            project='ods', workspace='/owner/workspace') == {'volumes': {
                name: {'name': value, 'external': True} for name, value in (
                    ('pixel-native-runtime', 'old-history'), ('pixel-native-previews', 'old-sites'),
                    ('pixel-native-preview-runtime', 'old-preview-socket'),
                    ('pixel-transition-state', 'old-transitions'))}}


@pytest.mark.parametrize('fault', [None, 'key', 'start', 'held', 'busy', 'unavailable', 'revision', 'json'])
def test_infrastructure_waits_for_idle_admission_not_gateway_health(monkeypatch, fault):
    clock = [0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0] + 100))
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        assert '--wait' not in args
        if args[0] == 'up':
            assert args == ('up', '-d', '--build', *module.SERVICES)
            return SimpleNamespace(returncode=1 if fault == 'start' else 0)
        assert args[:5] == ('exec', '-T', 'pixel-edge', 'python3', '-c')
        assert 'a' * 64 not in args[-1]
        assert json.loads(kwargs['input']) == {'key': 'a' * 64}
        value = {'capability': 'available', 'phase': 'held' if fault == 'held' else 'idle',
            'streams': 1 if fault == 'busy' else 0, 'admission_blocked': False,
            'revision': 'wrong' if fault == 'revision' else 'b' * 64}
        return SimpleNamespace(returncode=1 if fault == 'unavailable' else 0,
            stdout='broken' if fault == 'json' else json.dumps(value))
    if fault:
        with pytest.raises(ValueError):
            module.start_infrastructure(run, dashboard_key='invalid' if fault == 'key' else 'a' * 64)
        if fault == 'key': assert calls == []
    else:
        assert module.start_infrastructure(run, dashboard_key='a' * 64)['requiresGatewayActivation'] is True


@pytest.mark.parametrize('fault', [None, 'array', 'missing', 'duplicate', 'unhealthy', 'stopped', 'json', 'exit'])
def test_final_readiness_requires_all_exact_services_healthy(monkeypatch, fault):
    clock = [0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0] + 100))
    rows = [{'Service': name, 'State': 'running', 'Health': 'healthy'} for name in module.SERVICES]
    if fault == 'missing': rows.pop()
    if fault == 'duplicate': rows[1] = rows[0]
    if fault == 'unhealthy': rows[0]['Health'] = 'unhealthy'
    if fault == 'stopped': rows[0]['State'] = 'exited'
    def run(*args, **kwargs):
        assert args == ('ps', '--format', 'json', *module.SERVICES)
        body = json.dumps(rows) if fault == 'array' else '\n'.join(json.dumps(row) for row in rows)
        return SimpleNamespace(returncode=1 if fault == 'exit' else 0, stdout='broken' if fault == 'json' else body)
    if fault in (None, 'array'):
        assert module.wait_ready(run) == {'phase': 'docker-ready'}
    else:
        with pytest.raises(ValueError, match='health-timeout'): module.wait_ready(run)


@pytest.mark.parametrize('fault', [None, 'binding', 'idle', 'revision', 'busy', 'unblocked', 'conflict'])
def test_migration_start_reacquires_only_the_approved_hold(monkeypatch, fault):
    clock = [0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0] + 100))
    binding = {'token': 'c' * 64, 'revision': 'd' * 64}
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        if args[0] == 'up': return SimpleNamespace(returncode=0)
        assert json.loads(kwargs['input']) == {'key': 'a' * 64, 'binding': binding}
        assert binding['token'] not in args[-1]
        return SimpleNamespace(returncode=1 if fault == 'conflict' else 0, stdout=json.dumps({
            'capability': 'available', 'phase': 'idle' if fault == 'idle' else 'held',
            'streams': 1 if fault == 'busy' else 0, 'admission_blocked': fault != 'unblocked',
            'revision': 'e' * 64 if fault == 'revision' else binding['revision']}))
    if fault == 'binding': binding['token'] = 'invalid'
    if fault:
        with pytest.raises(ValueError):
            module.start_infrastructure(run, dashboard_key='a' * 64, admission=binding)
        if fault == 'binding': assert not calls
    else:
        assert module.start_infrastructure(run, dashboard_key='a' * 64, admission=binding) == {
            'phase': 'infrastructure-ready', 'requiresGatewayActivation': True, 'admissionHeld': True}


@pytest.mark.parametrize('fault', [None, 'managed', 'image', 'backup', 'stop', 'rename', 'foreign-name'])
def test_legacy_ingress_is_retained_and_restored_without_touching_managed_container(fault):
    identity, managed_id = 'a' * 64, 'b' * 64
    image, transaction = 'sha256:' + 'c' * 64, 'd' * 64
    legacy = {'Id': identity, 'Name': '/' + module.INGRESS_NAME, 'Image': image,
        'Config': {'User': '501:20', 'Labels': {}}, 'State': {'Running': True}}
    managed = {'Id': managed_id, 'Name': '/ods-pixel-native-ingress-1', 'Image': image,
        'Config': {'User': '501:20', 'Labels': {'com.docker.compose.project': 'ods'}}, 'State': {'Running': True}}
    if fault == 'managed': legacy['Config']['Labels']['com.docker.compose.project'] = 'ods'
    containers = {identity: legacy, managed_id: managed}
    managed_before = json.dumps(managed, sort_keys=True)
    events, records, injected = [], [], [fault]
    def run(*args, **kwargs):
        events.append(args)
        output, rc = '', 0
        if args[0] == 'inspect': output = json.dumps([containers[args[1]]])
        elif args[0] == 'ps':
            name = args[args.index('--filter') + 1][len('name=^'): -1]
            ids = [key for key, value in containers.items() if value['Name'] == name]
            if fault == 'backup' and '-before-' in name: ids = ['e' * 64]
            output = '\n'.join(ids)
        elif args[0] in ('stop', 'rename', 'start'):
            target = args[-1] if args[0] == 'stop' else args[1]
            assert target == identity
            assert records, 'journal must precede every mutation'
            if args[0] == injected[0]: rc = 1
            elif args[0] == 'rename': legacy['Name'] = '/' + args[2]
            else: legacy['State']['Running'] = args[0] == 'start'
        else: pytest.fail('unexpected Docker command')
        return SimpleNamespace(returncode=rc, stdout=output)
    def retain():
        return module.retain_legacy_ingress(run, identity=identity, image='sha256:' + 'f' * 64 if fault == 'image' else image,
            user='501:20', project='ods', transaction=transaction, checkpoint=records.append)
    if fault in ('managed', 'image', 'backup'):
        with pytest.raises(ValueError): retain()
        assert not records and not any(event[0] in ('stop', 'rename', 'start') for event in events)
        return
    if fault in ('stop', 'rename'):
        with pytest.raises(ValueError): retain()
        record = records[-1]
        injected[0] = None
    else:
        record = retain()
        assert legacy['Name'].endswith('-before-' + transaction[:12])
        assert legacy['State']['Running'] is False
    if fault == 'foreign-name':
        containers['e' * 64] = {'Name': '/' + module.INGRESS_NAME}
        before = len(events)
        with pytest.raises(ValueError, match='name-occupied'):
            module.restore_legacy_ingress(run, record, checkpoint=records.append)
        assert not any(event[0] in ('stop', 'rename', 'start') for event in events[before:])
    else:
        restored = module.restore_legacy_ingress(run, record, checkpoint=records.append)
        assert restored['phase'] == 'restored' and restored['requiresRecovery'] is False
        assert legacy['Name'] == '/' + module.INGRESS_NAME and legacy['State']['Running'] is True
    assert json.dumps(managed, sort_keys=True) == managed_before
    assert not any(event[0] == 'rm' for event in events)
