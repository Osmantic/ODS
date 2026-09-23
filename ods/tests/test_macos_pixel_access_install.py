"""Exercise native migration planning without privileged writes or restarts."""
import importlib.util
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
SPEC = importlib.util.spec_from_file_location(
    "pixel_macos_access_install", ROOT / "installers/macos/lib/pixel-macos-access-install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


@pytest.mark.parametrize('version', ['previous', 'candidate', 'unknown'])
def test_absent_upgrade_requires_matching_definition_and_stop_witness(version):
    services = {}
    for name in ('previous', 'candidate'):
        services[name] = SimpleNamespace(target='system/fixture',
            verify_definition=Mock(side_effect=None if name == version else
                installer.InstallError('native-deployment-changed')), assert_stopped=Mock())
    if version == 'unknown':
        with pytest.raises(installer.InstallError, match='runtime-upgrade-service-unqualified'):
            installer._assert_upgrade_absent(services['previous'], services['candidate'])
        for service in services.values(): service.assert_stopped.assert_not_called()
    else:
        installer._assert_upgrade_absent(services['previous'], services['candidate'])
        services[version].assert_stopped.assert_called_once()
        services['previous' if version == 'candidate' else 'candidate'].assert_stopped.assert_not_called()


def test_absent_candidate_cannot_fall_back_to_old_stop_witness():
    previous = SimpleNamespace(target='system/fixture', verify_definition=Mock(), assert_stopped=Mock())
    candidate = SimpleNamespace(target='system/fixture', verify_definition=Mock(),
        assert_stopped=Mock(side_effect=installer.InstallError('native-stop-witness-unavailable')))
    with pytest.raises(installer.InstallError, match='native-stop-witness-unavailable'):
        installer._assert_upgrade_absent(previous, candidate)
    previous.verify_definition.assert_not_called()
    previous.assert_stopped.assert_not_called()


@pytest.mark.parametrize('failure', [None, 'publish', 'config', 'activation', 'reproof', 'prior-archive', 'prior-edge', 'prior-stop'])
@pytest.mark.parametrize('migration', [False, True])
def test_upgrade_executor_holds_controller_lock_through_staging_and_activation(tmp_path, monkeypatch, failure, migration):
    import pixel_access_bridge as bridge_module
    import pixel_macos_custody as custody
    events, locked = [], []
    state = tmp_path / 'state'
    settings = dict(state_dir=str(state), install_dir='/test/ods', gateway_target='system/test',
        gateway_plist='/Library/LaunchDaemons/test.plist', gateway_process={}, gateway_binding={},
        openclaw_bin='/test/openclaw', gateway_policy={})
    raw = json.dumps(settings).encode()
    records = [dict(path='/test/config', before=raw, after=raw, mode=0o600, gid=0)]
    qualification = dict(currentDigest='a' * 64, candidateDigest='b' * 64)
    plan = dict(upgrade_qualification=qualification, runtime_bundle=dict(source='/candidate',
        destination='/published', digest='b' * 64, source_config_bytes=b'old', config_bytes=b'new'),
        key=b'test-key', owner=SimpleNamespace(pw_name='owner', pw_uid=501, pw_gid=20, pw_dir='/Users/owner'),
        source_bytes=b'plist', source_environment={}, source_process={}, gateway_binding={},
        access_settings=settings, access_port=18790, upgrade_kind='stream-progress')
    if migration:
        plan.update(migration_qualification={'fixture': True}, migration_source_config_bytes=b'previous',
            native_services={'fixture': True}, native_manager_port=3002, upgrade_kind='native-migration')
    activate = installer.migrate_install if migration else installer.upgrade_install

    class Bridge:
        def __init__(self, *args, **kwargs):
            self.state = Path(kwargs['state'])
        @contextmanager
        def locked(self):
            assert not locked
            locked.append(True)
            events.append('locked')
            try:
                yield
            finally:
                locked.pop()
                events.append('unlocked')

    def step(name):
        assert locked
        events.append(name)
        if failure == name:
            raise RuntimeError('injected-' + name)

    def create(path, value):
        step('journal')
        assert path == state / 'runtime-upgrade.json'
        assert value['phase'] == 'prepared'
        return SimpleNamespace(value=value)

    def config(plan, *, write=False):
        step('config' if write else 'config-preflight')

    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', state)
    monkeypatch.setattr(bridge_module, 'LaunchdAccessBridge', Bridge)
    monkeypatch.setattr(custody, 'protected_bytes', lambda path: raw)
    monkeypatch.setattr(installer, '_upgrade_file_snapshots',
                        lambda *args: (step('snapshot') or records, []))
    service = SimpleNamespace(target='system/test', process_identity=lambda: step('identity'))
    monkeypatch.setattr(installer, '_upgrade_services', lambda *args: ({'gateway': service}, {}))
    monkeypatch.setattr(installer, '_job_disabled', lambda target: False)
    monkeypatch.setattr(installer, '_runtime_config', config)
    monkeypatch.setattr(installer._bundle, 'qualify_stream_progress_upgrade',
                        lambda *args, **kwargs: step('qualify') or qualification)
    monkeypatch.setattr(installer, '_requalify_migration', lambda plan: step('qualify') or qualification)
    monkeypatch.setattr(installer._bundle, 'publish', lambda *args, **kwargs: step('publish') or Path('/published'))
    monkeypatch.setattr(installer._upgrade.RecoveryJournal, 'create', create)
    def write_context(path, data, *, mode):
        step('context')
        assert mode == 0o600
        assert path == state / ('runtime-upgrade-context-' + 'b' * 64 + '.json')
        assert json.loads(data) == installer._recovery_context(plan)
    monkeypatch.setattr(installer, '_write_exact', write_context)
    monkeypatch.setattr(installer, '_previous_upgrade_guard', lambda records: False)
    monkeypatch.setattr(installer, '_activate_upgrade', lambda *args, **kwargs: step('activation'))
    def reprove(plan):
        assert not locked
        events.append('reproof')
        if failure == 'reproof':
            raise RuntimeError('injected-reproof')
    monkeypatch.setattr(installer, '_reprove_installed_access', reprove)
    if failure and failure.startswith('prior-'):
        state.mkdir()
        names = {'prior-archive': 'runtime-upgrade-' + 'b' * 64 + '.completed.json',
                 'prior-edge': 'runtime-upgrade-edge-' + 'b' * 64 + '.json',
                 'prior-stop': 'runtime-upgrade-stop-' + 'b' * 64 + '-previous-access.json'}
        prior = state / names[failure]
        prior.write_bytes(b'preserved authority')
        with pytest.raises(installer.InstallError, match='prior-attempt-requires-archive'):
            activate(plan, '/source')
        assert not {'context', 'journal', 'publish', 'activation'}.intersection(events)
        assert list(state.iterdir()) == [prior]
        assert prior.read_bytes() == b'preserved authority'
        assert not locked
        return
    if failure:
        with pytest.raises(RuntimeError, match='injected-' + failure):
            activate(plan, '/source')
    else:
        activate(plan, '/source')
    assert events[0] == 'locked'
    if failure in (None, 'reproof'):
        assert events[-2:] == ['unlocked', 'reproof']
    else:
        assert events[-1] == 'unlocked'
    assert events.index('context') < events.index('journal') < events.index('publish')
    assert not locked
    if failure in ('publish', 'config'):
        assert 'activation' not in events


@pytest.mark.parametrize('failure', [None, 'candidate', 'final'])
@pytest.mark.parametrize('migration', [False, True, 'managed'])
def test_upgrade_activation_connects_hold_files_services_and_readiness(monkeypatch, failure, migration):
    relocation = Mock()
    monkeypatch.setattr(installer, '_relocate_upgrade_receipt', relocation)
    monkeypatch.setattr(installer, '_clear_candidate_stop_witnesses', Mock())
    monkeypatch.setattr(installer, '_prepare_candidate_stop_witnesses', lambda *args: None)
    import pixel_macos_custody as custody
    events = []
    bodies = {'/test/config': b'{"gateway_policy":{}}', '/test/code': b'old'}
    records = [dict(path=path, before=body, after=(b'new' if path.endswith('code') else body),
                    mode=0o600, gid=0) for path, body in bodies.items()]

    class Service:
        def __init__(self, role, version):
            self.target, self.role, self.version = role, role, version
        def assert_stopped(self):
            events.append(('stopped', self.version, self.role))
        def process_identity(self):
            return self.version, self.role

    class Journal:
        value = {'phase': 'prepared'}
        def phase(self, value):
            self.value = {'phase': value}
            events.append(value)
        def finish(self, verify):
            verify()
            events.append('journal-finished')

    roles = installer._upgrade.NATIVE_ROLES if migration == 'managed' else installer._upgrade.CORE_ROLES
    previous, candidate = ({role: Service(role, version) for role in roles}
                           for version in ('previous', 'candidate'))
    monkeypatch.setattr(installer, '_upgrade_services', lambda *args: (previous, candidate))
    monkeypatch.setattr(installer, '_upgrade_policy_mode', lambda plan: 'sandbox')
    monkeypatch.setattr(installer, '_destination', lambda path: Path('/test/config'))
    monkeypatch.setattr(custody, 'protected_bytes', lambda path, **kwargs: bodies[str(path)])
    def replace(path, *, expected, replacement, **kwargs):
        assert bodies[path] == expected
        bodies[path] = replacement
        events.append(('write', path, replacement))
    monkeypatch.setattr(custody, 'replace_protected_bytes', replace)
    monkeypatch.setattr(installer._policy, 'policy_state', lambda receipt: {'activeMode': 'sandbox'})
    monkeypatch.setattr(installer, '_verify_bundle_selection', lambda plan: None)
    monkeypatch.setattr(installer, '_acquire_migration_hold', lambda plan: events.append('hold') or 'token')
    monkeypatch.setattr(installer, '_finish_migration_hold', lambda plan, hold: events.append(('release', hold)))
    monkeypatch.setattr(installer, '_start_upgrade_service',
                        lambda service: events.append(('start', service.version, service.role)))
    monkeypatch.setattr(installer, '_stop_upgrade_service',
                        lambda service: events.append(('stop', service.version, service.role)))
    monkeypatch.setattr(installer, '_ready_gateway', lambda *args: None)
    def access(service, *, upgrade_guard=False):
        events.append(('access', service.version, upgrade_guard))
        if service.version == 'candidate' and (
                (failure == 'candidate' and upgrade_guard) or (failure == 'final' and not upgrade_guard)):
            raise RuntimeError('injected-readiness-failure')
    monkeypatch.setattr(installer, '_ready_access', access)
    monkeypatch.setattr(installer, '_ready_access_relay',
                        lambda service, plan, **kwargs: events.append(('relay', service.version, kwargs)))
    journal = Journal()
    plan = {'access_settings': {'gateway_port': 18789}}
    if migration: plan['migration_qualification'] = {'approved': True}
    if migration == 'managed':
        monkeypatch.setattr(installer, '_managed_service_record', lambda *a:
            dict(selection={'old': True}, recovery={'identity': {}, 'python': '/usr/bin/python3'}))
        monkeypatch.setattr(installer._native_services, 'helper', lambda name:
            SimpleNamespace(validate_published_policy=lambda **kw: events.append('policy-validation')))
    monkeypatch.setattr(installer, '_activate_new_services', lambda plan: events.append('native-start'))
    monkeypatch.setattr(installer, '_restore_new_services', lambda plan: events.append('native-stop'))
    monkeypatch.setattr(installer, '_verify_new_services', lambda plan: events.append('native-ready'))
    if failure:
        with pytest.raises(RuntimeError, match='injected-readiness-failure'):
            installer._activate_upgrade(plan, records, journal)
    else:
        installer._activate_upgrade(plan, records, journal)
    assert events[0] == 'hold'
    assert relocation.call_count == (2 if failure == 'candidate' else 1)
    if migration == 'managed':
        assert 'native-start' not in events and 'native-stop' not in events
        assert events.index(('stopped', 'previous', 'operations')) < events.index(('write', '/test/code', b'new'))
        assert events.index('policy-validation') < events.index(('start', 'candidate', 'operations'))
        if failure == 'candidate':
            assert events.index(('stopped', 'candidate', 'operations')) < events.index(('write', '/test/code', b'old'))
            assert events.index(('write', '/test/code', b'old')) < events.index(('start', 'previous', 'operations'))
    elif migration:
        assert events.index(('write', '/test/code', b'new')) < events.index('native-start')
        assert events.index('native-start') < events.index(('start', 'candidate', 'gateway'))
        if failure == 'candidate':
            assert events.index(('stopped', 'candidate', 'gateway')) < events.index('native-stop')
            assert events.index('native-stop') < events.index(('write', '/test/code', b'old'))
    else:
        assert 'native-start' not in events and 'native-stop' not in events
    if failure == 'candidate':
        assert relocation.call_args.kwargs == {'restore': True}
    if failure == 'candidate':
        assert bodies['/test/code'] == b'old'
        assert journal.value['phase'] == 'restored'
        assert events[-1] == ('release', 'token')
        assert events.index('journal-finished') < len(events) - 2
    elif failure == 'final':
        assert bodies['/test/code'] == b'new'
        assert 'journal-finished' in events
        assert ('release', 'token') not in events
    else:
        assert bodies['/test/code'] == b'new'
        assert journal.value['phase'] == 'active'
        assert events.index('journal-finished') < events.index(('access', 'candidate', False))
        assert events[-1] == ('release', 'token')


@pytest.mark.parametrize('changed', [False, True])
def test_upgrade_controller_identity_uses_verified_job_and_birth_not_node(monkeypatch, changed):
    import pixel_macos_process as processes
    node_identity = Mock(side_effect=AssertionError('controller is not Node'))
    pid = Mock(side_effect=[101, 102 if changed else 101])
    service = SimpleNamespace(target=installer._launchd.ACCESS_TARGET,
                              pid=pid, process_identity=node_identity)
    monkeypatch.setattr(processes, 'process_birth', lambda value: (123456, 789))
    if changed:
        with pytest.raises(installer.InstallError, match='native-access-process-changed'):
            installer._upgrade_service_identity(service)
    else:
        assert installer._upgrade_service_identity(service) == (101, 123456, 789)
    node_identity.assert_not_called()
    assert pid.call_count == 2


def test_guarded_readiness_does_not_claim_operational_readiness():
    guarded = dict(available=False, surface='darwin', pending=True,
                   runtime_verified=False, effective_mode='unknown',
                   reason='runtime-upgrade-recovery-required')
    assert installer._access_response_ready(guarded, upgrade_guard=True)
    assert not installer._access_response_ready(guarded)
    assert installer._access_response_ready({'available': True})
    assert not installer._access_response_ready({'available': True}, upgrade_guard=True)
    for key in guarded:
        incomplete = {k: v for k, v in guarded.items() if k != key}
        assert not installer._access_response_ready(incomplete, upgrade_guard=True)


@pytest.mark.parametrize('changed', [dict(available=True), dict(surface='linux'),
    dict(pending=False), dict(runtime_verified=True), dict(effective_mode='full-access'),
    dict(reason='other-error')])
def test_guarded_readiness_rejects_other_states(changed):
    value = dict(available=False, surface='darwin', pending=True,
                 runtime_verified=False, effective_mode='unknown',
                 reason='runtime-upgrade-recovery-required')
    assert not installer._access_response_ready({**value, **changed}, upgrade_guard=True)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    owner = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_name="pixel-owner", pw_dir=str(tmp_path))
    # A root test runner needs a non-root planning identity; filesystem identity
    # validation remains exercised independently below.
    if owner.pw_uid == 0:
        owner.pw_uid = 501
    monkeypatch.setattr(installer.pwd, "getpwnam", lambda _: owner)
    monkeypatch.setattr(installer.grp, "getgrgid", lambda _: SimpleNamespace(gr_name="staff"))
    node, entrypoint, profile, binary = [tmp_path / name for name in (
        "node with spaces", "runtime/openclaw.mjs", "gateway.sb", "openclaw")]
    for path in (node, entrypoint, profile, binary):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")
    node.chmod(0o755)
    binary.chmod(0o755)
    arguments = ["/usr/bin/env", "-i", "HOME=" + str(tmp_path), "API_TOKEN=private-fixture-value",
                 "OPENCLAW_CONFIG_PATH=" + str(tmp_path / 'openclaw.json'),
                 "OPENCLAW_STATE_DIR=" + str(tmp_path), "TMPDIR=" + str(tmp_path),
                 "DOCKER_CONFIG=" + str(tmp_path), "DOCKER_HOST=unix://" + str(tmp_path / 'docker.sock'),
                 "/usr/bin/sandbox-exec", "-f", str(profile), str(node), str(entrypoint),
                 "gateway", "run", "--port", "18789"]
    document = {"Label": installer._launchd.GATEWAY_LABEL, "ProgramArguments": arguments,
                "WorkingDirectory": str(tmp_path), "StandardOutPath": str(tmp_path / "out.log"),
                "StandardErrorPath": str(tmp_path / "err.log")}
    source = tmp_path / "gateway.plist"
    source.write_bytes(plistlib.dumps(document))
    (tmp_path / 'openclaw.json').write_text('{}')
    (tmp_path / 'docker.sock').touch()
    (tmp_path / '.openclaw').mkdir(mode=0o700)
    if os.getuid() == 0:
        os.chown(tmp_path, owner.pw_uid, owner.pw_gid)
        os.chown(tmp_path / '.openclaw', owner.pw_uid, owner.pw_gid)
    (tmp_path / ".env").write_text('DASHBOARD_API_KEY="' + "a" * 64 + '"\nPIXEL_OPENWEBUI_KEY=' + "b" * 64 + "\n")
    return dict(install_dir=str(tmp_path), owner_name=owner.pw_name,
                source_plist=str(source), openclaw_bin=str(binary), gateway_port=18789)


def test_plan_preserves_runtime_arguments_and_never_mutates_source(deployment):
    source = Path(deployment["source_plist"])
    before = source.read_bytes()
    plan = installer.make_plan(**deployment)
    _, command = installer._env_assignments(plan["gateway"]["ProgramArguments"])
    assert command == ["/usr/bin/sandbox-exec", "-f", str(installer.PROFILE),
                       str(installer.GATEWAY_LAUNCHER), "gateway", "run", "--port", "18789"]
    assert plan["gateway"]["UserName"] == "pixel-owner"
    assert plan["gateway_binding"]["process"]["executable"].endswith("/node with spaces")
    assert "'" + str(plan["node"]) + "'" in plan["launcher"].decode()
    assert source.read_bytes() == before


def test_plan_rejects_mismatched_port(deployment):
    with pytest.raises(installer.InstallError, match="port-mismatch"):
        installer.make_plan(**dict(deployment, gateway_port=18790))


def test_initial_plan_requires_bundle_and_does_not_adopt_installed_agent(deployment, bundled_deployment):
    with pytest.raises(installer.InstallError, match='initial-install-requires-new-protected-runtime'):
        installer.make_plan(**deployment, initial_install=True)
    plan = installer.make_plan(**bundled_deployment, initial_install=True)
    assert plan['initial_install'] is True
    agent = Path(plan['owner'].pw_dir) / 'Library/LaunchAgents' / (installer._launchd.GATEWAY_LABEL + '.plist')
    with pytest.raises(installer.InstallError, match='initial-install-requires-unloaded-template'):
        installer.make_plan(**dict(bundled_deployment, source_plist=str(agent)), initial_install=True)


@pytest.mark.parametrize('fault', [None, 'loaded', 'unknown', 'timeout', 'definition', 'registered', 'port'])
def test_initial_absence_is_explicit_and_closes_every_probe_socket(tmp_path, monkeypatch, fault):
    source = SimpleNamespace(target='gui/501/fixture', verify_definition=Mock(
        side_effect=installer.InstallError('changed-template') if fault == 'definition' else None))
    plan = {'owner': SimpleNamespace(pw_dir=str(tmp_path)),
            'access_settings': {'gateway_port': 18789}, 'access_port': 18790}
    if fault == 'registered':
        agent = tmp_path / 'Library/LaunchAgents' / (installer._launchd.GATEWAY_LABEL + '.plist')
        agent.parent.mkdir(parents=True)
        agent.write_text('existing unrelated definition')
    def command(args):
        assert args == ['/bin/launchctl', 'print', source.target]
        if fault == 'loaded': return 'loaded, even without a running PID'
        if fault == 'timeout': raise subprocess.TimeoutExpired(args, 1)
        raise installer.InstallError('host-command-failed', returncode=5 if fault == 'unknown' else 113)
    monkeypatch.setattr(installer, '_command', command)
    sockets = []
    def create(*args):
        listener = Mock()
        if fault == 'port' and sockets: listener.bind.side_effect = OSError('busy')
        sockets.append(listener)
        return listener
    monkeypatch.setattr(installer.socket, 'socket', create)
    if fault:
        with pytest.raises((installer.InstallError, subprocess.TimeoutExpired)):
            installer._require_initial_absence(plan, source)
    else:
        installer._require_initial_absence(plan, source)
        assert len(sockets) == 2
        sockets[0].bind.assert_called_once_with(('127.0.0.1', 18789))
        sockets[1].bind.assert_called_once_with(('127.0.0.1', 18790))
    for listener in sockets: listener.close.assert_called_once()


