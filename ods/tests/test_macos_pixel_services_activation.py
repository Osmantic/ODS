import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('native_activation',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-services.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('fault', [None, 'verify', 'existing', 'state', 'publish', 'activate'])
def test_initial_service_flow_validates_before_provisioning_and_uses_readiness(monkeypatch, fault):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module.os.path, 'lexists', lambda path: fault == 'existing')
    monkeypatch.setattr(module.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=501))
    events, records = [], []
    identity = {'name': '_ods_pixel_ops', 'uid': 60000, 'gid': 60000}
    class Absent(ValueError):
        returncode = 113
    def absent(*args, **kwargs): raise Absent()
    def event(name, value=None):
        events.append(name)
        if name == fault: raise ValueError('fixture')
        return value
    helpers = {
        'config': SimpleNamespace(verified_services=lambda *a, **k: event('verify', {'approved': b'source'})),
        'ops-account': SimpleNamespace(provision=lambda: event('identity', identity)),
        'ops-state': SimpleNamespace(provision=lambda **kw: event('state'),
            provision_manager_runtime=lambda **kw: event('runtime')),
        'ops-service': SimpleNamespace(select_python=lambda: '/python',
            installer_helpers=lambda: SimpleNamespace(_job_disabled=lambda target: False,
                _command=absent, InstallError=Absent),
            custody=SimpleNamespace(protected_bytes=lambda path: b'approved')),
    }
    monkeypatch.setattr(module, 'helper', helpers.__getitem__)
    monkeypatch.setattr(module, 'publish', lambda **kw: event('publish', {'manager': '/manager'}))
    monkeypatch.setattr(module, 'activation_adapters', lambda **kw: event('adapters', 'adapters'))
    monkeypatch.setattr(module, 'readiness_checks', lambda **kw: event('readiness', 'readiness'))
    def activate(**kw):
        assert kw == {'services': 'adapters', 'readiness': 'readiness', 'checkpoint': records.append}
        event('activate')
    monkeypatch.setattr(module, 'activate_new', activate)
    selection = {'bundle': '/bundle', 'expected_digest': 'a' * 64,
        'expected_ref': 'b' * 40, 'expected_config_digest': 'c' * 64}
    def run():
        return module.install_new(selection=selection, owner='owner', environment='/env', workspace='/workspace',
            port=3002, checkpoint=records.append)
    if fault:
        with pytest.raises(ValueError): run()
    else:
        assert run() == 'adapters'
        assert events == ['verify', 'identity', 'state', 'runtime', 'publish', 'adapters', 'readiness', 'activate']
    if fault in ('verify', 'existing'):
        assert events == ['verify'] and records == []


@pytest.mark.parametrize('fault', [None, 'api-failed', 'wrong-kind', 'invalid-json', 'exit', 'timeout'])
def test_manager_readiness_requires_successful_inventory(monkeypatch, fault):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    owner = SimpleNamespace(pw_uid=501, pw_gid=20)
    broker = SimpleNamespace(pw_uid=60000, pw_gid=60000)
    monkeypatch.setattr(module.pwd, 'getpwnam', lambda name: owner if name == 'owner' else broker)
    monkeypatch.setattr(module, 'helper', lambda name: SimpleNamespace(custody=SimpleNamespace(
        protected_bytes=lambda *args, **kwargs: b'approved')))
    clock = [0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0] + 50))
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[:4] == ['/python', '-I', '-B', '/programs/manager/extension_manager.py']
        assert argv[4:] == ['client', '/private/var/lib/ods-pixel-manager/extension-manager.sock', 'list', 'all']
        assert kwargs['user'] == 60000 and kwargs['group'] == 60000
        assert kwargs['extra_groups'] == [] and kwargs['env'] == {'PATH': '/usr/bin:/bin'}
        assert kwargs['timeout'] <= 5 and kwargs['stdin'] == module.subprocess.DEVNULL
        if fault == 'timeout': raise module.subprocess.TimeoutExpired(argv, 5)
        value = {'schemaVersion': 1, 'kind': 'wrong' if fault == 'wrong-kind' else 'ods-pixel-extension-inventory',
            'outcome': 'failed' if fault == 'api-failed' else 'succeeded'}
        return SimpleNamespace(returncode=1 if fault == 'exit' else 0,
            stdout=b'broken' if fault == 'invalid-json' else json.dumps(value).encode())
    monkeypatch.setattr(module.subprocess, 'run', run)
    checks = module.readiness_checks(owner='owner', identity={'name': '_ods_pixel_ops', 'uid': 60000, 'gid': 60000},
        python='/python', program_root='/programs')
    if fault:
        with pytest.raises(ValueError, match='manager-readiness-failed'):
            checks['manager']()
    else:
        assert checks['manager']() is True
    assert len(calls) == 1


