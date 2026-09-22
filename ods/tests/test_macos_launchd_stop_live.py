"""Opt-in real GUI launchd lifecycle; root file custody is fixture-only."""
import json
import importlib.util
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from pixel_gateway_service import LaunchdGatewayService, launchd_definition_digest
import pixel_macos_custody as custody

spec = importlib.util.spec_from_file_location('live_upgrade',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-runtime-upgrade.py')
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)

install_spec = importlib.util.spec_from_file_location('live_initial_install',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-macos-access-install.py')
installer = importlib.util.module_from_spec(install_spec)
install_spec.loader.exec_module(installer)


class CommandError(Exception):
    def __init__(self, code, returncode=None):
        self.code, self.returncode = code, returncode
        super().__init__(code)


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('ODS_TEST_LAUNCHD_LIVE') != '1',
                    reason='opt-in isolated macOS GUI launchd test')
@pytest.mark.parametrize('fail_readiness', [False, True])
def test_initial_activation_has_no_previous_job_to_restore(tmp_path, monkeypatch, fail_readiness):
    """Actual jobs/stop proofs; readiness is process-only, not a Pixel HTTP proof."""
    import socket
    domain = 'gui/' + str(os.getuid())
    prefix = 'com.ods.test.initial-' + uuid.uuid4().hex
    events, services = [], []
    def command(args, timeout=20):
        events.append(args)
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise installer.InstallError('host-command-failed', returncode=result.returncode)
        return result.stdout
    for role in ('source', 'gateway', 'access', 'relay'):
        label = prefix + '-' + role
        path = tmp_path / (role + '.plist')
        body = plistlib.dumps({'Label': label, 'ProgramArguments': ['/bin/sleep', '300'],
                               'RunAtLoad': True})
        path.write_bytes(body)
        def verify(path=path, body=body):
            assert path.read_bytes() == body
        services.append(LaunchdGatewayService(command, installer.InstallError,
            domain + '/' + label, verify, plist=path, verify_definition=verify))
    old, gateway, access, relay = services
    with socket.socket() as first, socket.socket() as second:
        first.bind(('127.0.0.1', 0))
        second.bind(('127.0.0.1', 0))
        gateway_port, access_port = first.getsockname()[1], second.getsockname()[1]
    plan = {'initial_install': True, 'source': old.plist,
            'owner': SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(tmp_path / 'Owner Home')),
            'access_settings': {'gateway_port': gateway_port}, 'access_port': access_port}
    journal = tmp_path / 'initial.json'
    monkeypatch.setattr(installer, '_command', command)
    monkeypatch.setattr(installer, '_ready_gateway', lambda service, port: service.pid(require_running=True))
    def ready_access(service):
        service.pid(require_running=True)
        if fail_readiness: raise installer.InstallError('fixture-not-ready')
    monkeypatch.setattr(installer, '_ready_access', ready_access)
    monkeypatch.setattr(installer, '_ready_access_relay', lambda service, plan: service.pid(require_running=True))
    try:
        if fail_readiness:
            with pytest.raises(installer.InstallError, match='fixture-not-ready'):
                installer._activate(plan, services, journal)
            for service in services[1:]:
                service.assert_stopped()
                assert installer._job_disabled(service.target)
        else:
            installer._activate(plan, services, journal)
            for service in services[1:]: assert service.pid(require_running=True) > 0
        with pytest.raises(installer.InstallError) as absent:
            command(['/bin/launchctl', 'print', old.target])
        assert absent.value.returncode == 113
        assert not any(args[1] in ('bootstrap', 'enable', 'disable', 'bootout')
                       and args[-1] in (old.target, str(old.plist)) for args in events)
        value = json.loads(journal.read_text())
        assert value['phase'] == ('initial-stopped' if fail_readiness else 'active')
        assert value['operation'] == 'initial-install'
    finally:
        for service in reversed(services[1:]):
            try:
                service.pid(require_running=True)
            except installer.InstallError as error:
                if error.returncode != 113: raise
            else:
                service.stop()
                service.assert_stopped()
            command(['/bin/launchctl', 'enable', service.target])


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('ODS_TEST_LAUNCHD_LIVE') != '1',
                    reason='opt-in isolated macOS GUI launchd test')