def test_initial_absence_rejects_actual_occupied_loopback_port(tmp_path, monkeypatch):
    import socket
    source = SimpleNamespace(target='gui/501/fixture', verify_definition=Mock())
    monkeypatch.setattr(installer, '_command', Mock(side_effect=
        installer.InstallError('host-command-failed', returncode=113)))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        plan = {'owner': SimpleNamespace(pw_dir=str(tmp_path)),
                'access_settings': {'gateway_port': listener.getsockname()[1]}, 'access_port': 18790}
        with pytest.raises(installer.InstallError, match='initial-install-port-unavailable'):
            installer._require_initial_absence(plan, source)


def test_initial_installer_cannot_activate_upgrade_plan():
    with pytest.raises(installer.InstallError, match='runtime-upgrade-activation-required'):
        installer.install({'upgrade_qualification': {'candidateDigest': 'fixture'}}, ROOT)


def test_upgrade_uses_distinct_config_and_rebinds_gateway(bundled_deployment):
    plan = installer.make_plan(**bundled_deployment)
    source = Path(plan['runtime_bundle']['source_config'])
    before = source.read_bytes()
    config = plan['runtime_bundle']['config_bytes']
    old_binding = plan['gateway_binding']['definition']
    installer._version_upgrade_config(plan)
    candidate = Path(plan['runtime_bundle']['config_path'])
    assert candidate != source and candidate.name == 'openclaw-' + bundled_deployment['bundle_digest'] + '.json'
    assert not candidate.exists() and source.read_bytes() == before
    assert plan['runtime_bundle']['config_bytes'] == config
    env, _ = installer._env_assignments(plan['gateway']['ProgramArguments'])
    assert env['OPENCLAW_CONFIG_PATH'] == str(candidate)
    assert plan['gateway_binding']['definition'] != old_binding
    assert plan['access_settings']['gateway_binding'] == plan['gateway_binding']


def managed_service_fixture(plan, monkeypatch):
    import base64
    owner = plan['owner']
    getpwnam = installer.pwd.getpwnam
    monkeypatch.setattr(installer.pwd, 'getpwnam', lambda name:
        SimpleNamespace(pw_uid=61000, pw_gid=61000) if name == '_ods_pixel_ops' else getpwnam(name))
    contract = installer._managed_service_contract()
    journal = str(installer._launchd.ACCESS_STATE / 'service-installation.json')
    versions = []
    for version in ('before', 'after'):
        contents = {path: (version + ':' + path).encode() for path in contract}
        definitions = {}
        for role in ('manager', 'promoter', 'operations'):
            path = '/Library/LaunchDaemons/com.ods.pixel-native-' + role + '.plist'
            document = dict(Label='com.ods.pixel-native-' + role,
                UserName={'manager': owner.pw_name, 'promoter': 'root', 'operations': '_ods_pixel_ops'}[role],
                ProgramArguments=['/usr/bin/env', '-i', '/usr/bin/python3', '-I', '-B',
                    '/usr/local/libexec/ods-pixel-services/' + role + '/fixture.py'],
                StandardOutPath='/private/var/log/' + role + '-' + version + '.log')
            contents[path] = plistlib.dumps(document)
            definitions[role] = dict(path=path, body=base64.b64encode(contents[path]).decode('ascii'))
        record = dict(schemaVersion=1, owner=owner.pw_uid,
            selection=plan['native_services'] if version == 'after' else {'expected_digest': 'a' * 64},
            progress={'phase': 'services-active'}, requiresGatewayProof=True,
            attempted=['manager', 'promoter', 'operations'], stopWitnesses={},
            recovery=dict(schemaVersion=1, owner=owner.pw_name, python='/usr/bin/python3',
                identity={'name': '_ods_pixel_ops', 'uid': 61000, 'gid': 61000,
                    'home': '/private/var/empty'}, definitions=definitions))
        contents[journal] = json.dumps(record).encode()
        versions.append(contents)
    return [dict(path=path, before=versions[0][path], after=versions[1][path], mode=mode, gid=gid)
        for path, (mode, gid) in contract.items()]


@pytest.mark.parametrize('fault', [None, 'pending', 'owner', 'phase', 'incomplete', 'foreign', 'drift', 'metadata'])
def test_managed_service_snapshots_preserve_existing_state_before_any_write(monkeypatch, fault):
    import pixel_macos_custody as custody
    plan = dict(owner=SimpleNamespace(pw_name='fixture', pw_uid=501, pw_gid=20),
        native_services={'expected_digest': 'b' * 64}, native_manager_port=3002,
        access_settings={'install_dir': '/Users/fixture/ods'},
        runtime_bundle={'source_config_bytes': b'{"agents":{"list":[{"id":"pixel","workspace":"/workspace"}]}}'})
    records = managed_service_fixture(plan, monkeypatch)
    journal = str(installer._launchd.ACCESS_STATE / 'service-installation.json')
    disk = {r['path']: r['before'] for r in records}
    old = json.loads(disk[journal])
    if fault == 'pending': old['requiresRecovery'] = True
    if fault == 'owner': old['owner'] = 502
    if fault == 'phase': old['progress']['phase'] = 'starting'
    disk[journal] = json.dumps(old).encode()
    monkeypatch.setattr(custody, 'protected_bytes', lambda path, **kwargs: disk[str(path)])
    checked = []
    def metadata(path, body, **kwargs):
        checked.append(path)
        assert body == disk[str(path)]
        if fault == 'metadata': raise installer.InstallError('existing-ods-file-unsafe')
    monkeypatch.setattr(installer, '_check_existing', metadata)
    monkeypatch.setattr(installer, '_verify_new_services',
        lambda previous: previous['native_services'] == old['selection'] or pytest.fail('wrong previous selection'))
    files = [(Path(r['path']), r['after'], r['mode'], r['gid']) for r in records if r['path'] != journal]
    if fault == 'incomplete': files.pop(0)
    if fault == 'foreign': files.append((Path('/foreign'), b'foreign', 0o644, 0))
    def publication(**kwargs):
        assert kwargs['workspace'] == Path('/workspace') and kwargs['port'] == 3002
        assert kwargs['identity'] == old['recovery']['identity']
        if fault == 'drift': disk[journal] += b'\n'
        return files
    monkeypatch.setattr(installer._native_services, 'publication_files', publication)
    monkeypatch.setattr(installer, '_write_exact', lambda *a, **kw: pytest.fail('snapshot wrote files'))
    if fault:
        with pytest.raises(installer.InstallError): installer._managed_service_snapshots(plan)
    else:
        result = installer._managed_service_snapshots(plan)
        assert len(result) == len(records) == len(checked)
        assert all(item['before'] == disk[item['path']] for item in result)
        updated = installer._managed_service_record(result, 'after')
        assert updated['selection'] == plan['native_services']
        assert updated['recovery']['identity'] == old['recovery']['identity']
        assert updated['recovery']['python'] == old['recovery']['python']
        assert not any('/results/' in r['path'] or '/workspace/' in r['path'] for r in result)


@pytest.mark.parametrize('managed', [False, True])
@pytest.mark.parametrize('invalid_version', [None, 'before', 'after'])
def test_upgrade_services_pin_files_processes_and_separate_stop_witnesses(bundled_deployment, monkeypatch, tmp_path, invalid_version, managed):
    import pixel_macos_custody as custody
    import pixel_access_bridge as bridge
    plan = installer.make_plan(**bundled_deployment)
    plan['upgrade_qualification'] = {'candidateDigest': plan['runtime_bundle']['digest']}
    files = installer._deployment_files(ROOT, plan)
    records = [dict(path=str(path), before=body, after=body, mode=attrs['mode'], gid=attrs['gid'])
               for path, body, attrs in files]
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', tmp_path)
    if managed:
        plan['native_services'] = {'expected_digest': 'b' * 64}
        records.extend(managed_service_fixture(plan, monkeypatch))
    on_disk = {r['path']: r['before'] for r in records}
    on_disk['/usr/bin/python3'] = b'protected-python-fixture'
    monkeypatch.setattr(custody, 'protected_bytes', lambda path, **kwargs: on_disk[str(path)])
    if invalid_version:
        record = next(r for r in records if r['path'] == str(installer._launchd.ACCESS_PLIST))
        document = plistlib.loads(record[invalid_version])
        document['ProgramArguments'].remove('-I')
        record[invalid_version] = plistlib.dumps(document)
        with pytest.raises(installer.InstallError, match='launchd-provider-environment-unavailable'):
            installer._upgrade_services(plan, records)
        assert not list(tmp_path.glob('runtime-upgrade-stop-*.json'))
        return
    previous, candidate = installer._upgrade_services(plan, records)
    roles = installer._upgrade.NATIVE_ROLES if managed else installer._upgrade.CORE_ROLES
    assert set(previous) == set(candidate) == set(roles)
    for role in previous:
        assert previous[role].target == candidate[role].target
        previous[role].verify_definition()
        path = str(candidate[role].plist)
        original = on_disk[path]
        on_disk[path] = next(r['after'] for r in records if r['path'] == path)
        candidate[role].verify_definition()
        on_disk[path] = original
    assert previous['gateway'].process == plan['source_process']
    assert candidate['gateway'].process == plan['gateway_binding']['process']
    assert candidate['access'].process is None
    for version, services in [('previous', previous), ('candidate', candidate)]:
        for role, service in services.items():
            service.save_stop({'fixture': version + '-' + role})
    witnesses = list(tmp_path.glob('runtime-upgrade-stop-*.json'))
    assert len(witnesses) == 2 * len(roles)
    assert all(p.stat().st_mode & 0o077 == 0 for p in witnesses)
    real_read = bridge.private_json
    monkeypatch.setattr(bridge, 'private_json', lambda path, uid, maximum:
                        real_read(path, os.getuid(), maximum))
    # The callback imported private_json when built: recreate to simulate owner.
    previous, candidate = installer._upgrade_services(plan, records)
    assert candidate['relay'].load_stop() == {'fixture': 'candidate-relay'}
    on_disk[str(installer._launchd.GATEWAY_PLIST)] = b'changed'
    with pytest.raises(installer.InstallError, match='native-deployment-changed'):
        previous['gateway'].verify_definition()


@pytest.mark.parametrize('state', ['absent', 'loaded', 'disabled', 'unknown'])
def test_upgrade_start_requires_absent_enabled_service(monkeypatch, state):
    service = SimpleNamespace(target='system/com.ods.test', plist=Path('/Library/LaunchDaemons/com.ods.test.plist'),
                              verify_definition=Mock(), process_identity=Mock(), save_stop=Mock(),
                              _stopping_tree=('stale',))
    commands = []
    def command(args):
        commands.append(args)
        if args[1] == 'print':
            if state == 'absent': raise installer.InstallError('host-command-failed', returncode=113)
            if state == 'unknown': raise installer.InstallError('host-command-failed', returncode=5)
            return 'loaded'
        service.save_stop.assert_called_once_with(None)
        assert service._stopping_tree is None
        return ''
    monkeypatch.setattr(installer, '_command', command)
    monkeypatch.setattr(installer, '_job_disabled', lambda _: state == 'disabled')
    wait = Mock()
    monkeypatch.setattr(installer, '_wait_running', wait)
    if state == 'absent':
        installer._start_upgrade_service(service)
        assert commands[-1] == ['/bin/launchctl', 'bootstrap', 'system', str(service.plist)]
        wait.assert_called_once_with(service)
        service.process_identity.assert_called_once()
    else:
        with pytest.raises(installer.InstallError): installer._start_upgrade_service(service)
        assert not any('bootstrap' in command for command in commands)
        wait.assert_not_called()
        service.save_stop.assert_not_called()


@pytest.mark.parametrize('state', ['previous', 'candidate', 'absent', 'unknown', 'identity-failed'])
def test_upgrade_observer_requires_matching_definition_and_live_identity(monkeypatch, state):
    previous, candidate = [SimpleNamespace(target='system/test', verify_definition=Mock(),
        process_identity=Mock()) for _ in range(2)]
    def command(args):
        if state == 'absent':
            raise installer.InstallError('host-command-failed', returncode=113)
        if state == 'unknown':
            raise installer.InstallError('host-command-failed', returncode=5)
        return 'loaded'
    monkeypatch.setattr(installer, '_command', command)
    if state == 'candidate':
        previous.verify_definition.side_effect = installer.InstallError('native-deployment-changed')
    if state == 'identity-failed':
        previous.process_identity.side_effect = installer.InstallError('gateway-process-mismatch')
    if state in ('unknown', 'identity-failed'):
        with pytest.raises(installer.InstallError):
            installer._observe_upgrade_service(previous, candidate)
        candidate.process_identity.assert_not_called()
    else:
        assert installer._observe_upgrade_service(previous, candidate) == state
        if state == 'absent':
            previous.process_identity.assert_not_called()
            candidate.process_identity.assert_not_called()
        else:
            (previous if state == 'previous' else candidate).process_identity.assert_called_once()


@pytest.mark.parametrize('drift', [False, True])
def test_service_baseline_rebinding_uses_both_approved_snapshots(monkeypatch, drift):
    import pixel_gateway_service as services
    monkeypatch.setattr(services, 'launchd_definition_digest', lambda doc, error: doc['digest'])
    monkeypatch.setattr(installer._policy, 'validate_receipt', lambda value: value)
    records = [dict(path=str(installer._destination(installer._launchd.GATEWAY_PLIST)),
        before=plistlib.dumps({'digest': 'old'}), after=plistlib.dumps({'digest': 'new'})),
        dict(path=str(installer._destination(installer.ACCESS_FILES['config'])),
        before=json.dumps({'gateway_policy': {'id': 'old-policy'}}).encode(),
        after=json.dumps({'gateway_policy': {'id': 'new-policy'}}).encode())]
    boundary = dict(schemaVersion=1, platform='macos-launchd',
        target=installer._launchd.GATEWAY_TARGET, plist=str(installer._launchd.GATEWAY_PLIST),
        definition='foreign' if drift else 'old', policy={'id': 'old-policy', 'activeMode': 'sandboxed'})
    body = json.dumps({'boundary': json.dumps(boundary)}).encode()
    if drift:
        with pytest.raises(installer.InstallError, match='service-baseline-drift'):
            installer._upgrade_service_baseline(body, records)
    else:
        updated = json.loads(installer._upgrade_service_baseline(body, records))
        expected = dict(boundary, definition='new', policy={'id': 'new-policy', 'activeMode': 'sandboxed'})
        assert json.loads(updated['boundary']) == expected
        reversed_records = [dict(r, before=r['after'], after=r['before']) for r in records]
        restored = installer._upgrade_service_baseline(json.dumps(updated).encode(), reversed_records)
        assert json.loads(json.loads(restored)['boundary']) == boundary


def test_upgrade_snapshots_journal_existing_service_baseline(tmp_path, monkeypatch):
    import pixel_macos_custody as custody
    baseline = tmp_path / 'service-baseline.json'
    baseline.write_bytes(b'old-baseline')
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', tmp_path)
    monkeypatch.setattr(installer, '_deployment_files', lambda *_: [])
    monkeypatch.setattr(installer, '_upgrade_policy_mode', lambda _: 'sandboxed')
    monkeypatch.setattr(custody, 'protected_bytes', lambda path, **_: path.read_bytes())
    check = Mock()
    convert = Mock(return_value=b'new-baseline')
    monkeypatch.setattr(installer, '_check_existing', check)
    monkeypatch.setattr(installer, '_upgrade_service_baseline', convert)
    records, additions = installer._upgrade_file_snapshots({
        'upgrade_qualification': {'fixture': True},
        'access_settings': {'gateway_policy': {'profiles': {}}}}, ROOT)
    assert not additions
    assert records == [dict(path=str(baseline), before=b'old-baseline',
                            after=b'new-baseline', mode=0o600, gid=0)]
    check.assert_called_once_with(baseline, b'old-baseline', mode=0o600, gid=0)
    assert baseline.read_bytes() == b'old-baseline'


@pytest.mark.parametrize('restore', [False, True])
@pytest.mark.parametrize('migration', [False, True])
@pytest.mark.parametrize('fault', [None, 'live', 'hold', 'owner', 'child'])
def test_owner_receipt_adapter_requires_stopped_jobs_and_drops_identity(monkeypatch, restore, migration, fault):
    import hashlib
    owner = SimpleNamespace(pw_name='fixture', pw_uid=501, pw_gid=20)
    plan = {'owner': owner, 'source_environment': {'HOME': '/private/owner', 'SECRET': 'not-forwarded'},
            'runtime_bundle': {'source_config': '/private/config/old', 'config_path': '/private/config/new',
                               'source_config_bytes': b'old', 'config_bytes': b'new'}}
    if migration:
        plan['migration_qualification'] = {'approved': True}
        plan['migration_source_config_bytes'] = b'old'
        plan['source_environment']['OPENCLAW_CONFIG_PATH'] = '/private/config/old'
        plan['runtime_bundle']['source_config'] = '/owner/staging/candidate.json'
        plan['runtime_bundle']['source_config_bytes'] = b'staged-candidate-not-active'
    previous = {role: object() for role in ('gateway', 'access', 'relay')}
    candidate = {role: object() for role in previous}
    absent = Mock(side_effect=installer.InstallError('still-running') if fault == 'live' else None)
    hold = Mock(return_value={'phase': 'held'},
                side_effect=installer.InstallError('not-held') if fault == 'hold' else None)
    monkeypatch.setattr(installer, '_assert_upgrade_absent', absent)
    monkeypatch.setattr(installer, '_resume_migration_hold', hold)
    monkeypatch.setattr(installer.pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=0, pw_gid=0) if fault == 'owner' else owner)
    monkeypatch.setattr(installer.os, 'getgrouplist', lambda *a: [20])
    run = Mock(return_value=SimpleNamespace(returncode=1 if fault == 'child' else 0,
                                          stdout='{"relocated":true}'))
    monkeypatch.setattr(installer.subprocess, 'run', run)
    if fault:
        with pytest.raises(installer.InstallError):
            installer._relocate_upgrade_receipt(plan, previous, candidate, restore=restore)
        if fault != 'child': run.assert_not_called()
    else:
        installer._relocate_upgrade_receipt(plan, previous, candidate, restore=restore)
        assert absent.call_count == 6 and hold.call_count == 2
        options = run.call_args.kwargs
        assert (options['user'], options['group'], options['extra_groups']) == (501, 20, [20])
        assert options['env'] == {'HOME': '/private/owner', 'PATH': '/usr/bin:/bin'}
        payload = json.loads(options['input'])
        assert payload['source'] == '/private/config/' + ('new' if restore else 'old')
        assert payload['targetHash'] == hashlib.sha256(b'old' if restore else b'new').hexdigest()


@pytest.mark.parametrize('fault', [None, 'child', 'mode', 'shape', 'custody', 'owner'])
def test_post_upgrade_reproof_requires_installed_helper_and_matching_mode(monkeypatch, fault):
    import pixel_macos_custody as custody
    owner = SimpleNamespace(pw_name='owner', pw_uid=501, pw_gid=20, pw_dir='/Users/owner')
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer.pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=0, pw_gid=0) if fault == 'owner' else owner)
    monkeypatch.setattr(installer.os, 'getgrouplist', lambda *args: [20])
    protected = Mock(return_value=b'protected-helper',
        side_effect=ValueError('unsafe') if fault == 'custody' else None)
    monkeypatch.setattr(custody, 'protected_bytes', protected)
    monkeypatch.setattr(installer._policy, 'policy_state', lambda _: {'activeMode': 'full-access'})
    value = {'result': 'reproved', 'mode': 'sandboxed' if fault == 'mode' else 'full-access'}
    if fault == 'shape': value['untrusted'] = True
    run = Mock(return_value=SimpleNamespace(returncode=1 if fault == 'child' else 0, stdout=json.dumps(value)))
    monkeypatch.setattr(installer.subprocess, 'run', run)
    plan = {'owner': owner, 'access_settings': {'gateway_policy': {}}}
    if fault:
        with pytest.raises(ValueError):
            installer._reprove_installed_access(plan)
        if fault in ('custody', 'owner'): run.assert_not_called()
    else:
        installer._reprove_installed_access(plan)
        assert run.call_args.args[0] == ['/usr/bin/python3', '-I',
            str(installer.ACCESS_PROGRAM_ROOT / 'pixel_access_reconcile.py'), '--startup']
        assert run.call_args.kwargs['user'] == 501
        assert run.call_args.kwargs['stdin'] == installer.subprocess.DEVNULL