@pytest.mark.parametrize('fault', [None, 'loaded', 'disabled', 'print-error', 'bootstrap', 'readiness', 'stop'])
def test_new_service_activation_never_replaces_existing_and_tracks_failed_attempts(monkeypatch, fault):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    events, records = [], []
    class Error(ValueError):
        def __init__(self, code): self.returncode = code
    def command(args, **kwargs):
        events.append(args[1:])
        if args[1] == 'print':
            if fault == 'loaded': return 'existing'
            raise Error(1 if fault == 'print-error' else 113)
        if args[1] == 'bootstrap' and fault == 'bootstrap': raise Error(5)
    installer = SimpleNamespace(_command=command, _job_disabled=lambda target: fault == 'disabled', InstallError=Error)
    monkeypatch.setattr(module, 'helper', lambda name: SimpleNamespace(installer_helpers=lambda: installer))
    services, ready = {}, {}
    def stop(name):
        events.append(['stop', name])
        if fault == 'stop': raise Error(5)
    def stopped():
        if fault == 'stop': raise Error(5)
    for name in ('manager', 'promoter', 'operations'):
        services[name] = SimpleNamespace(target='system/com.ods.pixel-native-' + name,
            plist=Path('/Library/LaunchDaemons/com.ods.pixel-native-' + name + '.plist'),
            verify_definition=lambda: None, process_identity=lambda: (123, 1, 1),
            verify=lambda: None,
            stop=lambda name=name: stop(name), assert_stopped=stopped)
        ready[name] = lambda name=name: not (name == 'promoter' and fault in ('readiness', 'stop'))
    def run(): module.activate_new(services=services, readiness=ready, checkpoint=records.append)
    if fault:
        with pytest.raises(ValueError): run()
        if fault in ('loaded', 'disabled', 'print-error'):
            assert not records
            assert not any(event[0] == 'bootstrap' for event in events)
        else:
            assert records[-1]['phase'] == 'activation-failed'
            assert records[-1]['requiresRecovery'] is True
            expected = ['manager'] if fault == 'bootstrap' else ['promoter', 'manager']
            assert [event[1] for event in events if event[0] == 'stop'] == expected
            assert records[-1]['stopUnconfirmed'] == (expected if fault == 'stop' else [])
            if fault == 'stop':
                assert [event[1] for event in events if event[0] == 'bootout'] == [
                    'system/com.ods.pixel-native-' + name for name in expected]
    else:
        run()
        assert records[-1]['phase'] == 'services-active'
        assert records[-1]['requiresGatewayProof'] is True
        assert [r['service'] for r in records if r['phase'] == 'verified'] == ['manager', 'promoter', 'operations']


@pytest.mark.parametrize('fault', [None, 'stop', 'definition', 'wrong-target'])
def test_gateway_rollback_stops_every_new_service_in_reverse_order(monkeypatch, fault):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    events, records = [], []
    installer = SimpleNamespace(_command=lambda args: events.append(args[1:]))
    monkeypatch.setattr(module, 'helper', lambda name: SimpleNamespace(installer_helpers=lambda: installer))
    services = {}
    def verify(name):
        if name == 'operations' and fault == 'definition': raise ValueError('definition-changed')
    def stop(name):
        events.append(['stop', name])
        if name == 'operations' and fault == 'stop': raise ValueError('still-running')
    def stopped(name):
        if name == 'operations' and fault == 'stop': raise ValueError('still-running')
    for name in ('manager', 'promoter', 'operations'):
        services[name] = SimpleNamespace(target='system/com.ods.pixel-native-' + name,
            verify_definition=lambda name=name: verify(name), verify=lambda name=name: verify(name),
            stop=lambda name=name: stop(name), assert_stopped=lambda name=name: stopped(name))
    if fault == 'wrong-target': services['operations'].target = 'system/unrelated'
    def run():
        module.stop_new(services=services, attempted=list(services), checkpoint=records.append)
    if fault:
        with pytest.raises(ValueError): run()
    else: run()
    if fault == 'wrong-target':
        assert not events and not records
        return
    assert [event[1] for event in events if event[0] == 'stop'] == (
        ['promoter', 'manager'] if fault == 'definition' else ['operations', 'promoter', 'manager'])
    assert records[0]['phase'] == 'stopping-services'
    assert records[-1]['phase'] == ('service-stop-failed' if fault else 'services-stopped')
    assert records[-1]['requiresRecovery'] is True
    assert records[-1]['stopUnconfirmed'] == (['operations'] if fault else [])


@pytest.mark.parametrize('fault', [None, 'owner', 'version', 'missing', 'encoding', 'relative', 'extra'])
def test_recovery_controls_use_exact_recorded_definitions(monkeypatch, fault):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    record = {'schemaVersion': 1, 'owner': 'owner', 'python': '/usr/bin/python3',
        'identity': {'name': '_ods_pixel_ops', 'uid': 60000, 'gid': 60000},
        'definitions': {name: {'path': '/Library/LaunchDaemons/com.ods.pixel-native-' + name + '.plist',
            'body': module.base64.b64encode((name + '-approved').encode()).decode('ascii')}
            for name in ('manager', 'promoter', 'operations')}}
    if fault == 'owner': record['owner'] = 'different'
    if fault == 'version': record['schemaVersion'] = True
    if fault == 'missing': record['definitions'].pop('operations')
    if fault == 'encoding': record['definitions']['manager']['body'] = '%%%'
    if fault == 'relative': record['definitions']['manager']['path'] = 'relative'
    if fault == 'extra': record['unexpected'] = True
    calls = []
    monkeypatch.setattr(module, 'activation_adapters', lambda **kw: calls.append(kw) or 'approved-adapters')
    save, load = lambda *args: None, lambda *args: None
    def run(): return module.recovery_adapters(record, owner='owner', save_stop=save, load_stop=load)
    if fault:
        with pytest.raises(ValueError, match='recovery-record-invalid'): run()
        assert not calls
    else:
        assert run() == 'approved-adapters'
        assert calls[0]['expected'] == {name: (name + '-approved').encode() for name in record['definitions']}
        assert calls[0]['save_stop'] is save and calls[0]['load_stop'] is load