@pytest.mark.parametrize('replace_definition', [False, True])
def test_stop_witness_survives_adapter_recreation(tmp_path, monkeypatch, replace_definition):
    label = 'com.ods.test.stop-' + uuid.uuid4().hex
    target = 'gui/' + str(os.getuid()) + '/' + label
    path = tmp_path / (label + '.plist')
    witness = tmp_path / 'stop.json'
    document = dict(Label=label, ProgramArguments=['/usr/bin/env', '-i', '/bin/sleep', '300'],
                    RunAtLoad=True)
    expected = plistlib.dumps(document)
    path.write_bytes(expected)
    def command(arguments, timeout=20):
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise CommandError('host-command-failed', result.returncode)
        return result.stdout
    def verify():
        assert path.read_bytes() == expected
    protected_read = custody.protected_bytes
    monkeypatch.setattr(custody, 'protected_bytes', lambda filename, **kwargs:
                        path.read_bytes() if Path(filename) == path else protected_read(filename, **kwargs))
    def save(value):
        with witness.open('w') as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
    def service():
        return LaunchdGatewayService(command, CommandError, target, verify,
            plist=path, save_stop=save, load_stop=lambda: json.loads(witness.read_text()))
    loaded = False
    try:
        command(['/bin/launchctl', 'bootstrap', target.rsplit('/', 1)[0], str(path)])
        loaded = True
        original = service()
        deadline = time.monotonic() + 15
        while True:
            try:
                pid = original.pid(require_running=True)
                break
            except CommandError:
                if time.monotonic() >= deadline: raise
                time.sleep(0.1)
        original.stop()
        original.assert_stopped()
        loaded = False
        if replace_definition:
            old_witness = json.loads(witness.read_text())
            document['ProgramArguments'][-1] = '301'
            expected = plistlib.dumps(document)
            save(dict(old_witness, definition=launchd_definition_digest(document, CommandError)))
            path.write_bytes(expected)
        recovered = service()
        assert recovered._stopping_tree is None
        recovered.assert_stopped()
        assert any(row[0] == pid for row in recovered._stopping_tree)
        value = json.loads(witness.read_text())
        value['target'] = target + '-different'
        save(value)
        with pytest.raises(CommandError, match='native-stop-witness-unavailable'):
            service().assert_stopped()
        save(None)
        with pytest.raises(CommandError, match='native-stop-witness-unavailable'):
            service().assert_stopped()
    finally:
        if loaded:
            command(['/bin/launchctl', 'bootout', target])


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('ODS_TEST_LAUNCHD_LIVE') != '1',
                    reason='opt-in isolated macOS GUI launchd test')
@pytest.mark.parametrize('absent_role', [None, 'gateway', 'access', 'relay'])
def test_three_live_jobs_recover_in_dependency_order(tmp_path, monkeypatch, absent_role):
    domain = 'gui/' + str(os.getuid())
    prefix = 'com.ods.test.recover-' + uuid.uuid4().hex
    previous, candidate, files, loaded, events = {}, {}, {}, set(), []
    def command(arguments, timeout=20):
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise CommandError('host-command-failed', result.returncode)
        return result.stdout
    def make(role, version, path, expected):
        target = domain + '/' + prefix + '-' + role
        witness = tmp_path / (role + '-' + version + '.json')
        def verify():
            assert path.read_bytes() == expected
        def save(value):
            with witness.open('w') as handle:
                json.dump(value, handle)
                handle.flush()
                os.fsync(handle.fileno())
        service = LaunchdGatewayService(command, CommandError, target, verify,
            plist=path, save_stop=save, load_stop=lambda: json.loads(witness.read_text()))
        service.test_role = role
        return service
    for role in ('gateway', 'access', 'relay'):
        path = tmp_path / (role + '.plist')
        def definition(seconds):
            return plistlib.dumps(dict(Label=prefix + '-' + role,
                ProgramArguments=['/usr/bin/env', '-i', '/bin/sleep', str(seconds)], RunAtLoad=True))
        before, after = definition(300), definition(301)
        files[path] = (before, after)
        path.write_bytes(after)
        previous[role] = make(role, 'previous', path, before)
        candidate[role] = make(role, 'candidate', path, after)
    protected_read = custody.protected_bytes
    monkeypatch.setattr(custody, 'protected_bytes', lambda filename, **kwargs:
        Path(filename).read_bytes() if Path(filename) in files else protected_read(filename, **kwargs))
    def start(service):
        service.verify_definition()
        command(['/bin/launchctl', 'bootstrap', domain, str(service.plist)])
        loaded.add(service.target)
        deadline = time.monotonic() + 15
        while True:
            try:
                service.pid(require_running=True)
                break
            except CommandError:
                if time.monotonic() >= deadline: raise
                time.sleep(0.1)
        events.append('start-' + service.test_role)
    def stop(service):
        service.stop()
        service.assert_stopped()
        loaded.discard(service.target)
        events.append('stop-' + service.test_role)
    def observe(old, new):
        new.verify_definition()
        try:
            new.pid(require_running=True)
        except CommandError as error:
            if error.returncode != 113: raise
            return 'absent'
        return 'candidate'
    def absent(old, new):
        new.assert_stopped()
        events.append('absent-' + new.test_role)
    def verify_files():
        assert all(path.read_bytes() in versions for path, versions in files.items())
    def restore():
        assert not loaded
        for service in candidate.values(): service.assert_stopped()
        for path, (before, after) in files.items():
            assert path.read_bytes() == after
            path.write_bytes(before)
        events.append('restore')
    def ready(services):
        assert services is previous
        for service in services.values():
            service.verify_definition()
            service.pid(require_running=True)
        events.append('ready')
    try:
        for service in candidate.values(): start(service)
        if absent_role:
            stop(candidate[absent_role])
            # Recovery must read the durable witness, not retained Python state.
            candidate[absent_role]._stopping_tree = None
        events.clear()
        upgrade.recover_previous(previous=previous, candidate=candidate,
            phase=lambda phase: events.append(phase), verify_snapshots=verify_files,
            observe=observe, assert_absent=absent, restore_files=restore,
            start=start, stop=stop, ready=ready)
        assert events == ['rolling-back',
            *[('absent-' if role == absent_role else 'stop-') + role for role in ('relay', 'access', 'gateway')],
            'restore', 'start-gateway', 'start-access', 'start-relay', 'ready', 'restored']
    finally:
        for target in tuple(loaded):
            command(['/bin/launchctl', 'bootout', target])
            loaded.remove(target)