@pytest.mark.parametrize('fault', [None, 'owner', 'gateway_target', 'state_dir', 'custody', 'proof'])
def test_recovery_reproof_uses_current_protected_settings(monkeypatch, fault):
    import pixel_macos_custody as custody
    owner = SimpleNamespace(pw_name='owner', pw_uid=501, pw_gid=20, pw_dir='/Users/owner')
    settings = {'owner': 'owner', 'gateway_target': installer._launchd.GATEWAY_TARGET,
                'state_dir': str(installer._launchd.ACCESS_STATE),
                'gateway_policy': {'restored-policy': True}}
    if fault in ('owner', 'gateway_target', 'state_dir'):
        settings[fault] = 'unrelated'
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer.pwd, 'getpwnam', lambda name: owner)
    protected = Mock(return_value=json.dumps(settings).encode(),
                     side_effect=ValueError('custody') if fault == 'custody' else None)
    monkeypatch.setattr(custody, 'protected_bytes', protected)
    proof = Mock(side_effect=installer.InstallError('access-reproof-required') if fault == 'proof' else None)
    monkeypatch.setattr(installer, '_reprove_installed_access', proof)
    if fault:
        with pytest.raises(ValueError):
            installer._reprove_recovered_access('owner')
        if fault != 'proof': proof.assert_not_called()
    else:
        installer._reprove_recovered_access('owner')
        proof.assert_called_once_with({'owner': owner, 'access_settings': settings})
    protected.assert_called_once_with(installer._destination(installer.ACCESS_FILES['config']))


def test_upgrade_stop_requires_process_tree_proof_even_when_job_absent(monkeypatch):
    service = SimpleNamespace(assert_stopped=Mock(side_effect=installer.InstallError('native-idle-unconfirmed')))
    monkeypatch.setattr(installer, '_stop_loaded', lambda _: None)
    with pytest.raises(installer.InstallError, match='native-idle-unconfirmed'):
        installer._stop_upgrade_service(service)


@pytest.mark.parametrize('missing', [None, 'policy', 'program', 'key'])
def test_upgrade_snapshots_only_allow_new_policy_files(tmp_path, monkeypatch, missing):
    import pixel_macos_custody as custody
    program, policy, key = [tmp_path / name for name in ('program', 'policy', 'key')]
    for path in (program, policy, key): path.write_bytes(b'old')
    if missing: {'policy': policy, 'program': program, 'key': key}[missing].unlink()
    rows = [(program, b'new', dict(mode=0o644)), (policy, b'new-policy', dict(mode=0o644)),
            (key, b'old', dict(mode=0o600, uid=501, gid=20))]
    plan = {'upgrade_qualification': {'fixture': True}, 'access_settings': {
        'gateway_policy': {'profiles': {'sandboxed': {'path': str(policy)}}}}}
    monkeypatch.setattr(installer, '_deployment_files', lambda *_: rows)
    monkeypatch.setattr(installer, '_upgrade_policy_mode', lambda _: 'sandboxed')
    monkeypatch.setitem(installer.ACCESS_FILES, 'key', key)
    monkeypatch.setattr(installer, '_preflight_directory', lambda _: None)
    monkeypatch.setattr(installer, '_check_existing', lambda *a, **k: None)
    monkeypatch.setattr(custody, 'protected_bytes', lambda path, **_: path.read_bytes())
    if missing in ('program', 'key'):
        with pytest.raises(installer.InstallError, match='missing'):
            installer._upgrade_file_snapshots(plan, ROOT)
    else:
        records, additions = installer._upgrade_file_snapshots(plan, ROOT)
        assert all(r['path'] != str(key) for r in records)
        assert records[0]['before'] == b'old' and records[0]['after'] == b'new'
        assert len(additions) == (1 if missing == 'policy' else 0)
        assert program.read_bytes() == b'old'


@pytest.mark.parametrize('mode', ['sandboxed', 'full-access'])
def test_upgrade_preserves_verified_active_policy_bytes(tmp_path, monkeypatch, mode):
    import pixel_macos_custody as custody
    profile = tmp_path / 'active.sb'
    profile.write_bytes(b'old-policy')
    monkeypatch.setattr(installer, 'PROFILE', profile)
    plan = {'upgrade_qualification': {'fixture': True}, 'policies': {
        'sandboxed': b'new-sandbox', 'full-access': b'new-full'},
        'access_settings': {'gateway_policy': {'profiles': {}}}}
    monkeypatch.setattr(installer, '_deployment_files', lambda *_:
                        [(profile, b'new-sandbox', {'mode': 0o644})])
    monkeypatch.setattr(installer, '_upgrade_policy_mode', lambda _: mode)
    monkeypatch.setattr(installer, '_preflight_directory', lambda _: None)
    monkeypatch.setattr(installer, '_check_existing', lambda *a, **k: None)
    monkeypatch.setattr(custody, 'protected_bytes', lambda path, **_: path.read_bytes())
    records, additions = installer._upgrade_file_snapshots(plan, ROOT)
    assert records[0]['after'] == plan['policies'][mode]
    assert records[0]['before'] == b'old-policy' and not additions


def test_upgrade_rejects_controller_binding_drift_before_policy_selection(bundled_deployment, monkeypatch):
    import pixel_macos_custody as custody
    plan = installer.make_plan(**bundled_deployment)
    original = plistlib.loads(plan['source_bytes'])
    original['UserName'] = plan['owner'].pw_name
    plan['source_bytes'] = plistlib.dumps(original)
    settings = dict(plan['access_settings'])
    settings['gateway_binding'] = {'not': 'the active definition'}
    monkeypatch.setattr(custody, 'protected_bytes', lambda _: json.dumps(settings).encode())
    policy = Mock()
    monkeypatch.setattr(installer._policy, 'policy_state', policy)
    with pytest.raises(installer.InstallError, match='controller-binding-changed'):
        installer._upgrade_policy_mode(plan)
    policy.assert_not_called()


@pytest.mark.parametrize('change', [None, 'launcher', 'path', 'owner'])
def test_system_source_resolves_only_exact_protected_launcher(deployment, monkeypatch, change):
    source = Path(deployment['source_plist'])
    document = plistlib.loads(source.read_bytes())
    env, command = installer._env_assignments(document['ProgramArguments'])
    root = source.parent / 'bundles' / ('a' * 64)
    (root / 'runtime').mkdir(parents=True)
    (root / 'node').write_text('node fixture')
    (root / 'node').chmod(0o755)
    (root / 'runtime/openclaw.mjs').write_text('entry fixture')
    launcher = source.parent / 'protected-launcher'
    launcher.write_bytes(installer._launcher_bytes(root / 'node', root / 'runtime/openclaw.mjs'))
    monkeypatch.setattr(installer, 'GATEWAY_LAUNCHER', launcher)
    monkeypatch.setattr(installer._bundle, 'INSTALL_ROOT', root.parent)
    monkeypatch.setattr(installer._launchd, 'GATEWAY_PLIST', source)
    env['PATH'] = str(root) + ':/usr/bin:/bin'
    document['UserName'] = deployment['owner_name']
    if change == 'launcher':
        launcher.write_text('different executable')
    if change == 'path':
        env['PATH'] = '/tmp/untrusted:/usr/bin'
    if change == 'owner':
        document.pop('UserName')
    document['ProgramArguments'] = ['/usr/bin/env', '-i',
        *(k + '=' + v for k, v in env.items()), *command[:3], str(launcher), *command[5:]]
    source.write_bytes(plistlib.dumps(document))
    if change:
        with pytest.raises(installer.InstallError, match='native-system-'):
            installer._source_gateway(source, deployment['owner_name'], 18789)
    else:
        result = installer._source_gateway(source, deployment['owner_name'], 18789)
        assert result[-2:] == (root / 'node', root / 'runtime/openclaw.mjs')


def test_plan_pins_distinct_profiles_and_starts_sandboxed(deployment):
    plan = installer.make_plan(**deployment)
    receipt = plan['access_settings']['gateway_policy']
    assert receipt == installer._policy.deployment_receipt('/private/etc/ods', plan['policies'])
    files = {str(path): body for path, body, _ in installer._deployment_files(ROOT, plan)}
    assert files[receipt['active']] == plan['policies']['sandboxed']
    for mode, record in receipt['profiles'].items():
        assert files[record['path']] == plan['policies'][mode]
        assert b'(deny file-write*' in files[record['path']]
    assert plan['profile'].read_bytes() not in plan['policies'].values()


def test_plan_requires_explicit_native_paths_for_policy(deployment):
    source = Path(deployment['source_plist'])
    document = plistlib.loads(source.read_bytes())
    document['ProgramArguments'] = [arg for arg in document['ProgramArguments']
                                    if not arg.startswith('OPENCLAW_STATE_DIR=')]
    source.write_bytes(plistlib.dumps(document))
    with pytest.raises(installer.InstallError, match='policy-runtime-path-required'):
        installer.make_plan(**deployment)


@pytest.mark.parametrize('failure', ['missing', 'public', 'symlink', 'file'])
def test_admission_home_must_exist_and_be_private_before_staging(deployment, failure):
    directory = Path(deployment['install_dir']) / '.openclaw'
    if failure == 'public':
        directory.chmod(0o755)
    else:
        directory.rmdir()
        if failure == 'symlink': directory.symlink_to(deployment['install_dir'], target_is_directory=True)
        elif failure == 'file': directory.touch()
    with pytest.raises(installer.InstallError, match='native-admission-home-required'):
        installer.make_plan(**deployment)


def test_owner_credential_must_differ_from_chat_credential(deployment):
    (Path(deployment["install_dir"]) / ".env").write_text(
        "DASHBOARD_API_KEY=" + "a" * 64 + "\nPIXEL_OPENWEBUI_KEY=" + "a" * 64 + "\n")
    with pytest.raises(installer.InstallError, match="distinct-dashboard"):
        installer.make_plan(**deployment)


def test_missing_validator_fails_during_plan(deployment):
    Path(deployment["openclaw_bin"]).unlink()
    with pytest.raises(installer.InstallError, match="validator-unavailable"):
        installer.make_plan(**deployment)


def test_dry_run_output_contains_no_environment_or_credentials(deployment, capsys):
    assert installer.main(["--source", str(ROOT), "--install-dir", deployment["install_dir"],
                           "--owner", deployment["owner_name"], "--gateway-plist", deployment["source_plist"],
                           "--openclaw-bin", deployment["openclaw_bin"]]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["mode"] == "dry-run"
    assert "private-fixture-value" not in output
    assert "a" * 64 not in output
    assert "ProgramArguments" not in output


@pytest.mark.parametrize('kind', ['stream-progress', 'workspace-root'])
def test_upgrade_cli_selects_system_plan_and_prints_only_public_identity(deployment, monkeypatch, capsys, kind):
    plan = installer.make_plan(**deployment)
    plan['upgrade_kind'] = kind
    planner = Mock(return_value=plan)
    monkeypatch.setattr(installer, 'make_upgrade_plan', planner)
    assert installer.main(['--source', str(ROOT), '--owner', deployment['owner_name'],
        '--openclaw-bin', deployment['openclaw_bin'], '--current-bundle-digest', 'c' * 64,
        '--runtime-bundle', '/candidate', '--bundle-digest', 'd' * 64,
        '--upgrade-kind', kind]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)['operation'] == 'runtime-upgrade'
    assert planner.call_args.kwargs['current_digest'] == 'c' * 64
    assert planner.call_args.kwargs['upgrade_kind'] == kind
    assert json.loads(output)['upgradeKind'] == kind
    assert 'source_plist' not in planner.call_args.kwargs
    assert 'private-fixture-value' not in output
    assert 'ProgramArguments' not in output


@pytest.mark.parametrize('error', [None, 'runtime-upgrade-recovery-required', 'private-error-detail'])
def test_upgrade_cli_activation_requires_explicit_flag_and_dispatches(deployment, monkeypatch, capsys, error):
    plan = installer.make_plan(**deployment)
    plan.update(runtime_bundle={'digest': 'd' * 64},
                upgrade_qualification={'currentDigest': 'c' * 64, 'candidateDigest': 'd' * 64},
                upgrade_kind='workspace-root')
    planner = Mock(return_value=plan)
    activate = Mock(return_value='active', side_effect=installer._upgrade.UpgradeError(error) if error else None)
    monkeypatch.setattr(installer, 'make_upgrade_plan', planner)
    monkeypatch.setattr(installer, 'upgrade_install', activate)

    assert installer.main(['--source', '/source', '--owner', deployment['owner_name'],
        '--openclaw-bin', deployment['openclaw_bin'], '--current-bundle-digest', 'c' * 64,
        '--runtime-bundle', '/candidate', '--bundle-digest', 'd' * 64,
        '--upgrade-kind', 'workspace-root', '--activate-upgrade']) == (1 if error else 0)

    activate.assert_called_once_with(plan, '/source')
    captured = capsys.readouterr()
    if error:
        assert not captured.out
        expected = error if error == 'runtime-upgrade-recovery-required' else 'native-runtime-upgrade-failed'
        assert captured.err == 'error: ' + expected + '\n'
        return
    output = json.loads(captured.out)
    assert output == {'operation': 'runtime-upgrade', 'runtimeBundleDigest': 'd' * 64,
                      'status': 'active'}


def test_upgrade_cli_activation_without_current_bundle_never_plans(monkeypatch, capsys):
    planner = Mock()
    monkeypatch.setattr(installer, 'make_upgrade_plan', planner)
    assert installer.main(['--source', '/source', '--openclaw-bin', '/bin/openclaw',
                           '--activate-upgrade']) == 1
    assert 'upgrade-activation-requires-current-bundle' in capsys.readouterr().err
    planner.assert_not_called()


def test_workspace_upgrade_dispatch_does_not_fall_back_to_streaming(monkeypatch):
    workspace = Mock(side_effect=ValueError('candidate rejected'))
    streaming = Mock()
    monkeypatch.setattr(installer._bundle, 'qualify_workspace_root_upgrade', workspace)
    monkeypatch.setattr(installer._bundle, 'qualify_stream_progress_upgrade', streaming)
    with pytest.raises(ValueError, match='candidate rejected'):
        installer._qualify_upgrade('workspace-root', '/old', '/new',
                                   current_digest='a' * 64, candidate_digest='b' * 64)
    streaming.assert_not_called()
    with pytest.raises(installer.InstallError, match='unsupported-runtime-upgrade-kind'):
        installer._qualify_upgrade('arbitrary', '/old', '/new',
                                   current_digest='a' * 64, candidate_digest='b' * 64)


def test_upgrade_kind_requires_current_bundle_before_planning(monkeypatch, capsys):
    planner = Mock()
    monkeypatch.setattr(installer, 'make_plan', planner)
    assert installer.main(['--source', '/source', '--openclaw-bin', '/bin/openclaw',
                           '--upgrade-kind', 'workspace-root']) == 1
    assert 'upgrade-kind-requires-current-bundle' in capsys.readouterr().err
    planner.assert_not_called()


@pytest.mark.parametrize('extra', [['--install'], ['--gateway-plist', '/user.plist']])
def test_upgrade_cli_refuses_activation_and_user_source_before_planning(monkeypatch, capsys, extra):
    planner = Mock()
    monkeypatch.setattr(installer, 'make_upgrade_plan', planner)
    assert installer.main(['--source', '/source', '--openclaw-bin', '/bin/openclaw',
                           '--current-bundle-digest', 'c' * 64, *extra]) == 1
    assert 'runtime-upgrade-dry-run-only' in capsys.readouterr().err
    planner.assert_not_called()


def test_private_state_accepts_root_readable_parents(monkeypatch):
    target = Path("/private/var/lib/ods-pixel-access")
    monkeypatch.setattr(Path, "lstat", lambda p: SimpleNamespace(
        st_uid=0, st_mode=stat.S_IFDIR | (0o700 if p == target else 0o755)))
    installer._check_directory(target, private=True)


@pytest.mark.parametrize("mode", [stat.S_IFDIR | 0o777, stat.S_IFLNK | 0o755])
def test_state_rejects_writable_or_symlink_parent(monkeypatch, mode):
    monkeypatch.setattr(Path, "lstat", lambda p: SimpleNamespace(
        st_uid=0, st_mode=mode if p == Path("/private/var/lib") else stat.S_IFDIR | 0o700))
    with pytest.raises(installer.InstallError, match="custody"):
        installer._check_directory("/private/var/lib/ods-pixel-access", private=True)


def test_controller_bundle_contains_model_modules_and_all_sources_exist():
    assert {"pixel_model_contract.py", "pixel_model_coordinator.py"} <= set(installer.HOST_FILES)
    host = ROOT / "extensions/services/pixel-agent/host"
    for name in installer.HOST_FILES:
        assert (host / name).is_file() or (ROOT / "bin" / name).is_file(), name
    for package, names in (("pixel_settings", installer.SETTINGS_FILES),
                           ("pixel_provider", installer.PROVIDER_FILES)):
        for name in names:
            assert (ROOT / "bin" / package / name).is_file(), name


def test_missing_source_is_rejected_before_any_write(bundled_deployment, monkeypatch, tmp_path):
    plan = installer.make_plan(**bundled_deployment)
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0)
    write, run = Mock(), Mock()
    monkeypatch.setattr(installer, "_write_exact", write)
    monkeypatch.setattr(installer.subprocess, "run", run)
    with pytest.raises(FileNotFoundError):
        installer.install(plan, tmp_path / "missing-source")
    write.assert_not_called()
    run.assert_not_called()


def test_last_destination_conflict_leaves_everything_untouched(bundled_deployment, monkeypatch, tmp_path):
    plan = installer.make_plan(**bundled_deployment)
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0)
    conflict = tmp_path / "existing.plist"
    conflict.write_bytes(b"existing deployment")
    first = tmp_path / "not-created" / "first.py"
    monkeypatch.setattr(installer, "_deployment_files", lambda *_: [
        (first, b"new", dict(mode=0o644)),
        (conflict, b"different", dict(mode=0o644)),
    ])
    monkeypatch.setattr(installer, "_check_directory", lambda *a, **k: None)
    run = Mock()
    monkeypatch.setattr(installer.subprocess, "run", run)
    with pytest.raises(installer.InstallError, match="existing-ods-file-drift"):
        installer.install(plan, ROOT)
    assert not first.parent.exists()
    assert conflict.read_bytes() == b"existing deployment"
    run.assert_not_called()


def test_parent_symlink_is_rejected_before_mkdir(monkeypatch, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(installer.InstallError, match="custody"):
        installer._write_exact(alias / "missing" / "file", b"new", mode=0o644)
    assert not (real / "missing").exists()


@pytest.mark.parametrize("code", [0, 1, 5, 126])
def test_existing_or_unreadable_system_job_is_not_absence(monkeypatch, code):
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], code))
    with pytest.raises(installer.InstallError, match="existing-system|absence-unconfirmed"):
        installer._preflight_jobs()


def test_job_absence_requires_all_fixed_system_targets(monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 113))
    monkeypatch.setattr(installer.subprocess, "run", run)
    installer._preflight_jobs()
    assert [call.args[0] for call in run.call_args_list] == [
        ["/bin/launchctl", "print", installer._launchd.GATEWAY_TARGET],
        ["/bin/launchctl", "print", installer._launchd.ACCESS_TARGET],
        ["/bin/launchctl", "print", installer._launchd.RELAY_TARGET],
    ]


def test_materialized_sources_do_not_change_after_plan(deployment, tmp_path, monkeypatch):
    plan = installer.make_plan(**deployment)
    files = installer._deployment_files(ROOT, plan)
    profile_bytes = plan['policies']['sandboxed']
    plan["profile"].write_bytes(b"changed after planning")
    assert next(data for path, data, _ in files
                if path == installer._destination(installer.PROFILE)) == profile_bytes
    assert len({path for path, _, _ in files}) == len(files)


def test_write_checks_parent_again_after_creation(monkeypatch, tmp_path):
    check = Mock(side_effect=[None, installer.InstallError("custody-changed")])
    monkeypatch.setattr(installer, "_check_directory", check)
    destination = tmp_path / "staged" / "file"
    with pytest.raises(installer.InstallError, match="custody-changed"):
        installer._write_exact(destination, b"new", mode=0o644)
    assert not destination.exists()


@pytest.fixture
def migration(deployment, tmp_path, monkeypatch):
    plan = installer.make_plan(**deployment)
    events, failures, applied_failures = [], set(), set()

    def event(name):
        events.append(name)
        if name in failures:
            failures.remove(name)
            raise installer.InstallError('simulated-' + name)

    class Service:
        def __init__(self, name, target, plist, loaded=False):
            self.name, self.target, self.plist, self.loaded = name, target, plist, loaded
            self.disabled = False
        def pid(self, **_):
            event('pid-' + self.name)
            if not self.loaded:
                raise installer.InstallError('host-command-failed', returncode=113)
            return 123
        def stop(self):
            event('stop-' + self.name)
            self.loaded = False
        def assert_stopped(self):
            event('stopped-' + self.name)
            assert not self.loaded
        def verify_definition(self):
            event('verify-' + self.name)
        def process_identity(self):
            event('identity-' + self.name)
            return (123, 1, 1)

    old = Service('old', 'gui/501/' + installer._launchd.GATEWAY_LABEL, plan['source'], True)
    gateway = Service('gateway', installer._launchd.GATEWAY_TARGET, installer._launchd.GATEWAY_PLIST)
    access = Service('access', installer._launchd.ACCESS_TARGET, installer._launchd.ACCESS_PLIST)
    relay = Service('relay', installer._launchd.RELAY_TARGET, installer._launchd.RELAY_PLIST)
    services = (old, gateway, access, relay)

    def command(argv, **_):
        operation = argv[1]
        service = next(s for s in services if str(s.plist) == argv[-1] or s.target == argv[-1])
        name = operation + '-' + service.name
        event(name)
        if operation == 'bootstrap': service.loaded = True
        elif operation in ('enable', 'disable'): service.disabled = operation == 'disable'
        else: raise AssertionError(argv)
        if name in applied_failures:
            applied_failures.remove(name)
            raise subprocess.TimeoutExpired(argv, 1)
        return ''

    monkeypatch.setattr(installer, '_command', command)
    monkeypatch.setattr(installer, '_wait_running', lambda s: event('wait-' + s.name))
    monkeypatch.setattr(installer, '_ready_gateway', lambda s, port: event('ready-' + s.name))
    monkeypatch.setattr(installer, '_ready_access', lambda s: event('ready-' + s.name))
    monkeypatch.setattr(installer, '_ready_access_relay', lambda s, p: event('ready-' + s.name))
    return SimpleNamespace(plan=plan, services=services, old=old, gateway=gateway, access=access, relay=relay,
                           journal=tmp_path / 'installation.json', events=events,
                           failures=failures, applied_failures=applied_failures)


def test_handover_stops_original_before_starting_new_services(migration):
    m = migration
    installer._activate(m.plan, m.services, m.journal)
    assert m.old.disabled and not m.old.loaded
    assert m.gateway.loaded and m.access.loaded
    assert m.relay.loaded and not m.relay.disabled
    assert not m.gateway.disabled and not m.access.disabled
    assert m.events.index('stopped-old') < m.events.index('bootstrap-gateway')
    assert m.events.index('bootstrap-access') < m.events.index('bootstrap-relay')
    assert 'ready-relay' in m.events
    assert json.loads(m.journal.read_bytes())['phase'] == 'active'
    assert stat.S_IMODE(m.journal.stat().st_mode) == 0o600
    assert b'private-fixture' not in m.journal.read_bytes()


@pytest.mark.parametrize('failure', [None, 'bootstrap-gateway', 'wait-gateway', 'ready-gateway',
    'bootstrap-access', 'wait-access', 'ready-access', 'bootstrap-relay', 'wait-relay', 'ready-relay'])
def test_initial_activation_never_bootstraps_or_disables_source(migration, monkeypatch, failure):
    m = migration
    m.plan['initial_install'] = True
    m.old.loaded = False
    def absent(plan, old):
        assert plan is m.plan and old is m.old and not old.loaded
        m.events.append('initial-absent')
    monkeypatch.setattr(installer, '_require_initial_absence', absent)
    if failure:
        m.failures.add(failure)
        with pytest.raises(installer.InstallError, match='simulated-'):
            installer._activate(m.plan, m.services, m.journal)
    else:
        installer._activate(m.plan, m.services, m.journal)
    assert m.events.index('initial-absent') < m.events.index('bootstrap-gateway')
    assert not any(event.endswith('-old') for event in m.events)
    assert not m.old.loaded and not m.old.disabled
    for service in (m.gateway, m.access, m.relay):
        assert service.loaded is (failure is None)
        assert service.disabled is (failure is not None)
    state = json.loads(m.journal.read_text())
    assert state['operation'] == 'initial-install'
    assert state['phase'] == ('initial-stopped' if failure else 'active')


def test_initial_rollback_failure_keeps_recovery_required(migration, monkeypatch):
    m = migration
    m.plan['initial_install'] = True
    m.old.loaded = False
    monkeypatch.setattr(installer, '_require_initial_absence', lambda *args: None)
    m.failures.update(('ready-access', 'stop-gateway'))
    with pytest.raises(installer.InstallError, match='recovery-required'):
        installer._activate(m.plan, m.services, m.journal)
    assert not m.old.loaded and 'bootstrap-old' not in m.events
    assert json.loads(m.journal.read_text())['phase'] == 'recovery-required'


@pytest.mark.parametrize('failure', ['disable-old', 'stop-old', 'stopped-old', 'enable-gateway',
    'bootstrap-gateway', 'wait-gateway', 'bootstrap-access', 'wait-access', 'ready-gateway', 'ready-access',
    'bootstrap-relay', 'wait-relay', 'ready-relay'])
def test_activation_failure_restores_previous_gateway(migration, failure):
    m = migration
    m.failures.add(failure)
    with pytest.raises(installer.InstallError, match='simulated-'):
        installer._activate(m.plan, m.services, m.journal)
    assert m.old.loaded and not m.old.disabled
    assert not m.gateway.loaded and not m.access.loaded
    assert m.gateway.disabled and m.access.disabled
    assert not m.relay.loaded and m.relay.disabled
    assert json.loads(m.journal.read_bytes())['phase'] == 'restored'
    assert 'ready-old' in m.events
    if 'stop-gateway' in m.events:
        assert m.events.index('stopped-gateway') < m.events.index('enable-old')


@pytest.mark.parametrize('service', ['gateway', 'access', 'relay'])
def test_bootstrap_timeout_checks_and_stops_same_job_before_restore(migration, service):
    m = migration
    m.applied_failures.add('bootstrap-' + service)
    with pytest.raises(subprocess.TimeoutExpired):
        installer._activate(m.plan, m.services, m.journal)
    assert m.old.loaded and not m.gateway.loaded and not m.access.loaded
    assert m.events.count('bootstrap-' + service) == 1
    assert m.events.index('stopped-' + service) < m.events.index('bootstrap-old')


@pytest.mark.parametrize('failure', ['stop-access', 'stopped-gateway', 'verify-old', 'ready-old', 'stop-relay'])
def test_rollback_failure_retains_recovery_journal(migration, failure):
    m = migration
    m.failures.update(('ready-access', failure))
    with pytest.raises(installer.InstallError, match='migration-recovery-required'):
        installer._activate(m.plan, m.services, m.journal)
    assert m.gateway.disabled and m.access.disabled
    assert json.loads(m.journal.read_bytes())['phase'] == 'recovery-required'
    if failure != 'ready-old':
        assert not m.old.loaded
        assert 'enable-old' not in m.events


@pytest.mark.parametrize('body', [
    'disabled services = {\n"com.ods.test" => disabled\n}',
    'disabled services = {\n"com.ods.test" => enabled\n}',
    'disabled services = {\n"unrelated" => disabled\n}',
])
def test_disabled_state_parses_effective_job_setting(monkeypatch, body):
    monkeypatch.setattr(installer, '_command', lambda _: body)
    assert installer._job_disabled('gui/501/com.ods.test') == ('"com.ods.test" => disabled' in body)


@pytest.mark.parametrize('body', ['', '{}', 'disabled services = {\ninvalid\n}',
    'disabled services = {\n"com.ods.test" => enabled\n"com.ods.test" => disabled\n}'])
def test_ambiguous_disabled_state_fails_closed(monkeypatch, body):
    monkeypatch.setattr(installer, '_command', lambda _: body)
    with pytest.raises(installer.InstallError, match='disabled-state-unavailable'):
        installer._job_disabled('gui/501/com.ods.test')


def test_staging_failure_keeps_original_running_and_new_autostart_disabled(migration, bundled_deployment, monkeypatch):
    m = migration
    m.plan = installer.make_plan(**bundled_deployment)
    monkeypatch.setattr(installer, '_runtime_config', lambda *a, **k: None)
    monkeypatch.setattr(installer._bundle, 'publish', lambda *a, **k: Path(m.plan['runtime_bundle']['destination']))
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer, '_preflight_jobs', lambda: None)
    monkeypatch.setattr(installer, '_preflight_directory', lambda *a, **k: None)
    monkeypatch.setattr(installer, '_preflight_file', lambda *a, **k: None)
    monkeypatch.setattr(installer, '_check_directory', lambda *a, **k: None)
    monkeypatch.setattr(installer, '_activation_services', lambda _: m.services)
    monkeypatch.setattr(installer, '_job_disabled', lambda _: False)
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', m.journal.parent)
    write = Mock(side_effect=OSError('simulated storage failure'))
    monkeypatch.setattr(installer, '_write_exact', write)
    with pytest.raises(OSError, match='storage failure'):
        installer.install(m.plan, ROOT)
    assert m.old.loaded and not m.old.disabled
    assert m.gateway.disabled and m.access.disabled
    assert 'stop-old' not in m.events
    assert json.loads(m.journal.read_bytes())['phase'] == 'staging-failed'


@pytest.mark.parametrize('initial', [False, True])
@pytest.mark.parametrize('disabled_source_only', [False, True])
def test_disabled_user_job_is_preserved_before_staging(migration, bundled_deployment, monkeypatch,
                                                       initial, disabled_source_only):
    m = migration
    m.plan = installer.make_plan(**bundled_deployment)
    m.plan['initial_install'] = initial
    monkeypatch.setattr(installer, '_runtime_config', lambda *a, **k: None)
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer, '_preflight_jobs', lambda: None)
    monkeypatch.setattr(installer, '_preflight_directory', lambda *a, **k: None)
    monkeypatch.setattr(installer, '_preflight_file', lambda *a, **k: None)
    monkeypatch.setattr(installer, '_activation_services', lambda _: m.services)
    monkeypatch.setattr(installer, '_job_disabled',
        lambda target: target == m.old.target if disabled_source_only else True)
    absent = Mock()
    monkeypatch.setattr(installer, '_require_initial_absence', absent)
    def reached_staging(*args):
        raise installer.InstallError('test-reached-staging')
    monkeypatch.setattr(installer, '_migration_phase', reached_staging)
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', m.journal.parent / 'state')
    monkeypatch.setattr(installer, '_check_directory', lambda *a, **k: None)
    write = Mock()
    monkeypatch.setattr(installer, '_write_exact', write)
    expected = 'test-reached-staging' if initial and disabled_source_only else 'migration-disabled-job'
    with pytest.raises(installer.InstallError, match=expected):
        installer.install(m.plan, ROOT)
    if initial:
        absent.assert_called_once_with(m.plan, m.old)
    write.assert_not_called()


def test_rollback_checks_source_stop_again_before_bootstrap(migration):
    m = migration
    m.failures.add('ready-access')
    with pytest.raises(installer.InstallError):
        installer._activate(m.plan, m.services, m.journal)
    assert m.events.count('stopped-old') == 2
    assert max(i for i, event in enumerate(m.events) if event == 'stopped-old') < m.events.index('bootstrap-old')


def test_upgrade_identity_waits_for_launcher_exec(monkeypatch):
    service = object()
    identity = (123, 'verified-node')
    verify = Mock(side_effect=[installer.InstallError('gateway-process-executable-mismatch'), identity])
    monkeypatch.setattr(installer, '_upgrade_service_identity', verify)
    monkeypatch.setattr(installer.time, 'sleep', lambda _: None)
    assert installer._wait_upgrade_identity(service) == identity
    assert verify.call_count == 2


@pytest.mark.parametrize('code', ['native-deployment-changed', 'gateway-process-owner-mismatch',
                                'gateway-process-specification-required'])
def test_upgrade_identity_never_retries_custody_errors(monkeypatch, code):
    verify = Mock(side_effect=installer.InstallError(code))
    monkeypatch.setattr(installer, '_upgrade_service_identity', verify)
    with pytest.raises(installer.InstallError, match=code):
        installer._wait_upgrade_identity(object())
    assert verify.call_count == 1


def test_upgrade_identity_does_not_accept_persistent_wrong_executable(monkeypatch):
    monkeypatch.setattr(installer, '_upgrade_service_identity',
        Mock(side_effect=installer.InstallError('gateway-process-executable-mismatch')))
    with pytest.raises(installer.InstallError, match='native-service-identity-timeout'):
        installer._wait_upgrade_identity(object(), timeout=0)


def test_wait_running_observes_one_job_without_restarting(monkeypatch):
    pid = Mock(side_effect=[0, 0, 123])
    monkeypatch.setattr(installer.time, 'sleep', lambda _: None)
    installer._wait_running(SimpleNamespace(pid=pid))
    assert pid.call_count == 3
    assert all(call.kwargs == {'require_running': False} for call in pid.call_args_list)


def test_wait_running_tolerates_transient_spawn_not_custody_errors(monkeypatch):
    monkeypatch.setattr(installer.time, 'sleep', lambda _: None)
    pid = Mock(side_effect=[installer.InstallError('runtime-unavailable-or-busy'), 123])
    installer._wait_running(SimpleNamespace(pid=pid))
    assert pid.call_count == 2
    for code in ('gateway-process-mismatch', 'native-deployment-changed', 'host-command-failed'):
        pid = Mock(side_effect=installer.InstallError(code))
        with pytest.raises(installer.InstallError, match=code):
            installer._wait_running(SimpleNamespace(pid=pid))
        assert pid.call_count == 1


@pytest.fixture
def bundled_deployment(deployment, monkeypatch):
    root = Path(deployment['install_dir'])
    monkeypatch.setattr(installer, 'RUNTIME_CONFIG_ROOT', root / 'protected-config-fixture')
    source = plistlib.loads(Path(deployment['source_plist']).read_bytes())
    env, command = installer._env_assignments(source['ProgramArguments'])
    node, entrypoint = map(Path, command[3:5])
    (entrypoint.parent / 'package.json').write_text(json.dumps({'name': 'openclaw', 'version': '2026.6.33'}))
    plugin = root / 'plugin'
    plugin.mkdir()
    (plugin / 'index.js').write_text('plugin fixture')
    config = {'gateway': {'auth': {'token': 'private-fixture-token'}},
              'models': {'fixture': {'baseUrl': 'http://localhost:8081'}},
              'plugins': {'load': {'paths': [str(plugin)]},
                          'entries': {'pixel-ods': {'enabled': True}}}}
    config_path = root / 'openclaw.json'
    config_path.write_text(json.dumps(config))
    config_path.chmod(0o600)
    owner = installer.pwd.getpwnam(deployment['owner_name'])
    if os.getuid() == 0:
        os.chown(config_path, owner.pw_uid, owner.pw_gid)
    destination = root / 'bundle'
    digest = installer._bundle.build(node=node, runtime=entrypoint.parent, plugins=[plugin], destination=destination)
    return dict(deployment, runtime_bundle=str(destination), bundle_digest=digest)


def test_bundle_plan_maps_only_runtime_paths_preserving_credentials(bundled_deployment):
    args = bundled_deployment
    original = (Path(args['install_dir']) / 'openclaw.json').read_bytes()
    plan = installer.make_plan(**args)
    selection = plan['runtime_bundle']
    destination = installer._bundle.INSTALL_ROOT / args['bundle_digest']
    assert plan['gateway_binding']['process']['executable'] == str(destination / 'node')
    assert plan['source_process']['executable'].endswith('node with spaces')
    assert str(destination / 'runtime/openclaw.mjs') in plan['launcher'].decode()
    assert plan['access_settings']['openclaw_bin'] == str(installer.GATEWAY_LAUNCHER)
    env, _ = installer._env_assignments(plan['gateway']['ProgramArguments'])
    assert env['OPENCLAW_CONFIG_PATH'] == selection['config_path']
    assert env['OPENCLAW_WRAPPER'] == str(installer.GATEWAY_LAUNCHER)
    assert env['PATH'].split(':')[0] == str(destination)
    candidate = json.loads(selection['config_bytes'])
    previous = json.loads(original)
    assert candidate['gateway'] == previous['gateway']
    assert candidate['models'] == previous['models']
    assert candidate['plugins']['entries'] == previous['plugins']['entries']
    assert candidate['plugins']['load']['paths'] == [str(destination / 'plugins/0')]
    assert (Path(args['install_dir']) / 'openclaw.json').read_bytes() == original
    assert not Path(selection['config_path']).exists()
    for policy in plan['policies'].values():
        assert str(destination).encode() in policy


@pytest.mark.parametrize('fault', [None, 'state', 'config', 'runtime', 'recheck'])
@pytest.mark.parametrize('transport', [False, True])
def test_joint_migration_plan_preserves_state_and_cannot_use_partial_installers(bundled_deployment, monkeypatch, fault, transport):
    args = bundled_deployment
    source = Path(args['source_plist'])
    source_before = source.read_bytes()
    monkeypatch.setattr(installer._launchd, 'GATEWAY_PLIST', source)
    binding = installer._launchd.binding
    monkeypatch.setattr(installer._launchd, 'binding', lambda document, **kw: binding(document, plist=source, **kw))
    document = plistlib.loads(source_before)
    environment, _ = installer._env_assignments(document['ProgramArguments'])
    previous = Path(environment['OPENCLAW_CONFIG_PATH'])
    original = previous.read_bytes()
    candidate = previous.parent / 'migration-candidate'
    candidate.mkdir()
    config = candidate / 'openclaw.json'
    config.write_bytes(original)
    config.chmod(0o600)
    owner = installer.pwd.getpwnam(args['owner_name'])
    if os.getuid() == 0: os.chown(config, owner.pw_uid, owner.pw_gid)
    runtime = installer._bundle_plan(args['runtime_bundle'], args['bundle_digest'],
        dict(environment, OPENCLAW_CONFIG_PATH=str(config)), owner)
    selection = {'currentDigest': 'a' * 64, 'runtime': runtime,
        'preservation': {'stateDir': '/wrong' if fault == 'state' else environment['OPENCLAW_STATE_DIR']},
        'previousConfig': str(previous), 'previousConfigBytes': b'wrong' if fault == 'config' else original}
    if fault == 'runtime': selection['runtime'] = dict(runtime, config_bytes=b'wrong')
    calls = []
    def qualify(**kwargs):
        calls.append(kwargs)
        return dict(selection, changed=True) if fault == 'recheck' and len(calls) > 1 else selection
    monkeypatch.setattr(installer, 'qualify_migration_selection', qualify)
    helper = installer._native_services.helper
    monkeypatch.setattr(installer._native_services, 'helper', lambda name:
        SimpleNamespace(verified_services=lambda *a, **kw: {}) if name == 'config' else helper(name))
    def run():
        return installer.make_migration_plan(install_dir=args['install_dir'], owner_name=args['owner_name'],
            openclaw_bin=args['openclaw_bin'], gateway_port=args['gateway_port'], candidate=candidate,
            runtime_bundle=args['runtime_bundle'], bundle_digest=args['bundle_digest'], current_digest='a' * 64,
            services_bundle=candidate, services_digest='b' * 64, source_ref='c' * 40,
            native_transport={'docker': args['openclaw_bin'], 'project': 'ods', 'image': 'sha256:' + 'd' * 64,
                'user': str(owner.pw_uid) + ':20'} if transport else None)
    if fault:
        with pytest.raises(installer.InstallError, match='selection-changed'): run()
    else:
        plan = run()
        env, _ = installer._env_assignments(plan['gateway']['ProgramArguments'])
        assert env['HOME'] == environment['HOME']
        assert env['OPENCLAW_STATE_DIR'] == environment['OPENCLAW_STATE_DIR']
        revision = hashlib.sha256(plan['runtime_bundle']['config_bytes']).hexdigest()
        assert env['OPENCLAW_CONFIG_PATH'].endswith('openclaw-' + args['bundle_digest'] + '-' + revision + '.json')
        assert env['PIXEL_OPS_STATE_DIR'] == '/private/var/lib/pixel-ops-broker'
        if transport:
            assert env['PIXEL_HISTORY_TRANSPORT'] == 'docker-exec'
            assert env['PIXEL_HISTORY_DOCKER'] == env['PIXEL_PREVIEW_DOCKER'] == args['openclaw_bin']
            assert env['PIXEL_HISTORY_PROJECT'] == 'ods'
            assert env['PIXEL_HISTORY_IMAGE'] == 'sha256:' + 'd' * 64
        assert not plan['initial_install'] and plan['upgrade_kind'] == 'native-migration'
        assert plan['upgrade_qualification']['kind'] == 'native-migration'
        assert plan['native_services']['expected_digest'] == 'b' * 64
        for activate in (installer.install, installer.upgrade_install):
            with pytest.raises(installer.InstallError, match='joint-native-migration-activation-required'):
                activate(plan, ROOT)
    assert source.read_bytes() == source_before and previous.read_bytes() == original
    assert not Path(runtime['config_path']).exists()


@pytest.mark.parametrize('wrong_path', [False, True])
def test_upgrade_plugin_mapping_checks_active_bundle_not_candidate(bundled_deployment, monkeypatch, wrong_path):
    args = bundled_deployment
    root = Path(args['install_dir'])
    old = Path(args['runtime_bundle'])
    plugin = root / 'plugin'
    original = '      const workspaceRoot = api.config?.agents?.list?.find(agent => agent.id === AGENT_ID)?.workspace;'
    (plugin / 'index.js').write_text(original)
    store = root / 'store'
    store.mkdir()
    staged = store / 'staged'
    digest = installer._bundle.build(node=old / 'node', runtime=old / 'runtime',
                                     plugins=[plugin], destination=staged)
    current = store / digest
    staged.rename(current)
    monkeypatch.setattr(installer._bundle, 'INSTALL_ROOT', store)
    (plugin / 'index.js').write_text(original[:-1] + '\n        ?? api.config?.agents?.defaults?.workspace;')
    candidate = root / 'candidate'
    new_digest = installer._bundle.build(node=old / 'node', runtime=old / 'runtime',
                                         plugins=[plugin], destination=candidate)
    config_path = root / 'openclaw.json'
    config = json.loads(config_path.read_bytes())
    config['plugins']['load']['paths'] = [str(plugin if wrong_path else current / 'plugins/0')]
    config_path.write_text(json.dumps(config))
    owner = installer.pwd.getpwnam(args['owner_name'])
    def plan():
        return installer._bundle_plan(candidate, new_digest,
            {'OPENCLAW_CONFIG_PATH': str(config_path)}, owner,
            upgrade=(digest, 'workspace-root'))
    if wrong_path:
        with pytest.raises(installer.InstallError, match='active-plugin-upgrade-source-mismatch'):
            plan()
    else:
        selection = plan()
        assert json.loads(selection['config_bytes'])['plugins']['load']['paths'] == [str(store / new_digest / 'plugins/0')]
        with pytest.raises(installer.InstallError, match='bundle-plugin-content-mismatch'):
            installer._bundle_plan(candidate, new_digest, {'OPENCLAW_CONFIG_PATH': str(config_path)}, owner)


@pytest.mark.parametrize('change', ['plugin', 'count', 'installs', 'digest', 'config-mode'])
def test_bundle_plan_rejects_unqualified_mapping(bundled_deployment, change):
    args = dict(bundled_deployment)
    root = Path(args['install_dir'])
    config = json.loads((root / 'openclaw.json').read_bytes())
    if change == 'plugin': (root / 'plugin/index.js').write_text('new unapproved plugin')
    elif change == 'count': config['plugins']['load']['paths'] = []
    elif change == 'installs': config['plugins']['installs'] = {'pixel-ods': {'sourcePath': '/unknown'}}
    elif change == 'digest': args['bundle_digest'] = '0' * 64
    elif change == 'config-mode': (root / 'openclaw.json').chmod(0o644)
    (root / 'openclaw.json').write_text(json.dumps(config))
    with pytest.raises(ValueError):
        installer.make_plan(**args)


def test_bundle_options_must_be_paired(deployment):
    for values in ({'runtime_bundle': '/missing'}, {'bundle_digest': 'a' * 64}):
        with pytest.raises(installer.InstallError, match='runtime-bundle-and-digest-required'):
            installer.make_plan(**deployment, **values)


def test_unbundled_plan_cannot_install_or_touch_services(deployment, monkeypatch):
    plan = installer.make_plan(**deployment)
    assert plan['runtime_bundle'] is None
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    files, command, services = Mock(), Mock(), Mock()
    monkeypatch.setattr(installer, '_deployment_files', files)
    monkeypatch.setattr(installer, '_command', command)
    monkeypatch.setattr(installer, '_activation_services', services)
    with pytest.raises(installer.InstallError, match='protected-runtime-bundle-required'):
        installer.install(plan, ROOT)
    files.assert_not_called()
    command.assert_not_called()
    services.assert_not_called()


def test_bundle_migration_keeps_distinct_process_identities(bundled_deployment):
    plan = installer.make_plan(**bundled_deployment)
    old, gateway, _, relay = installer._activation_services(plan)
    assert old.process == plan['source_process']
    assert gateway.process == plan['gateway_binding']['process']
    assert old.process['executable'] != gateway.process['executable']
    assert relay.process == gateway.process
    assert relay.target == installer._launchd.RELAY_TARGET


def test_bundle_includes_owner_relay_and_protected_transport_files(bundled_deployment):
    plan = installer.make_plan(**bundled_deployment, access_port=19891)
    relay = plan['access_relay']
    assert relay['UserName'] == plan['owner'].pw_name != 'root'
    env, command = installer._env_assignments(relay['ProgramArguments'])
    assert env == {'PIXEL_NATIVE_ACCESS_PORT': '19891'}
    assert command == ['/usr/bin/sandbox-exec', '-f', str(installer._launchd.RELAY_PROFILE),
                       plan['gateway_binding']['process']['executable'], str(installer._launchd.RELAY_PROGRAM)]
    files = {path: (body, attributes) for path, body, attributes in installer._deployment_files(ROOT, plan)}
    for path in (installer._launchd.RELAY_PROGRAM, installer._launchd.RELAY_PLIST,
                 installer._launchd.RELAY_PROFILE, installer.ACCESS_PROGRAM_ROOT / 'access_mode_relay.mjs'):
        _, attributes = files[installer._destination(path)]
        assert attributes == dict(mode=0o644, uid=0, gid=0)


@pytest.mark.parametrize('port', [0, 65536, True, '18790', 18789])
def test_native_access_port_rejects_invalid_or_gateway_collision(deployment, port):
    with pytest.raises(installer.InstallError, match='invalid-native-access-port'):
        installer.make_plan(**deployment, access_port=port)


@pytest.mark.parametrize('proof_fails', [False, True])
@pytest.mark.parametrize('initial_install', [False, True])
def test_publication_precedes_config_staging_and_activation(bundled_deployment, migration, monkeypatch, proof_fails, initial_install):
    m = migration
    plan = installer.make_plan(**bundled_deployment, initial_install=initial_install)
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer, '_preflight_jobs', lambda: None)
    monkeypatch.setattr(installer, '_preflight_directory', lambda *a, **k: None)
    monkeypatch.setattr(installer, '_preflight_file', lambda *a, **k: None)
    monkeypatch.setattr(installer, '_check_directory', lambda *a, **k: None)
    monkeypatch.setattr(installer, '_activation_services', lambda _: m.services)
    monkeypatch.setattr(installer, '_job_disabled', lambda _: False)
    monkeypatch.setattr(installer, '_require_initial_absence', lambda *args: m.events.append('initial-absence'))
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', m.journal.parent)
    monkeypatch.setattr(installer, '_write_exact', lambda *a, **k: m.events.append('deploy-file'))
    monkeypatch.setattr(installer, '_runtime_config', lambda p, write=False:
                        m.events.append('write-config' if write else 'preflight-config'))
    def publish(source, **kwargs):
        m.events.append('publish-bundle')
        assert kwargs == {'expected_digest': plan['runtime_bundle']['digest']}
        return Path(plan['runtime_bundle']['destination'])
    monkeypatch.setattr(installer._bundle, 'publish', publish)
    monkeypatch.setattr(installer, '_activate_with_admission', lambda *args: m.events.append('activate'))
    def reprove(value):
        assert value is plan and m.events[-1] == 'activate'
        m.events.append('reprove')
        if proof_fails:
            raise installer.InstallError('native-runtime-access-reproof-required')
    monkeypatch.setattr(installer, '_reprove_installed_access', reprove)
    if proof_fails:
        with pytest.raises(installer.InstallError, match='access-reproof-required'):
            installer.install(plan, ROOT)
    else:
        installer.install(plan, ROOT)
    assert m.events.index('preflight-config') < m.events.index('publish-bundle')
    assert m.events.index('disable-gateway') < m.events.index('publish-bundle')
    assert m.events.index('publish-bundle') < m.events.index('write-config') < m.events.index('activate')
    assert m.events[-1] == 'reprove'
    if initial_install:
        assert m.events.index('initial-absence') < m.events.index('publish-bundle')
        assert 'identity-old' not in m.events
    else:
        assert 'identity-old' in m.events and 'initial-absence' not in m.events


@pytest.mark.parametrize('result', ['active', 'restored', 'initial-stopped', 'recovery-required'])
def test_admission_only_reopens_after_verified_activation_or_restoration(migration, monkeypatch, result):
    m = migration
    events = []
    hold = {'fixture': 'owned-hold'}
    monkeypatch.setattr(installer, '_acquire_migration_hold', lambda p: events.append('hold') or hold)
    monkeypatch.setattr(installer, '_finish_migration_hold', lambda p, h: events.append(('release', h)))
    def activate(*args):
        events.append('activate')
        m.journal.write_text(json.dumps({'phase': result}))
        if result != 'active':
            raise installer.InstallError('simulated-failure')
    monkeypatch.setattr(installer, '_activate', activate)
    if result == 'active':
        installer._activate_with_admission(m.plan, m.services, m.journal)
    else:
        with pytest.raises(installer.InstallError, match='simulated-failure'):
            installer._activate_with_admission(m.plan, m.services, m.journal)
    assert events[:2] == ['hold', 'activate']
    assert events[2:] == ([('release', hold)] if result in ('active', 'restored') else [])


@pytest.mark.parametrize('fails', [False, True])
def test_native_services_start_under_admission_before_gateway(migration, monkeypatch, fails):
    m = migration
    m.plan.update(initial_install=True, native_services={'approved': True})
    events = []
    monkeypatch.setattr(installer, '_acquire_migration_hold', lambda p: events.append('hold'))
    monkeypatch.setattr(installer, '_finish_migration_hold', lambda *a: events.append('release'))
    def native(plan):
        events.append('native-services')
        m.journal.write_text(json.dumps({'phase': 'staging'}))
        if fails: raise ValueError('native-service-not-ready')
    monkeypatch.setattr(installer, '_activate_initial_services', native)
    monkeypatch.setattr(installer, '_activate', lambda *a: events.append('gateway'))
    if fails:
        with pytest.raises(ValueError, match='native-service-not-ready'):
            installer._activate_with_admission(m.plan, m.services, m.journal)
        assert events == ['hold', 'native-services']
    else:
        installer._activate_with_admission(m.plan, m.services, m.journal)
        assert events == ['hold', 'native-services', 'gateway', 'release']


def test_native_service_selection_binds_exact_gateway_configuration(bundled_deployment, monkeypatch, tmp_path):
    plan = installer.make_plan(**bundled_deployment, initial_install=True)
    seen = []
    helper = installer._native_services.helper
    monkeypatch.setattr(installer._native_services, 'helper', lambda name: SimpleNamespace(
        verified_services=lambda path, **kw: seen.append((path, kw))) if name == 'config' else helper(name))
    installer.bind_initial_services(plan, bundle=tmp_path, digest='a' * 64, source_ref='b' * 40)
    assert seen[0][1] == {'expected_digest': 'a' * 64, 'expected_ref': 'b' * 40,
        'expected_config_digest': hashlib.sha256(plan['runtime_bundle']['source_config_bytes']).hexdigest()}
    assert plan['native_manager_port'] == 3002
    for mode, policy in plan['policies'].items():
        if mode == 'sandboxed':
            assert b'(subpath "/private/var/lib/pixel-ops-broker/requests")' in policy
            assert b'(subpath "/private/var/lib/pixel-ops-broker/cancel")' in policy
        assert b'(literal "/private/var/lib/ods-pixel-artifact-promoter/promoter.sock")' in policy
        deny = next(line for line in policy.splitlines() if line.startswith(b'(deny file-write* '))
        assert b'(subpath "/private/var/lib/pixel-ops-broker/approvals")' in deny
        assert b'(subpath "/private/var/lib/pixel-ops-broker/results")' in deny
        assert b'(subpath "/usr/local/libexec/ods-pixel-services")' in deny
        assert b'(subpath "/private/var/lib/pixel-ops-broker")' not in deny
    plan['initial_install'] = False
    with pytest.raises(installer.InstallError, match='require-initial-install'):
        installer.bind_initial_services(plan, bundle=tmp_path, digest='a' * 64, source_ref='b' * 40)


@pytest.mark.parametrize('stop_fails', [False, True])
def test_gateway_failure_stops_new_services_and_retains_admission(migration, monkeypatch, stop_fails):
    m = migration
    m.plan.update(initial_install=True, native_services={'approved': True})
    events = []
    monkeypatch.setattr(installer, '_acquire_migration_hold', lambda p: events.append('hold'))
    monkeypatch.setattr(installer, '_finish_migration_hold', lambda *a: events.append('release'))
    def stop():
        events.append('stop-native')
        if stop_fails: raise ValueError('uncertain-stop')
    def start(plan):
        events.append('start-native')
        return stop
    def activate(*args):
        events.append('gateway-failed')
        m.journal.write_text(json.dumps({'phase': 'initial-stopped'}))
        raise installer.InstallError('gateway-failed')
    monkeypatch.setattr(installer, '_activate_initial_services', start)
    monkeypatch.setattr(installer, '_activate', activate)
    with pytest.raises(installer.InstallError, match='stop-unconfirmed' if stop_fails else 'gateway-failed'):
        installer._activate_with_admission(m.plan, m.services, m.journal)
    assert events == ['hold', 'start-native', 'gateway-failed', 'stop-native']
    assert json.loads(m.journal.read_text())['phase'] == ('recovery-required' if stop_fails else 'initial-stopped')


@pytest.mark.parametrize('fails', [False, True])
def test_initial_service_journal_preserves_failure_and_refuses_automatic_replay(tmp_path, monkeypatch, fails):
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', tmp_path)
    selection = {'bundle': '/bundle', 'expected_digest': 'a' * 64,
        'expected_ref': 'b' * 40, 'expected_config_digest': 'c' * 64}
    plan = {'initial_install': True, 'native_services': selection,
        'owner': SimpleNamespace(pw_uid=501, pw_name='owner'), 'native_manager_port': 3002,
        'access_settings': {'install_dir': '/ods'},
        'runtime_bundle': {'source_config_bytes': json.dumps({'agents': {'list': [
            {'id': 'pixel', 'workspace': '/workspace'}]}}).encode()}}
    def install_new(**kw):
        assert kw['selection'] == selection and kw['environment'] == Path('/ods/.env')
        assert kw['workspace'] == Path('/workspace') and kw['owner'] == 'owner' and kw['port'] == 3002
        kw['checkpoint']({'phase': 'publishing-services'})
        if fails: raise ValueError('private error must not enter journal')
        kw['checkpoint']({'phase': 'services-prepared', 'recovery': {'fixture': 'approved'}})
        kw['checkpoint']({'phase': 'starting', 'attempted': ['manager']})
        kw['save_stop']('manager', {'fixture': 'process-witness'})
        kw['checkpoint']({'phase': 'services-active'})
    run = Mock(side_effect=install_new)
    monkeypatch.setattr(installer._native_services, 'install_new', run)
    if fails:
        with pytest.raises(ValueError): installer._activate_initial_services(plan)
    else:
        installer._activate_initial_services(plan)
    record = json.loads((tmp_path / 'service-installation.json').read_bytes())
    assert record['progress']['phase'] == ('publishing-services' if fails else 'services-active')
    assert record.get('requiresRecovery', False) is fails
    assert record['requiresGatewayProof'] is True and record['selection'] == selection
    if not fails:
        assert record['recovery'] == {'fixture': 'approved'}
        assert record['stopWitnesses'] == {'manager': {'fixture': 'process-witness'}}
        assert record['attempted'] == ['manager']
    assert 'private error' not in json.dumps(record)
    with pytest.raises(installer.InstallError, match='journal-requires-review'):
        installer._activate_initial_services(plan)
    assert run.call_count == 1


@pytest.mark.parametrize('fault', [None, 'missing', 'not-started', 'owner', 'selection', 'attempts', 'drift', 'stop'])
def test_new_service_recovery_uses_protected_journal_and_rechecks_before_stop(tmp_path, monkeypatch, fault):
    import pixel_access_bridge as bridge
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', tmp_path)
    journal = tmp_path / 'service-installation.json'
    if fault != 'missing': journal.touch()
    selection = {'approved': True}
    plan = {'owner': SimpleNamespace(pw_uid=501, pw_name='owner'), 'native_services': selection}
    stored = {'schemaVersion': 1, 'owner': 501, 'selection': selection, 'recovery': {'fixture': 'approved'},
        'attempted': ['manager', 'promoter'], 'stopWitnesses': {}}
    if fault == 'owner': stored['owner'] = 502
    if fault == 'selection': stored['selection'] = {'unapproved': True}
    if fault == 'attempts': stored['attempted'] = ['promoter']
    if fault == 'not-started': stored['attempted'] = []
    events = []
    def read(path, uid, limit):
        assert path == journal and uid == 0
        return json.loads(json.dumps(stored))
    def write(path, value):
        assert path == journal
        stored.clear()
        stored.update(json.loads(json.dumps(value)))
        events.append('write')
    monkeypatch.setattr(bridge, 'private_json', read)
    monkeypatch.setattr(bridge, 'atomic_json', write)
    callbacks = {}
    def adapters(record, **kw):
        assert record == {'fixture': 'approved'} and kw['owner'] == 'owner'
        callbacks.update(kw)
        if fault == 'drift': stored['unexpected'] = True
        events.append('adapters')
        return 'verified-adapters'
    def stop(**kw):
        assert kw['services'] == 'verified-adapters' and kw['attempted'] == ['manager', 'promoter']
        events.append('stop')
        callbacks['save_stop']('manager', {'fixture': 'witness'})
        assert callbacks['load_stop']('manager') == {'fixture': 'witness'}
        kw['checkpoint']({'phase': 'service-stop-failed' if fault == 'stop' else 'services-stopped'})
        if fault == 'stop': raise ValueError('stop-unconfirmed')
    monkeypatch.setattr(installer._native_services, 'recovery_adapters', adapters)
    monkeypatch.setattr(installer._native_services, 'stop_new', stop)
    if fault in ('owner', 'selection', 'attempts', 'drift', 'stop'):
        with pytest.raises((ValueError, installer.InstallError)): installer._restore_new_services(plan)
    else:
        installer._restore_new_services(plan)
    if fault in (None, 'stop'):
        assert events == ['adapters', 'stop', 'write', 'write']
        assert stored['selection'] == selection and stored['requiresRecovery'] is True
        assert stored['stopWitnesses'] == {'manager': {'fixture': 'witness'}}
    else:
        assert 'stop' not in events and 'write' not in events


@pytest.mark.parametrize('fault', [None, 'not-migration', 'pending', 'phase', 'attempts', 'health', 'drift'])
def test_rollback_retains_verified_prior_services_when_candidate_never_started(tmp_path, monkeypatch, fault):
    import pixel_access_bridge as bridge
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', tmp_path)
    (tmp_path / 'service-installation.json').touch()
    old = {'expected_digest': 'a' * 64}
    record = dict(schemaVersion=1, owner=501, selection=old, stopWitnesses={},
        attempted=['manager', 'promoter', 'operations'], progress={'phase': 'services-active'})
    plan = dict(owner=SimpleNamespace(pw_uid=501, pw_name='owner'),
        native_services={'expected_digest': 'b' * 64}, migration_qualification={'approved': True})
    if fault == 'not-migration': plan.pop('migration_qualification')
    if fault == 'pending': record['requiresRecovery'] = True
    if fault == 'phase': record['progress']['phase'] = 'starting'
    if fault == 'attempts': record['attempted'] = ['manager']
    monkeypatch.setattr(bridge, 'private_json', lambda *args: json.loads(json.dumps(record)))
    verified = []
    def verify(previous):
        assert previous['native_services'] == old
        verified.append(True)
        if fault == 'health': raise installer.InstallError('native-services-not-ready')
        if fault == 'drift': record['extra'] = True
    monkeypatch.setattr(installer, '_verify_new_services', verify)
    monkeypatch.setattr(installer._native_services, 'stop_new',
        lambda **kwargs: pytest.fail('prior services must not be stopped'))
    monkeypatch.setattr(bridge, 'atomic_json', lambda *args: pytest.fail('prior journal must not be replaced'))
    if fault:
        with pytest.raises(installer.InstallError): installer._restore_new_services(plan)
    else:
        installer._restore_new_services(plan)
        assert verified == [True]


def test_unqualified_existing_native_services_fail_before_gateway_mutation(tmp_path, monkeypatch):
    from contextlib import nullcontext
    import pixel_access_bridge as bridge
    import pixel_macos_custody as custody
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', tmp_path)
    (tmp_path / 'service-installation.json').touch()
    settings = dict(state_dir=str(tmp_path), install_dir='/owner/ods', gateway_target='system/fixture',
        gateway_plist='/fixture.plist', gateway_process='fixture', gateway_binding={},
        openclaw_bin='/fixture/node', owner='owner', gateway_policy={})
    monkeypatch.setattr(custody, 'protected_bytes', lambda *args, **kwargs: json.dumps(settings).encode())
    monkeypatch.setattr(bridge, 'LaunchdAccessBridge', lambda *args, **kwargs:
        SimpleNamespace(state=tmp_path, locked=lambda: nullcontext()))
    monkeypatch.setattr(installer, '_upgrade_file_snapshots',
        lambda *args: ([], []))
    def unqualified(plan):
        raise installer.InstallError('native-managed-service-not-ready')
    monkeypatch.setattr(installer, '_managed_service_snapshots', unqualified)
    monkeypatch.setattr(installer, '_upgrade_services',
        lambda *args: pytest.fail('unqualified update must fail before staging or stopping'))
    plan = dict(migration_qualification={'approved': True}, upgrade_qualification={'approved': True},
        runtime_bundle={'approved': True}, key=b'fixture-key', owner=SimpleNamespace(pw_name='owner'))
    with pytest.raises(installer.InstallError, match='native-managed-service-not-ready'):
        installer._execute_upgrade_install(plan, '/fixture')


@pytest.mark.parametrize('fault', [None, 'phase', 'selection', 'identity', 'readiness', 'drift'])
def test_candidate_health_includes_all_native_services_and_stable_journal(monkeypatch, fault):
    import pixel_access_bridge as bridge
    selection = {'approved': True}
    plan = {'owner': SimpleNamespace(pw_uid=501, pw_name='owner'), 'native_services': selection}
    record = {'owner': 501, 'selection': {'wrong': True} if fault == 'selection' else selection,
        'progress': {'phase': 'services-stopped' if fault == 'phase' else 'services-active'},
        'recovery': {'identity': {'fixture': 'identity'}, 'python': '/protected/python'}}
    reads, events = [], []
    def read(*args):
        reads.append(args)
        return dict(record, changed=True) if fault == 'drift' and len(reads) > 1 else record
    monkeypatch.setattr(bridge, 'private_json', read)
    def identity(name):
        events.append(('identity', name))
        if fault == 'identity': raise ValueError('wrong-process')
    services = {name: SimpleNamespace(process_identity=lambda name=name: identity(name))
        for name in ('manager', 'promoter', 'operations')}
    def ready(name):
        events.append(('ready', name))
        return fault != 'readiness'
    monkeypatch.setattr(installer._native_services, 'recovery_adapters', lambda *a, **kw: services)
    monkeypatch.setattr(installer._native_services, 'readiness_checks', lambda **kw:
        {name: lambda name=name: ready(name) for name in services})
    if fault:
        with pytest.raises((ValueError, installer.InstallError)): installer._verify_new_services(plan)
    else:
        installer._verify_new_services(plan)
        assert events == [(action, name) for name in services for action in ('identity', 'ready')]
        assert len(reads) == 2
    if fault in ('phase', 'selection'): assert not events


def test_unconfirmed_admission_never_stops_gateway(migration, monkeypatch):
    monkeypatch.setattr(installer, '_acquire_migration_hold',
                        Mock(side_effect=installer.InstallError('edge-not-idle')))
    activate = Mock()
    monkeypatch.setattr(installer, '_activate', activate)
    with pytest.raises(installer.InstallError, match='edge-not-idle'):
        installer._activate_with_admission(migration.plan, migration.services, migration.journal)
    activate.assert_not_called()
    assert migration.old.loaded


def test_lost_migration_hold_reply_retains_binding_without_activation(migration, monkeypatch):
    m = migration
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', m.journal.parent)
    monkeypatch.setattr(installer, '_migration_edge_context', lambda _: {})
    monkeypatch.setattr(installer.subprocess, 'run', Mock(return_value=SimpleNamespace(stdout='a' * 64 + ' true pixel-edge')))
    before = dict(capability='available', phase='idle', revision='b' * 64,
                  streams=0, admission_blocked=False)
    request = Mock(side_effect=[before, TimeoutError('reply lost')])
    monkeypatch.setattr(installer, '_migration_edge_request', request)
    activate, release = Mock(), Mock()
    monkeypatch.setattr(installer, '_activate', activate)
    monkeypatch.setattr(installer, '_finish_migration_hold', release)
    with pytest.raises(TimeoutError, match='reply lost'):
        installer._activate_with_admission(m.plan, m.services, m.journal)
    path = m.journal.parent / 'installation-edge.json'
    record = json.loads(path.read_bytes())
    assert record['phase'] == 'acquiring'
    assert record['binding'] == request.call_args.args[3]
    assert record['container'] == 'a' * 64
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    activate.assert_not_called()
    release.assert_not_called()
    # Even if the next status is idle, a previous ambiguous attempt is not
    # permission to overwrite its token and acquire again.
    request.side_effect = None
    request.return_value = before
    with pytest.raises(installer.InstallError, match='edge-journal-requires-review'):
        installer._acquire_migration_hold(m.plan)
    assert json.loads(path.read_bytes()) == record
    assert request.call_count == 3
    assert m.old.loaded


@pytest.mark.parametrize('upgrade', [False, True])
def test_migration_hold_journals_before_acquire_and_preserves_exact_binding(migration, monkeypatch, upgrade):
    import pixel_access_bridge as bridge
    real_read = bridge.private_json
    monkeypatch.setattr(bridge, 'private_json', lambda path, uid, maximum:
                        real_read(path, os.getuid(), maximum))
    m = migration
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', m.journal.parent)
    monkeypatch.setattr(installer, '_migration_edge_context', lambda _: {})
    monkeypatch.setattr(installer.subprocess, 'run', Mock(return_value=SimpleNamespace(stdout='a' * 64 + ' true pixel-edge')))
    path = m.journal.parent / 'installation-edge.json'
    if upgrade:
        path.write_text('original installation record')
        m.plan['upgrade_qualification'] = {'candidateDigest': 'd' * 64}
        m.plan['runtime_bundle'] = {'digest': 'd' * 64}
        path = m.journal.parent / ('runtime-upgrade-edge-' + 'd' * 64 + '.json')
    before = dict(capability='available', phase='idle', revision='b' * 64, streams=0,
                  admission_blocked=False, host_runtime_verified=False)
    calls = []
    def request(plan, container, operation=None, binding=None):
        assert container == 'a' * 64
        calls.append((operation, binding))
        if operation is None:
            return before
        persisted = json.loads(path.read_bytes())
        assert persisted['binding'] == binding
        assert persisted['container'] == container
        if operation == 'acquire':
            assert persisted['phase'] in ('acquiring', 'held')
            return dict(before, phase='held', admission_blocked=True)
        assert operation == 'release'
        return dict(before, revision='c' * 64)
    monkeypatch.setattr(installer, '_migration_edge_request', request)
    hold = installer._acquire_migration_hold(m.plan)
    assert hold['phase'] == 'held'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    installer._finish_migration_hold(m.plan, hold)
    assert json.loads(path.read_bytes())['phase'] == 'released'
    assert [call[0] for call in calls] == [None, 'acquire', 'acquire', 'release']
    assert all(call[1] == hold['binding'] for call in calls[1:])
    if upgrade:
        assert (m.journal.parent / 'installation-edge.json').read_text() == 'original installation record'


@pytest.mark.parametrize('digest,bundle', [('../bad', '../bad'), ('a' * 64, 'b' * 64), (None, None)])
def test_upgrade_hold_rejects_invalid_selection_before_contacting_edge(monkeypatch, digest, bundle):
    context = Mock()
    monkeypatch.setattr(installer, '_migration_edge_context', context)
    with pytest.raises(installer.InstallError, match='runtime-upgrade-plan-required'):
        installer._acquire_migration_hold({'upgrade_qualification': {'candidateDigest': digest},
                                         'runtime_bundle': {'digest': bundle}})
    context.assert_not_called()


@pytest.mark.parametrize('changed', [dict(phase='busy', streams=1), dict(phase='held'),
                                   dict(capability='unavailable'), dict(admission_blocked=True)])
def test_migration_rejects_busy_or_unavailable_edge_before_writing(migration, monkeypatch, changed):
    m = migration
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', m.journal.parent)
    monkeypatch.setattr(installer, '_migration_edge_context', lambda _: {})
    monkeypatch.setattr(installer.subprocess, 'run', Mock(return_value=SimpleNamespace(stdout='a' * 64 + ' true pixel-edge')))
    before = dict(capability='available', phase='idle', revision='b' * 64, streams=0, admission_blocked=False)
    request = Mock(return_value={**before, **changed})
    monkeypatch.setattr(installer, '_migration_edge_request', request)
    with pytest.raises(installer.InstallError, match='edge-not-idle'):
        installer._acquire_migration_hold(m.plan)
    assert request.call_count == 1
    assert not (m.journal.parent / 'installation-edge.json').exists()


@pytest.mark.parametrize('phase', ['acquiring', 'held'])
def test_resume_edge_hold_reuses_persisted_identity(migration, monkeypatch, phase):
    import pixel_access_bridge as bridge
    m = migration
    record = dict(schemaVersion=1, container='a' * 64,
                  binding=dict(token='b' * 64, revision='c' * 64), phase=phase)
    held = dict(capability='available', phase='held', streams=0,
                admission_blocked=True, revision='c' * 64)
    if phase == 'held':
        record['status'] = held
    read, write = Mock(return_value=record), Mock()
    monkeypatch.setattr(bridge, 'private_json', read)
    monkeypatch.setattr(bridge, 'atomic_json', write)
    monkeypatch.setattr(installer, '_migration_edge_context', lambda _: {})
    inspect = Mock(return_value=SimpleNamespace(stdout='a' * 64 + ' true pixel-edge'))
    monkeypatch.setattr(installer.subprocess, 'run', inspect)
    request = Mock(return_value=held)
    monkeypatch.setattr(installer, '_migration_edge_request', request)
    result = installer._resume_migration_hold(m.plan)
    assert inspect.call_args.args[0][2] == record['container']
    request.assert_called_once_with(m.plan, record['container'], 'acquire', record['binding'])
    assert result == dict(record, phase='held', status=held)
    assert read.call_count == 3
    write.assert_called_once()


@pytest.mark.parametrize('fault', ['released', 'token', 'container', 'drift', 'timeout', 'response'])
def test_resume_edge_hold_never_replaces_uncertain_binding(migration, monkeypatch, fault):
    import pixel_access_bridge as bridge
    record = dict(schemaVersion=1, container='a' * 64,
                  binding=dict(token='b' * 64, revision='c' * 64), phase='acquiring')
    if fault == 'released':
        record['phase'] = 'released'
    if fault == 'token':
        record['binding']['token'] = 'invalid'
    read, write = Mock(return_value=record), Mock()
    if fault == 'drift':
        read.side_effect = [record, dict(record, phase='released')]
    monkeypatch.setattr(bridge, 'private_json', read)
    monkeypatch.setattr(bridge, 'atomic_json', write)
    monkeypatch.setattr(installer, '_migration_edge_context', lambda _: {})
    monkeypatch.setattr(installer.subprocess, 'run', Mock(return_value=SimpleNamespace(
        stdout=('d' if fault == 'container' else 'a') * 64 + ' true pixel-edge')))
    request = Mock(return_value=dict(capability='available', phase='idle', streams=0,
                                    admission_blocked=False, revision='c' * 64))
    if fault == 'timeout':
        request.side_effect = TimeoutError('reply lost')
    monkeypatch.setattr(installer, '_migration_edge_request', request)
    with pytest.raises((installer.InstallError, TimeoutError)):
        installer._resume_migration_hold(migration.plan)
    write.assert_not_called()
    if fault not in ('timeout', 'response'):
        request.assert_not_called()


@pytest.mark.parametrize('failure', [None, 'snapshot', 'runtime', 'hold', 'restore', 'ready', 'finish'])
def test_recovery_adapter_reopens_only_after_verified_restoration(monkeypatch, failure):
    monkeypatch.setattr(installer, '_clear_candidate_stop_witnesses', Mock())
    import pixel_macos_custody as custody
    monkeypatch.setattr(installer, '_recover_unstarted_upgrade', lambda *a, **kw: False)
    events = []
    previous = {role: SimpleNamespace(target=role) for role in ('gateway', 'access', 'relay')}
    candidate = {role: SimpleNamespace(target=role) for role in previous}
    plan = {'access_settings': {'gateway_port': 18789}}
    records = [dict(path='/fixture', before=b'old', after=b'new', mode=0o644, gid=0)]
    live = {'value': b'new'}
    monkeypatch.setattr(custody, 'protected_bytes', lambda *a, **k: live['value'])
    monkeypatch.setattr(installer, '_upgrade_services', lambda *a: (previous, candidate))
    def checkpoint(name):
        events.append(name)
        if name == failure:
            raise RuntimeError(name)
    monkeypatch.setattr(installer, '_verify_recovery_runtime', lambda plan: checkpoint('runtime'))
    monkeypatch.setattr(installer, '_resume_migration_hold', lambda p: checkpoint('hold') or 'original')
    monkeypatch.setattr(installer, '_finish_migration_hold', lambda p, h: events.append(('release', h)))
    monkeypatch.setattr(installer, '_upgrade_service_identity', lambda s: None)
    monkeypatch.setattr(installer, '_upgrade_policy_mode', lambda p: 'full-access')
    monkeypatch.setattr(installer, '_ready_gateway', lambda *a: checkpoint('ready'))
    monkeypatch.setattr(installer, '_ready_access', lambda *a, **k: events.append('access'))
    monkeypatch.setattr(installer, '_ready_access_relay', lambda *a, **k: events.append('relay'))
    def recover(**kwargs):
        assert kwargs['observe'] is installer._observe_upgrade_service
        assert kwargs['assert_absent'] is installer._assert_upgrade_absent
        assert kwargs['start'] is installer._start_upgrade_service
        assert kwargs['stop'] is installer._stop_upgrade_service
        kwargs['verify_snapshots']()
        checkpoint('restore')
        live['value'] = b'old'
        kwargs['ready'](previous)
    monkeypatch.setattr(installer._upgrade, 'recover_previous', recover)
    def finish(verify):
        verify()
        checkpoint('finish')
    journal = SimpleNamespace(phase=Mock(), finish=finish)
    def run():
        installer._recover_upgrade(plan, records, journal,
            verify_snapshots=lambda: checkpoint('snapshot'))
    if failure:
        with pytest.raises(RuntimeError, match=failure):
            run()
        assert ('release', 'original') not in events
        if failure == 'runtime':
            assert 'hold' not in events and 'restore' not in events
    else:
        run()
        assert events[-4:] == ['finish', 'access', 'relay', ('release', 'original')]


@pytest.mark.parametrize('version', ['previous', 'candidate', 'absent'])
@pytest.mark.parametrize('failure', [None, 'stop', 'replace', 'start', 'ready'])
def test_recovery_adapter_runs_real_sequencer_and_cas(monkeypatch, version, failure):
    monkeypatch.setattr(installer, '_relocate_upgrade_receipt', Mock())
    monkeypatch.setattr(installer, '_clear_candidate_stop_witnesses', Mock())
    import pixel_macos_custody as custody
    monkeypatch.setattr(installer, '_recover_unstarted_upgrade', lambda *a, **kw: False)
    monkeypatch.setattr(installer, '_verify_recovery_runtime', lambda plan: None)
    events, live = [], {'/a': b'old-a', '/b': b'new-b'}
    def event(name):
        events.append(name)
        if name == failure:
            raise RuntimeError(name)
    def services():
        return {role: SimpleNamespace(target=role, assert_stopped=lambda: event('stopped'))
                for role in ('gateway', 'access', 'relay')}
    previous, candidate = services(), services()
    monkeypatch.setattr(installer, '_upgrade_services', lambda *a: (previous, candidate))
    monkeypatch.setattr(installer, '_observe_upgrade_service', lambda *a: version)
    monkeypatch.setattr(installer, '_assert_upgrade_absent', lambda *a: event('stop'))
    monkeypatch.setattr(installer, '_stop_upgrade_service', lambda s: event('stop'))
    monkeypatch.setattr(installer, '_start_upgrade_service', lambda s: event('start'))
    monkeypatch.setattr(installer, '_upgrade_service_identity', lambda s: None)
    monkeypatch.setattr(installer, '_upgrade_policy_mode', lambda p: 'full-access')
    monkeypatch.setattr(installer, '_resume_migration_hold', lambda p: event('hold') or 'original')
    monkeypatch.setattr(installer, '_finish_migration_hold', lambda *a: event('release'))
    monkeypatch.setattr(installer, '_ready_gateway', lambda *a: event('ready'))
    monkeypatch.setattr(installer, '_ready_access', lambda *a, **k: None)
    monkeypatch.setattr(installer, '_ready_access_relay', lambda *a, **k: None)
    monkeypatch.setattr(custody, 'protected_bytes', lambda path, **k: live[path])
    def replace(path, *, expected, replacement, mode, gid):
        event('replace')
        assert live[path] == expected and mode == 0o644 and gid == 0
        live[path] = replacement
    monkeypatch.setattr(custody, 'replace_protected_bytes', replace)
    records = [dict(path='/' + name, before=('old-' + name).encode(),
                    after=('new-' + name).encode(), mode=0o644, gid=0) for name in ('a', 'b')]
    def finish(verify):
        verify()
        event('finish')
    journal = SimpleNamespace(phase=lambda phase: event('phase-' + phase), finish=finish)
    def run():
        installer._recover_upgrade({'access_settings': {'gateway_port': 18789}},
            records, journal, verify_snapshots=lambda: event('verify'))
    if failure:
        with pytest.raises(installer._upgrade.UpgradeError, match='recovery-required'):
            run()
        assert 'release' not in events and 'finish' not in events
        assert events[-1] == 'phase-recovery-required'
    else:
        run()
        assert live == {'/a': b'old-a', '/b': b'old-b'}
        assert events.count('replace') == 1
        assert events.count('stop') == events.count('start') == 3
        assert events.index('replace') > max(i for i, e in enumerate(events) if e == 'stop')
        assert events.index('replace') < events.index('start')
        assert events[-2:] == ['finish', 'release']


@pytest.mark.parametrize('fault', [None, 'owner', 'selection', 'encoding', 'drift'])
def test_recovery_context_roundtrip_preserves_bytes_and_approved_identity(monkeypatch, fault):
    import pixel_access_bridge as bridge
    owner = SimpleNamespace(pw_name='owner', pw_uid=501, pw_gid=20, pw_dir='/Users/owner')
    plan = {field: {} for field in installer.RECOVERY_PLAN_FIELDS}
    plan.update(owner=owner, source_bytes=b'original plist\x00', key=b'private-key',
                upgrade_kind='workspace-root', access_port=18790,
                upgrade_qualification={'currentDigest': 'a' * 64, 'candidateDigest': 'b' * 64},
                runtime_bundle={'digest': 'b' * 64, 'source_config_bytes': b'old config',
                                'config_bytes': b'new config'})
    saved = installer._recovery_context(plan)
    assert json.loads(json.dumps(saved)) == saved
    if fault == 'owner':
        saved['owner']['uid'] = 502
    if fault == 'selection':
        saved['upgrade_qualification']['candidateDigest'] = 'c' * 64
    if fault == 'encoding':
        saved['key'] = '!!!'
    reader = Mock(return_value=saved)
    if fault == 'drift':
        reader.side_effect = [saved, {}]
    monkeypatch.setattr(bridge, 'private_json', reader)
    monkeypatch.setattr(installer.pwd, 'getpwnam', lambda name: owner)
    def load():
        return installer._load_recovery_context(Path('/private/context'),
            current_digest='a' * 64, candidate_digest='b' * 64, owner_name='owner')
    if fault:
        with pytest.raises(installer.InstallError):
            load()
    else:
        assert load() == plan


@pytest.mark.parametrize('fault', [None, 'source', 'owner', 'settings', 'binding', 'process', 'baseline'])
def test_recovery_context_must_match_journaled_definitions(bundled_deployment, monkeypatch, fault):
    plan = installer.make_plan(**bundled_deployment)
    owner = plan['owner']
    source = plistlib.loads(plan['source_bytes'])
    source['UserName'] = owner.pw_name
    plan['source_bytes'] = plistlib.dumps(source)
    old_binding = installer._launchd.binding(plistlib.loads(plan['source_bytes']),
        owner=owner.pw_name, executable=plan['source_process']['executable'],
        uid=owner.pw_uid, gid=owner.pw_gid)
    before = dict(plan['access_settings'], gateway_binding=old_binding,
                  gateway_process=plan['source_process'])
    after = dict(plan['access_settings'])
    if fault == 'owner': before['owner'] = 'another-owner'
    if fault == 'settings': after['gateway_port'] += 1
    if fault == 'binding': before['gateway_binding'] = {}
    if fault == 'process': before['gateway_process'] = {}
    records = [dict(path=str(installer._destination(installer._launchd.GATEWAY_PLIST)),
                    before=b'wrong' if fault == 'source' else plan['source_bytes'],
                    after=installer._launchd.encode(plan['gateway'])),
               dict(path=str(installer._destination(installer.ACCESS_FILES['config'])),
                    before=json.dumps(before).encode(), after=json.dumps(after).encode())]
    if fault == 'baseline':
        monkeypatch.setattr(installer, '_upgrade_service_baseline', lambda *a: b'expected')
        records.append(dict(path=str(installer._launchd.ACCESS_STATE / 'service-baseline.json'),
                            before=b'old', after=b'wrong'))
    if fault:
        with pytest.raises(installer.InstallError, match='recovery-binding-mismatch'):
            installer._verify_recovery_bindings(plan, records)
    else:
        installer._verify_recovery_bindings(plan, records)


@pytest.mark.parametrize('fault', [None, 'platform', 'state', 'load', 'binding', 'drift', 'authority'])
def test_recover_install_keeps_controller_lock_through_restoration(monkeypatch, fault):
    monkeypatch.setattr(installer.os.path, 'lexists', lambda path: True)
    monkeypatch.setattr(installer, '_previous_upgrade_guard', lambda records: True)
    from contextlib import contextmanager
    import pixel_access_bridge as bridge_module
    import pixel_macos_custody as custody
    adapter = object.__new__(bridge_module.LaunchdAccessBridge)
    adapter.state = Path('/wrong') if fault == 'state' else installer._launchd.ACCESS_STATE
    monkeypatch.setattr(installer.sys, 'platform', 'linux' if fault == 'platform' else 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    held, events = [], []
    @contextmanager
    def locked(self):
        held.append(True)
        try: yield
        finally: held.pop()
    monkeypatch.setattr(bridge_module.LaunchdAccessBridge, 'recovery_locked', locked)
    plan, records = {}, [dict(path='/fixture', before=b'old', after=b'new')]
    journal = SimpleNamespace(value={'phase': 'prepared'})
    def load(**kwargs):
        assert held == [True]
        events.append('load')
        if fault == 'load': raise installer.InstallError('load')
        fresh = {'changed': True} if fault == 'authority' and events.count('load') > 1 else plan
        return fresh, journal, records
    monkeypatch.setattr(installer, '_load_upgrade_recovery', load)
    def binding(*args):
        assert held == [True]
        if fault == 'binding': raise installer.InstallError('binding')
    monkeypatch.setattr(installer, '_verify_recovery_bindings', binding)
    monkeypatch.setattr(custody, 'protected_bytes', lambda *a, **kw: b'unknown' if fault == 'drift' else b'old')
    def recover(p, r, j, *, verify_snapshots, previous_guard):
        assert previous_guard is True
        assert held == [True] and p == plan and r == records and j is journal
        events.append('recover')
        verify_snapshots()
    monkeypatch.setattr(installer, '_recover_upgrade', recover)
    def run():
        installer.recover_install(adapter, current_digest='a' * 64,
            candidate_digest='b' * 64, owner_name='owner')
    if fault:
        with pytest.raises(installer.InstallError): run()
        assert 'recover' not in events
    else:
        run()
        assert events == ['load', 'load', 'recover', 'load']
    assert held == []


@pytest.mark.parametrize('field', [None, 'state_dir', 'gateway_target', 'gateway_plist', 'owner', 'openclaw_bin'])
def test_recovery_bridge_constructs_without_running_services(bundled_deployment, monkeypatch, field):
    plan = installer.make_plan(**bundled_deployment)
    if field:
        plan['access_settings'][field] = 'unexpected'
    monkeypatch.setattr(installer, '_load_recovery_context', lambda *a, **kw: plan)
    command = Mock(side_effect=AssertionError('recovery bridge must not execute a service'))
    monkeypatch.setattr(installer.subprocess, 'run', command)
    def run():
        return installer._recovery_bridge(current_digest='a' * 64,
            candidate_digest='b' * 64, owner_name=plan['owner'].pw_name)
    if field:
        with pytest.raises(installer.InstallError, match='controller-binding-changed'): run()
    else:
        bridge = run()
        assert bridge.state == installer._launchd.ACCESS_STATE
        assert bridge.gateway_owner == plan['owner'].pw_name
    command.assert_not_called()


@pytest.mark.parametrize('fault', [None, 'platform', 'root', 'bridge', 'restore', 'proof'])
def test_recovery_cli_dispatches_without_install_source_and_redacts_errors(monkeypatch, capsys, fault):
    monkeypatch.setattr(installer.sys, 'platform', 'linux' if fault == 'platform' else 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 501 if fault == 'root' else 0)
    bridge = object()
    build = Mock(return_value=bridge,
        side_effect=ValueError('private-credential-in-error') if fault == 'bridge' else None)
    recover = Mock(return_value='restored', side_effect=installer._upgrade.UpgradeError('private-credential-in-error') if fault == 'restore' else None)
    monkeypatch.setattr(installer, '_recovery_bridge', build)
    monkeypatch.setattr(installer, 'recover_install', recover)
    def reprove(owner):
        recover.assert_called_once()
        assert owner == 'owner'
        if fault == 'proof':
            raise installer.InstallError('native-runtime-access-reproof-required')
    proof = Mock(side_effect=reprove)
    monkeypatch.setattr(installer, '_reprove_recovered_access', proof)
    result = installer.main(['recover-runtime', '--owner', 'owner',
        '--current-bundle-digest', 'a' * 64, '--bundle-digest', 'b' * 64])
    output = capsys.readouterr()
    assert 'private-credential' not in output.out + output.err
    if fault:
        assert result == 1 and not output.out
        if fault in ('platform', 'root'):
            build.assert_not_called()
        if fault not in ('restore', 'proof'): recover.assert_not_called()
        if fault != 'proof': proof.assert_not_called()
    else:
        assert result == 0
        assert json.loads(output.out)['status'] == 'restored'
        proof.assert_called_once_with('owner')
        recover.assert_called_once_with(bridge, current_digest='a' * 64,
            candidate_digest='b' * 64, owner_name='owner')


@pytest.mark.parametrize('arguments', [[], ['--owner', 'owner'],
    ['--owner', 'owner', '--current-bundle-digest', 'a' * 64, '--bundle-digest', 'b' * 64, '--install']])
def test_recovery_cli_rejects_incomplete_or_install_arguments(monkeypatch, arguments):
    build = Mock()
    monkeypatch.setattr(installer, '_recovery_bridge', build)
    with pytest.raises(SystemExit) as error:
        installer.main(['recover-runtime', *arguments])
    assert error.value.code == 2
    build.assert_not_called()


@pytest.mark.parametrize('fault', [None, 'platform', 'root', 'read', 'archive'])
def test_retirement_cli_dispatch_and_redaction(monkeypatch, capsys, fault):
    monkeypatch.setattr(installer.sys, 'platform', 'linux' if fault == 'platform' else 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 501 if fault == 'root' else 0)
    snapshots = {'runtime-upgrade-context-' + 'b' * 64 + '.json': b'{}'}
    read = Mock(return_value=snapshots, side_effect=ValueError('secret-value') if fault == 'read' else None)
    archive = Mock(return_value='history.json', side_effect=ValueError('secret-value') if fault == 'archive' else None)
    monkeypatch.setattr(installer, '_retirement_snapshots', read)
    monkeypatch.setattr(installer, '_decode_recovery_context', Mock(return_value={}))
    bridge = object()
    monkeypatch.setattr(installer, '_bridge_from_recovery_plan', Mock(return_value=bridge))
    monkeypatch.setattr(installer, '_archive_verified_retirement', archive)
    result = installer.main(['retire-runtime', '--owner', 'owner', '--current-bundle-digest',
        'a' * 64, '--bundle-digest', 'b' * 64, '--history-digest', 'c' * 64])
    output = capsys.readouterr()
    assert 'secret-value' not in output.out + output.err
    if fault:
        assert result == 1 and not output.out
        if fault in ('platform', 'root'):
            read.assert_not_called()
        if fault != 'archive':
            archive.assert_not_called()
    else:
        assert result == 0 and json.loads(output.out)['status'] == 'archived'
        archive.assert_called_once_with(bridge, snapshots, current_digest='a' * 64,
            candidate_digest='b' * 64, owner_name='owner')
        read.assert_called_once_with(current_digest='a' * 64,
            candidate_digest='b' * 64, history_digest='c' * 64)


@pytest.mark.parametrize('history', ['../escape', '', 'A' * 64])
def test_retirement_snapshot_rejects_invalid_history_before_read(monkeypatch, history):
    import pixel_macos_custody as custody
    read = Mock()
    monkeypatch.setattr(custody, 'protected_bytes', read)
    with pytest.raises(installer.InstallError, match='history-selection-invalid'):
        installer._retirement_snapshots(current_digest='a' * 64,
            candidate_digest='b' * 64, history_digest=history)
    read.assert_not_called()


@pytest.mark.parametrize('mode', [0o600, 0o644])
def test_retirement_snapshot_reads_only_selected_attempt(monkeypatch, tmp_path, mode):
    import pixel_macos_custody as custody
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', tmp_path)
    candidate = 'b' * 64
    names = ['runtime-upgrade-' + candidate + '.completed.json',
             'runtime-upgrade-context-' + candidate + '.json']
    for name in names:
        path = tmp_path / name
        path.write_bytes(b'{}')
        path.chmod(mode)
    (tmp_path / 'unrelated.json').write_bytes(b'private')
    read = Mock(side_effect=lambda path, **kw: path.read_bytes())
    monkeypatch.setattr(custody, 'protected_bytes', read)
    if mode != 0o600:
        with pytest.raises(installer.InstallError, match='history-mode-invalid'):
            installer._retirement_snapshots(current_digest='a' * 64, candidate_digest=candidate)
    else:
        assert installer._retirement_snapshots(current_digest='a' * 64,
            candidate_digest=candidate) == dict.fromkeys(names, b'{}')
        assert {call.args[0].name for call in read.call_args_list} == set(names)


@pytest.mark.parametrize('fault', [None, 'held', 'phase', 'file', 'service', 'snapshot', 'ready', 'real-journal'])
def test_unstarted_recovery_requires_intact_live_previous_deployment(monkeypatch, tmp_path, fault):
    monkeypatch.setattr(installer, '_clear_candidate_stop_witnesses', Mock())
    import pixel_macos_custody as custody
    previous = {role: object() for role in ('gateway', 'access', 'relay')}
    candidate = {role: object() for role in previous}
    events = []
    hold = tmp_path / 'hold'
    if fault == 'held': hold.write_text('{}')
    monkeypatch.setattr(installer, '_edge_hold_journal', lambda p: hold)
    monkeypatch.setattr(installer, '_verify_recovery_runtime', lambda p: None)
    monkeypatch.setattr(custody, 'protected_bytes', lambda *a, **kw: b'new' if fault == 'file' else b'old')
    monkeypatch.setattr(installer, '_observe_upgrade_service', lambda *a: 'candidate' if fault == 'service' else 'previous')
    monkeypatch.setattr(installer, '_upgrade_policy_mode', lambda p: 'full-access')
    def ready(*args, **kwargs):
        if fault == 'ready': raise installer.InstallError('not-ready')
    monkeypatch.setattr(installer, '_ready_gateway', ready)
    monkeypatch.setattr(installer, '_ready_access', ready)
    monkeypatch.setattr(installer, '_ready_access_relay', ready)
    def verify():
        events.append('verify')
        if fault == 'snapshot': raise installer.InstallError('snapshot')
    def finish(check):
        check()
        events.append('finish')
    journal = SimpleNamespace(value={'phase': 'starting-gateway' if fault == 'phase' else 'prepared'},
        phase=lambda phase: events.append(phase), finish=finish)
    records = [dict(path='/fixture', before=b'old', after=b'new', mode=0o644, gid=0)]
    if fault == 'real-journal':
        import pixel_access_bridge as bridge
        real_read = bridge.private_json
        monkeypatch.setattr(bridge, 'private_json', lambda path, uid, maximum:
                            real_read(path, os.getuid(), maximum))
        value = installer._upgrade.encode_recovery(records, current_digest='a' * 64,
            candidate_digest='b' * 64, allowed_paths={'/fixture'})
        journal = installer._upgrade.RecoveryJournal.create(tmp_path / 'runtime-upgrade.json', value)
    def run():
        return installer._recover_unstarted_upgrade({'access_settings': {'gateway_port': 18789}},
            records, journal, previous, candidate,
            verify_snapshots=verify, previous_guard=True)
    if fault == 'held':
        assert run() is False and not events
    elif fault == 'real-journal':
        assert run() is True
        assert not journal.path.exists()
        archive = tmp_path / ('runtime-upgrade-' + 'b' * 64 + '.completed.json')
        assert json.loads(archive.read_text())['phase'] == 'restored'
        assert events == ['verify', 'verify']
    elif fault:
        with pytest.raises(installer.InstallError): run()
        assert 'finish' not in events and 'restored' not in events
    else:
        assert run() is True
        assert events == ['verify', 'restored', 'verify', 'finish']


@pytest.mark.parametrize('kind', ['legacy', 'guarded', 'unknown', 'partial', 'syntax', 'missing'])
def test_previous_guard_is_derived_from_archived_code_without_execution(kind):
    path = str(installer._destination(installer.ACCESS_PROGRAM_ROOT / 'pixel_access_bridge.py'))
    if kind == 'guarded':
        body = (ROOT / 'bin/pixel_access_bridge.py').read_bytes()
    elif kind == 'legacy':
        body = b'class LaunchdAccessBridge(SystemdAccessBridge):\n    pass\n'
    elif kind == 'partial':
        body = b'class LaunchdAccessBridge(SystemdAccessBridge):\n    def status(self): return {}\n'
    elif kind == 'unknown':
        body = b'class LaunchdAccessBridge(SystemdAccessBridge):\n    def status(self): return {}\n    def locked(self): return None\n'
    else:
        body = b'invalid python !'
    records = [] if kind == 'missing' else [dict(path=path, before=body)]
    if kind in ('legacy', 'guarded'):
        assert installer._previous_upgrade_guard(records) is (kind == 'guarded')
    else:
        with pytest.raises(installer.InstallError, match='previous-guard-unqualified'):
            installer._previous_upgrade_guard(records)


@pytest.mark.parametrize('fault', [None, 'reply-lost', 'drift', 'rejected'])
def test_release_retry_preserves_token_and_never_reacquires(tmp_path, monkeypatch, fault):
    import pixel_access_bridge as bridge
    real_read = bridge.private_json
    monkeypatch.setattr(bridge, 'private_json', lambda path, uid, maximum:
                        real_read(path, os.getuid(), maximum))
    path = tmp_path / 'edge.json'
    monkeypatch.setattr(installer, '_edge_hold_journal', lambda p: path)
    record = dict(schemaVersion=1, container='a' * 64, phase='held',
                  binding=dict(token='b' * 64, revision='c' * 64),
                  status=dict(capability='available', phase='held', streams=0, admission_blocked=True))
    bridge.atomic_json(path, record)
    calls = []
    def request(plan, container, operation, binding):
        calls.append(operation)
        assert container == record['container'] and binding == record['binding']
        if operation == 'acquire': return record['status']
        assert json.loads(path.read_text())['phase'] == 'releasing'
        if fault == 'reply-lost' and calls.count('release') == 1:
            raise TimeoutError('lost reply')
        if fault == 'rejected': raise installer.InstallError('revision-conflict')
        if fault == 'drift': bridge.atomic_json(path, {'changed': True})
        return dict(capability='available', phase='idle', streams=0, admission_blocked=False)
    monkeypatch.setattr(installer, '_migration_edge_request', request)
    if fault == 'reply-lost':
        with pytest.raises(TimeoutError): installer._finish_migration_hold({}, record)
        pending = json.loads(path.read_text())
        assert pending['phase'] == 'releasing'
        installer._finish_migration_hold({}, pending)
        assert calls == ['acquire', 'release', 'release']
    elif fault:
        with pytest.raises(installer.InstallError): installer._finish_migration_hold({}, record)
        assert json.loads(path.read_text()).get('phase') != 'released'
    else:
        installer._finish_migration_hold({}, record)
    if fault in (None, 'reply-lost'):
        assert json.loads(path.read_text())['phase'] == 'released'


@pytest.mark.parametrize('phase', ['active', 'restored'])
@pytest.mark.parametrize('fault', [None, 'drift', 'authority', 'ready', 'late-ready', 'release', 'missing-hold'])
def test_archived_recovery_only_releases_after_live_verification(monkeypatch, tmp_path, phase, fault):
    monkeypatch.setattr(installer, '_clear_candidate_stop_witnesses', Mock())
    from contextlib import contextmanager
    import pixel_access_bridge as bridge
    import pixel_macos_custody as custody
    held, events = [], []
    @contextmanager
    def lock(**kwargs):
        assert kwargs == {'completed_digest': 'b' * 64}
        held.append(True)
        try: yield
        finally: held.pop()
    adapter = SimpleNamespace(recovery_locked=lock)
    plan = {'access_settings': {'gateway_port': 18789}}
    journal = SimpleNamespace(value={'phase': phase})
    records = [dict(path='/fixture', before=b'old', after=b'new')]
    def load(**kwargs):
        assert held and kwargs['completed'] is True
        events.append('load')
        return ({'drift': True} if fault == 'authority' and len(events) > 1 else plan), journal, records
    monkeypatch.setattr(installer, '_load_upgrade_recovery', load)
    monkeypatch.setattr(installer, '_verify_recovery_bindings', lambda *a: None)
    previous = {role: 'old-' + role for role in ('gateway', 'access', 'relay')}
    candidate = {role: 'new-' + role for role in previous}
    monkeypatch.setattr(installer, '_upgrade_services', lambda *a: (previous, candidate))
    def read(path, **kwargs):
        if str(path) != '/fixture': return b'{"gateway_policy": {}}'
        return b'drift' if fault == 'drift' else (b'new' if phase == 'active' else b'old')
    monkeypatch.setattr(custody, 'protected_bytes', read)
    monkeypatch.setattr(installer, '_verify_bundle_selection', lambda p: events.append('bundle'))
    monkeypatch.setattr(installer, '_upgrade_service_identity', lambda s: events.append(s))
    monkeypatch.setattr(installer._policy, 'policy_state', lambda p: {})
    def ready(*a, **kw):
        if fault == 'ready': raise installer.InstallError('ready')
        if fault == 'late-ready' and events.count('load') >= 3:
            raise installer.InstallError('service-stopped-before-release')
    monkeypatch.setattr(installer, '_ready_gateway', ready)
    monkeypatch.setattr(installer, '_ready_access', ready)
    monkeypatch.setattr(installer, '_ready_access_relay', ready)
    path = tmp_path / 'hold'
    if fault != 'missing-hold': path.write_text('fixture')
    monkeypatch.setattr(installer, '_edge_hold_journal', lambda p: path)
    record = dict(schemaVersion=1, phase='releasing', container='c' * 64,
                  binding=dict(token='d' * 64, revision='e' * 64), status={})
    monkeypatch.setattr(bridge, 'private_json', lambda *a: record)
    def release(p, h):
        assert held and h == record
        events.append('release')
        if fault == 'release': raise TimeoutError('release reply lost')
    monkeypatch.setattr(installer, '_finish_migration_hold', release)
    def run():
        return installer._finish_archived_upgrade(adapter, current_digest='a' * 64,
            candidate_digest='b' * 64, owner_name='owner')
    if fault and not (fault == 'missing-hold' and phase == 'restored'):
        with pytest.raises((installer.InstallError, TimeoutError)): run()
        if fault != 'release': assert 'release' not in events
    else:
        assert run() == phase
        assert ('release' in events) == (fault != 'missing-hold')
        assert ('new-gateway' in events) == (phase == 'active')
        assert ('old-gateway' in events) == (phase == 'restored')
    assert not held


@pytest.mark.parametrize('fault', [None, 'running', 'existing', 'boot', 'snapshot', 'target'])
def test_candidate_absence_receipts_require_all_previous_jobs_stopped(fault):
    from pixel_gateway_service import launchd_definition_digest
    previous, candidate, records, saved = {}, {}, [], []
    for role in ('gateway', 'access', 'relay'):
        path = Path('/Library/LaunchDaemons/' + role + '.plist')
        old_doc = dict(Label=role, ProgramArguments=['/usr/bin/env', '-i', '/old/node'])
        new_doc = dict(old_doc, ProgramArguments=['/usr/bin/env', '-i', '/new/node'])
        digest = launchd_definition_digest(old_doc, installer.InstallError)
        witness = dict(target=role, boot='boot', definition=digest, processes=[[123, 456, 0]])
        def stopped(role=role):
            if fault == 'running' and role == 'relay': raise installer.InstallError('still-running')
        def missing(role=role):
            if fault == 'existing' and role == 'relay': return None
            raise FileNotFoundError()
        previous[role] = SimpleNamespace(target=role, plist=path, verify_definition=lambda: None,
            assert_stopped=stopped, load_stop=lambda w=witness: w,
            definition=lambda d=digest: d,
            _boot_identity=lambda: 'wrong' if fault == 'boot' else 'boot')
        candidate[role] = SimpleNamespace(target='wrong' if fault == 'target' else role, plist=path,
            load_stop=missing, save_stop=lambda value, r=role: saved.append((r, value)))
        records.append(dict(path=str(path), before=plistlib.dumps(new_doc if fault == 'snapshot' else old_doc),
                            after=plistlib.dumps(new_doc)))
    if fault:
        with pytest.raises(installer.InstallError):
            installer._prepare_candidate_stop_witnesses(previous, candidate, records)
        assert not saved
    else:
        installer._prepare_candidate_stop_witnesses(previous, candidate, records)
        assert len(saved) == 3
        for role, value in saved:
            assert value['target'] == role and value['boot'] == 'boot'
            assert value['processes'] == [[123, 456, 0]]
            assert value['definition'] == launchd_definition_digest(
                plistlib.loads(next(r['after'] for r in records if r['path'] == str(candidate[role].plist))),
                installer.InstallError)


@pytest.mark.parametrize('fault', [None, 'permissions', 'symlink', 'fifo', 'owner', 'acl'])
def test_candidate_stop_witness_cleanup_is_exact_and_idempotent(tmp_path, monkeypatch, fault):
    import pixel_macos_custody as custody
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', tmp_path)
    @contextmanager
    def directory(path):
        assert path == tmp_path
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try: yield fd
        finally: os.close(fd)
    monkeypatch.setattr(custody, 'protected_directory', directory)
    real_stat = os.fstat
    def root_stat(fd):
        value = real_stat(fd)
        return SimpleNamespace(st_mode=value.st_mode, st_nlink=value.st_nlink,
                               st_uid=501 if fault == 'owner' else 0)
    monkeypatch.setattr(os, 'fstat', root_stat)
    monkeypatch.setattr(custody, '_require_no_acl', Mock(side_effect=
        custody.CustodyError('acl') if fault == 'acl' else None))
    digest = 'b' * 64
    plan = {'runtime_bundle': {'digest': digest}}
    for role in ('gateway', 'access', 'relay'):
        path = installer._runtime_upgrade_stop_witness(digest, 'candidate', role)
        path.write_text('{}')
        path.chmod(0o600)
    unrelated = tmp_path / ('runtime-upgrade-stop-' + ('c' * 64) + '-candidate-gateway.json')
    unrelated.write_text('{}')
    unrelated.chmod(0o600)
    last = installer._runtime_upgrade_stop_witness(digest, 'candidate', 'relay')
    if fault == 'permissions': last.chmod(0o644)
    if fault in ('symlink', 'fifo'):
        last.unlink()
        if fault == 'symlink': last.symlink_to(unrelated)
        else: os.mkfifo(last, 0o600)
    if fault:
        with pytest.raises((installer.InstallError, custody.CustodyError, OSError)):
            installer._clear_candidate_stop_witnesses(plan)
        assert len(list(tmp_path.glob('runtime-upgrade-stop-' + digest + '-candidate-*.json'))) == 3
        assert unrelated.exists()
        return
    installer._clear_candidate_stop_witnesses(plan)
    installer._clear_candidate_stop_witnesses(plan)

    assert not list(tmp_path.glob('runtime-upgrade-stop-' + digest + '-candidate-*.json'))
    assert unrelated.exists()


@pytest.mark.parametrize('plan', [{}, {'runtime_bundle': {}}, {'runtime_bundle': {'digest': '../bad'}}])
def test_candidate_cleanup_rejects_incomplete_plan(plan):
    with pytest.raises(installer.InstallError, match='stop-witness-invalid'):
        installer._clear_candidate_stop_witnesses(plan)


@pytest.mark.parametrize('fault', [None, 'missing', 'mode', 'binding', 'runtime'])
def test_retirement_plan_rechecks_deployment_contract_before_live_use(monkeypatch, fault):
    a, b = 'a' * 64, 'b' * 64
    required, _ = installer._recovery_file_contract()
    records = [dict(path=path, mode=mode, gid=gid, before=b'old', after=b'new')
               for path, (mode, gid) in required.items()]
    if fault == 'missing': records.pop()
    if fault == 'mode': records[0]['mode'] = 0o755
    value = installer._upgrade.encode_recovery(records, current_digest=a, candidate_digest=b,
                                               allowed_paths={item['path'] for item in records})
    value['phase'] = 'restored'
    snapshots = {'runtime-upgrade-' + b + '.completed.json': json.dumps(value).encode(),
                 'runtime-upgrade-context-' + b + '.json': b'{}'}
    plan = {'fixture': True}
    decode = Mock(return_value=plan)
    bindings = Mock(side_effect=installer.InstallError('binding') if fault == 'binding' else None)
    runtime = Mock(side_effect=installer.InstallError('runtime') if fault == 'runtime' else None)
    monkeypatch.setattr(installer, '_decode_recovery_context', decode)
    monkeypatch.setattr(installer, '_verify_recovery_bindings', bindings)
    monkeypatch.setattr(installer, '_verify_recovery_runtime', runtime)
    def run():
        return installer._retirement_plan(snapshots, current_digest=a, candidate_digest=b, owner_name='owner')
    if fault:
        with pytest.raises(installer.InstallError): run()
        if fault in ('missing', 'mode'):
            decode.assert_not_called()
            bindings.assert_not_called()
        if fault != 'runtime': runtime.assert_not_called()
    else:
        assert run() == (plan, records)
        decode.assert_called_once_with({}, current_digest=a, candidate_digest=b, owner_name='owner')
        bindings.assert_called_once_with(plan, records)
        runtime.assert_called_once_with(plan)


@pytest.mark.parametrize('fault', [None, 'pending', 'file', 'ready', 'identity'])
def test_retirement_live_requires_stable_restored_services(monkeypatch, tmp_path, fault):
    import pixel_macos_custody as custody
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', tmp_path)
    if fault == 'pending': (tmp_path / 'transition.json').write_text('{}')
    monkeypatch.setattr(custody, 'protected_bytes', lambda *a, **kw: b'new' if fault == 'file' else b'old')
    services = dict(gateway='gateway', access='access', relay='relay')
    monkeypatch.setattr(installer, '_upgrade_services', lambda *a: (services, {}))
    calls = []
    def identity(service):
        calls.append(service)
        return 2 if fault == 'identity' and len(calls) > 3 else 1
    monkeypatch.setattr(installer, '_upgrade_service_identity', identity)
    monkeypatch.setattr(installer, '_upgrade_policy_mode', lambda p: 'sandboxed')
    ready = Mock(side_effect=installer.InstallError('ready') if fault == 'ready' else None)
    monkeypatch.setattr(installer, '_ready_gateway', ready)
    monkeypatch.setattr(installer, '_ready_access', Mock())
    monkeypatch.setattr(installer, '_ready_access_relay', Mock())
    def run():
        installer._verify_retirement_live({'access_settings': {'gateway_port': 18789}},
                                         [dict(path='/fixture', before=b'old')])
    if fault:
        with pytest.raises(installer.InstallError): run()
        if fault in ('pending', 'file'):
            assert not calls
            ready.assert_not_called()
    else:
        run()
        assert calls == list(services) * 2


def test_retirement_archive_keeps_lock_during_live_check_and_storage(monkeypatch):
    import pixel_access_bridge as bridge_module
    adapter = object.__new__(bridge_module.LaunchdAccessBridge)
    adapter.state = installer._launchd.ACCESS_STATE
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    held = []
    @contextmanager
    def locked(self):
        held.append(True)
        try: yield
        finally: held.pop()
    monkeypatch.setattr(bridge_module.LaunchdAccessBridge, 'locked', locked)
    plan, records, snapshots = {}, [], {'fixture': b'body'}
    def decode(*a, **kw):
        assert held
        return plan, records
    monkeypatch.setattr(installer, '_retirement_plan', decode)
    def live(p, r): assert held and p is plan and r is records
    monkeypatch.setattr(installer, '_verify_retirement_live', live)
    def archive(state, **kwargs):
        assert held and state == adapter.state and kwargs['snapshots'] is snapshots
        kwargs['verify_restored']()
        kwargs['verify_restored']()
        return 'history.json'
    monkeypatch.setattr(installer._upgrade, 'archive_restored_attempt', archive)
    assert installer._archive_verified_retirement(adapter, snapshots, current_digest='a' * 64,
        candidate_digest='b' * 64, owner_name='owner') == 'history.json'
    assert not held


def test_recovery_file_contract_matches_real_deployment(bundled_deployment):
    plan = installer.make_plan(**bundled_deployment)
    required, optional = installer._recovery_file_contract()
    actual = {str(path): (attrs['mode'], attrs.get('gid', 0))
              for path, body, attrs in installer._deployment_files(ROOT, plan)
              if attrs.get('uid', 0) == 0}
    assert optional.pop(str(installer._launchd.ACCESS_STATE / 'service-baseline.json')) == (0o600, 0)
    assert actual == {**required, **optional}
    assert str(installer._destination(installer.ACCESS_FILES['key'])) not in actual


@pytest.mark.parametrize('fault', [None, 'missing', 'mode', 'gid', 'context', 'runtime', 'digest'])
def test_load_upgrade_recovery_enforces_independent_contract(monkeypatch, fault):
    import pixel_access_bridge as bridge
    required, optional = installer._recovery_file_contract()
    records = [dict(path=path, mode=mode, gid=gid, before=b'old', after=b'new')
               for path, (mode, gid) in required.items()]
    if fault == 'missing':
        records.pop()
    if fault in ('mode', 'gid'):
        records[0][fault] = 1
    value = {'files': records}
    monkeypatch.setattr(bridge, 'private_json', Mock(return_value=value))
    journal, plan = SimpleNamespace(value=value), {'fixture': True}
    loader = Mock(return_value=(journal, records))
    monkeypatch.setattr(installer._upgrade.RecoveryJournal, 'load', loader)
    context = Mock(return_value=plan, side_effect=installer.InstallError('context') if fault == 'context' else None)
    runtime = Mock(side_effect=installer.InstallError('runtime') if fault == 'runtime' else None)
    monkeypatch.setattr(installer, '_load_recovery_context', context)
    monkeypatch.setattr(installer, '_verify_recovery_runtime', runtime)
    def run():
        return installer._load_upgrade_recovery(current_digest='a' * 64,
            candidate_digest='../unsafe' if fault == 'digest' else 'b' * 64, owner_name='owner')
    if fault:
        with pytest.raises(installer.InstallError):
            run()
        if fault in ('missing', 'mode', 'gid', 'digest'):
            context.assert_not_called()
            runtime.assert_not_called()
        if fault == 'digest':
            loader.assert_not_called()
    else:
        assert run() == (plan, journal, records)
        assert loader.call_args.kwargs['allowed_paths'] == set(required)
        runtime.assert_called_once_with(plan)


@pytest.mark.parametrize('managed', [False, True])
@pytest.mark.parametrize('with_profiles', [False, True])
@pytest.mark.parametrize('completed_phase', [None, 'active', 'restored', 'prepared'])
def test_load_upgrade_recovery_uses_real_journal_decoder(monkeypatch, with_profiles, completed_phase, managed):
    import pixel_access_bridge as bridge
    required, optional = installer._recovery_file_contract()
    contract = {**required, **(optional if with_profiles else {})}
    records = [dict(path=path, mode=mode, gid=gid, before=b'old', after=b'new')
               for path, (mode, gid) in contract.items()]
    context = {'fixture': True}
    if managed:
        context.update(owner=SimpleNamespace(pw_name='owner', pw_uid=501, pw_gid=20),
            native_services={'expected_digest': 'b' * 64}, migration_qualification={'approved': True})
        records.extend(managed_service_fixture(context, monkeypatch))
        contract.update(installer._managed_service_contract())
    value = installer._upgrade.encode_recovery(records, current_digest='a' * 64,
        candidate_digest='b' * 64, allowed_paths=set(contract))
    if completed_phase: value['phase'] = completed_phase
    reader = Mock(return_value=value)
    monkeypatch.setattr(bridge, 'private_json', reader)
    monkeypatch.setattr(installer, '_load_recovery_context', lambda *a, **kw: context)
    monkeypatch.setattr(installer, '_verify_recovery_runtime', lambda plan: None)
    def load():
        return installer._load_upgrade_recovery(current_digest='a' * 64,
            candidate_digest='b' * 64, owner_name='owner', completed=completed_phase is not None)
    if completed_phase == 'prepared':
        with pytest.raises(installer.InstallError, match='archive-not-terminal'): load()
        return
    plan, journal, loaded = load()
    assert loaded == records and journal.value == value
    assert reader.call_count == 3
    assert journal.path.name == ('runtime-upgrade-' + 'b' * 64 + '.completed.json'
                                 if completed_phase else 'runtime-upgrade.json')


@pytest.mark.parametrize('published', [False, True])
@pytest.mark.parametrize('fault', [None, 'destination', 'source-path', 'candidate-path',
                                  'old-config', 'new-config', 'qualification', 'custody'])
def test_recovery_runtime_checks_partial_staging(monkeypatch, published, fault):
    import pixel_macos_custody as custody
    current, candidate = (installer._bundle.INSTALL_ROOT / (letter * 64) for letter in 'ab')
    parent = installer.RUNTIME_CONFIG_ROOT / '501'
    selection = dict(digest='b' * 64, destination=str(candidate),
        source='/untrusted/staging', source_config=str(parent / 'openclaw.json'),
        config_path=str(parent / ('openclaw-' + 'b' * 64 + '.json')),
        source_config_bytes=b'old', config_bytes=b'new')
    qualification = dict(currentDigest='a' * 64, candidateDigest='b' * 64)
    plan = dict(owner=SimpleNamespace(pw_uid=501), runtime_bundle=selection,
                upgrade_qualification=qualification, upgrade_kind='workspace-root')
    field = {'destination': 'destination', 'source-path': 'source_config',
             'candidate-path': 'config_path'}.get(fault)
    if field:
        selection[field] = '/untrusted/path'
    metadata = Mock(side_effect=ValueError('custody') if fault == 'custody' else None)
    monkeypatch.setattr(custody, 'protected_tree_metadata', metadata)
    verify = Mock()
    monkeypatch.setattr(installer._bundle, 'verify', verify)
    qualify = Mock(return_value={} if fault == 'qualification' else qualification)
    monkeypatch.setattr(installer, '_qualify_upgrade', qualify)
    monkeypatch.setattr(installer.os.path, 'lexists', lambda path: published)
    def config(path, uid):
        assert uid == 501
        if path == selection['source_config']:
            return b'changed' if fault == 'old-config' else b'old'
        return b'changed' if fault == 'new-config' else b'new'
    monkeypatch.setattr(installer, '_configuration_bytes', config)
    fails = fault and (published or fault not in ('new-config', 'qualification'))
    if fails:
        with pytest.raises((installer.InstallError, ValueError)):
            installer._verify_recovery_runtime(plan)
    else:
        installer._verify_recovery_runtime(plan)
        verify.assert_called_once_with(current, expected_digest='a' * 64)
        if published:
            qualify.assert_called_once_with('workspace-root', current, candidate,
                current_digest='a' * 64, candidate_digest='b' * 64)
        else:
            qualify.assert_not_called()


def test_bundle_selection_failure_does_not_stop_old_gateway(migration, monkeypatch):
    m = migration
    m.plan['runtime_bundle'] = {'fixture': True}
    monkeypatch.setattr(installer, '_verify_bundle_selection',
                        Mock(side_effect=installer.InstallError('native-config-changed')))
    with pytest.raises(installer.InstallError, match='native-config-changed'):
        installer._activate(m.plan, m.services, m.journal)
    assert 'stop-old' not in m.events
    assert m.old.loaded and not m.old.disabled
    assert json.loads(m.journal.read_bytes())['phase'] == 'restored'


@pytest.mark.skipif(os.getuid() != 0 or sys.platform != 'linux', reason='isolated Linux root fixture')
def test_candidate_config_is_private_and_does_not_overwrite_old(bundled_deployment, monkeypatch):
    import pixel_macos_custody
    monkeypatch.setattr(pixel_macos_custody, '_require_no_acl', lambda _: None)
    with tempfile.TemporaryDirectory(dir='/root', prefix='pixel-config-') as directory:
        monkeypatch.setattr(installer, 'RUNTIME_CONFIG_ROOT', Path(directory) / 'configs')
        plan = installer.make_plan(**bundled_deployment)
        selection, owner = plan['runtime_bundle'], plan['owner']
        old = Path(selection['source_config']).read_bytes()
        installer._runtime_config(plan)
        assert not installer.RUNTIME_CONFIG_ROOT.exists()
        installer._runtime_config(plan, write=True)
        target = Path(selection['config_path'])
        assert target.read_bytes() == selection['config_bytes']
        assert target.stat().st_uid == owner.pw_uid
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert target.stat().st_nlink == 1
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        assert Path(selection['source_config']).read_bytes() == old
        installer._runtime_config(plan, write=True)
        target.write_bytes(b'changed candidate')
        with pytest.raises(installer.InstallError, match='candidate-config-changed'):
            installer._runtime_config(plan, write=True)
        assert target.read_bytes() == b'changed candidate'
