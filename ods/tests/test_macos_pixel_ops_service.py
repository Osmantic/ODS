import importlib.util
import hashlib
from pathlib import Path
import plistlib
import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('ops_service',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-ops-service.py')
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)


@pytest.fixture
def arguments():
    return dict(identity={'name': '_ods_pixel_ops', 'uid': 61000, 'gid': 61000},
        python='/usr/bin/python3', program_root='/usr/local/libexec/ods-ops',
        state='/private/var/lib/pixel-ops-broker', policy={'schemaVersion': 2, 'targets': {
            'local': {'backend': 'local', 'allowedRoots': ['/srv/read'], 'writableRoots': ['/srv/write']},
            'remote': {'backend': 'ssh', 'allowedRoots': ['/remote/read'], 'writableRoots': ['/remote/write']}}})


def test_native_broker_daemon_preserves_isolation_and_local_policy_roots(arguments):
    rendered = service.render(**arguments)
    doc = plistlib.loads(rendered['plist'])
    assert doc['UserName'] == '_ods_pixel_ops' == doc['GroupName']
    assert doc['ProgramArguments'][:2] == ['/usr/bin/env', '-i']
    assert '-I' in doc['ProgramArguments'] and '/usr/bin/sandbox-exec' in doc['ProgramArguments']
    assert doc['Umask'] == 0o077 and doc['KeepAlive'] is True
    profile = rendered['profile'].decode()
    assert '(deny default)' in profile
    assert '/srv/read' in profile and '/srv/write' in profile
    assert '/remote/' not in profile
    assert '(deny file-write* (subpath "/usr/bin/python3")' in profile


@pytest.mark.parametrize('fault', ['root', 'other-account', 'label', 'overlap', 'write-code', 'relative'])
def test_unsafe_service_plan_refused(arguments, fault):
    if fault == 'root': arguments['identity']['uid'] = 0
    if fault == 'other-account': arguments['identity']['name'] = '_www'
    if fault == 'label': arguments['label'] = 'com.other.service'
    if fault == 'overlap': arguments['state'] = arguments['program_root'] + '/state'
    if fault == 'write-code': arguments['policy']['targets']['local']['writableRoots'] = [arguments['program_root']]
    if fault == 'relative': arguments['state'] = '../state'
    with pytest.raises(ValueError): service.render(**arguments)


@pytest.fixture
def publication(arguments, monkeypatch):
    options = dict(arguments)
    options['policy_body'] = json.dumps(options.pop('policy')).encode()
    options.update(broker_body=b'approved test bytes',
        expected_broker_sha256=hashlib.sha256(b'approved test bytes').hexdigest(),
        definition='/Library/LaunchDaemons/com.ods.pixel-native-operations.plist')
    events = []
    monkeypatch.setattr(service.sys, 'platform', 'darwin')
    monkeypatch.setattr(service.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(service.pwd, 'getpwnam', lambda name: SimpleNamespace(
        pw_uid=61000, pw_gid=61000, pw_shell='/usr/bin/false'))
    monkeypatch.setattr(service.custody, 'protected_bytes', lambda *args, **kw: events.append(('python', args)))
    helpers = SimpleNamespace(
        _preflight_file=lambda path, body, **kw: events.append(('preflight', path)),
        _write_exact=lambda path, body, **kw: events.append(('write', path)))
    monkeypatch.setattr(service, 'installer_helpers', lambda: helpers)
    def run(argv, **kwargs):
        events.append(('validate', (argv, kwargs)))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(service.subprocess, 'run', run)
    return options, events, helpers


def test_operations_plan_is_read_only_and_does_not_execute_broker(publication):
    options, events, _ = publication
    files = service.publication_files(**options)
    assert len(files) == 4
    assert [event[0] for event in events] == ['python']
    assert files[1][2:] == (0o640, options['identity']['gid'])
    assert files[-1][0] == Path(options['definition'])


def test_publication_validates_as_limited_account_before_definition(publication):
    options, events, _ = publication
    assert service.publish(**options) == Path(options['definition'])
    assert [event[0] for event in events] == ['python'] + ['preflight'] * 4 + ['write'] * 3 + ['validate', 'write']
    argv, kwargs = events[-2][1]
    assert argv[0] == '/usr/bin/sandbox-exec' and '-I' in argv
    assert kwargs['user'] == kwargs['group'] == 61000
    assert kwargs['extra_groups'] == []
    assert events[-1][1] == Path(options['definition'])


@pytest.mark.parametrize('fault', ['snapshot', 'json', 'identity', 'label', 'custody', 'preflight', 'validation', 'timeout'])
def test_publication_failure_never_writes_definition(publication, monkeypatch, fault):
    options, events, helpers = publication
    if fault == 'snapshot': options['expected_broker_sha256'] = '0' * 64
    if fault == 'json': options['policy_body'] = b'not json'
    if fault == 'identity': options['identity']['uid'] = 61001
    if fault == 'label': options['definition'] = '/Library/LaunchDaemons/foreign.plist'
    def fail(*args, **kwargs): raise ValueError('injected failure')
    if fault == 'custody': monkeypatch.setattr(service.custody, 'protected_bytes', fail)
    if fault == 'preflight': helpers._preflight_file = fail
    if fault == 'validation':
        monkeypatch.setattr(service.subprocess, 'run', lambda *args, **kw: SimpleNamespace(returncode=1))
    if fault == 'timeout':
        def timeout(*args, **kw): raise subprocess.TimeoutExpired('validation', 30)
        monkeypatch.setattr(service.subprocess, 'run', timeout)
    with pytest.raises((ValueError, subprocess.TimeoutExpired)): service.publish(**options)
    assert ('write', Path(options['definition'])) not in events
    if fault not in ('validation', 'timeout'):
        assert not any(event[0] == 'write' for event in events)


@pytest.mark.parametrize('output,code', [('', 0), ('relative\n', 0), ('/one\n/two\n', 0),
                                       ('/valid\n', 1), ('/' + 'a' * 4096, 0)])
def test_python_selection_rejects_invalid_discovery(monkeypatch, output, code):
    monkeypatch.setattr(service.sys, 'platform', 'darwin')
    monkeypatch.setattr(service.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=code, stdout=output))
    with pytest.raises(ValueError): service.select_python()


def test_python_selection_checks_final_executable_custody(monkeypatch):
    monkeypatch.setattr(service.sys, 'platform', 'darwin')
    final = '/Library/Developer/final/Python'
    def run(argv, **kw):
        assert argv[:4] == ['/usr/bin/python3', '-I', '-B', '-c']
        assert set(kw['env']) == {'PATH', 'HOME', 'TMPDIR'}
        return SimpleNamespace(returncode=0, stdout=final + '\n')
    checked = []
    monkeypatch.setattr(service.subprocess, 'run', run)
    monkeypatch.setattr(service.custody, 'protected_bytes', lambda path, **kw: checked.append(path))
    assert service.select_python() == Path(final)
    assert checked == [final]


@pytest.mark.skipif(sys.platform != 'darwin' or os.geteuid() != 0 or
    os.environ.get('ODS_TEST_OPS_SERVICE_LIVE') != '1', reason='explicit root launchd broker qualification')
def test_real_confined_launchdaemon_executes_upstream_request():
    spec = importlib.util.spec_from_file_location('ops_state_service_test',
        Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-ops-state.py')
    spool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spool)
    broker = pwd.getpwnam('_ods_pixel_ops')
    owner = pwd.getpwuid(int(os.environ['SUDO_UID']))
    source = Path(os.environ['ODS_TEST_PIXEL_SOURCE'])
    node = os.environ['ODS_TEST_PIXEL_NODE']
    label = 'com.ods.pixel-native-operations.test-' + uuid.uuid4().hex
    target = 'system/' + label

    def launch(*args):
        return subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True, timeout=30)

    def owner_run(args):
        result = subprocess.run(args, user=owner.pw_uid, group=owner.pw_gid,
            extra_groups=os.getgrouplist(owner.pw_name, owner.pw_gid), cwd='/',
            env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        return result.stdout

    with tempfile.TemporaryDirectory(prefix='ods-ops-service-', dir='/private/var/lib') as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        program = root / 'program'
        program.mkdir(mode=0o755)
        state = spool.provision(state=root / 'state', gateway_uid=owner.pw_uid,
            broker_uid=broker.pw_uid, broker_gid=broker.pw_gid)
        shutil.copyfile(source / 'plugin-ops/publish-json.js', root / 'publish.mjs')
        (root / 'publish.mjs').chmod(0o644)
        outside = root / 'outside.txt'
        outside.write_text('original')
        os.chown(outside, broker.pw_uid, broker.pw_gid)
        outside.chmod(0o600)
        writable_probe = subprocess.run(['/usr/bin/python3', '-c',
            'import sys; open(sys.argv[1], "w").write("original")', str(outside)],
            user=broker.pw_uid, group=broker.pw_gid, extra_groups=[],
            capture_output=True, text=True, timeout=10)
        assert writable_probe.returncode == 0, writable_probe.stderr
        policy = {'schemaVersion': 2, 'targets': {'control': {'enabled': True,
            'backend': 'local', 'environment': 'lab', 'expectedHostname': socket.gethostname(),
            'defaultCwd': str(state), 'allowedRoots': [str(state)], 'allowRaw': False}},
            'actions': {'host.identity': {'description': 'identity', 'tier': 'read',
                'effect': 'observe', 'defaultAuthority': 'observe', 'idempotent': True,
                'reversible': False, 'targets': ['control'], 'argv': ['/bin/hostname'],
                'cwd': str(state), 'exclusiveTarget': False}},
            'authority': {'defaultLevel': 'propose', 'grants': []}}
        policy['actions']['sandbox.boundary'] = {**policy['actions']['host.identity'],
            'argv': ['/usr/bin/python3', '-I', '-c',
                'import sys\ntry:\n open(sys.argv[1], "w").write("escaped")\n'
                'except PermissionError:\n print("denied")\n'
                'else:\n raise SystemExit("sandbox escaped")', str(outside)]}
        broker_body = (source / 'deploy/ops-broker/broker.py').read_bytes()
        python = service.select_python()
        publication = dict(identity={'name': broker.pw_name, 'uid': broker.pw_uid, 'gid': broker.pw_gid},
            python=python, program_root=program, state=state,
            policy_body=json.dumps(policy).encode(), label=label, broker_body=broker_body,
            expected_broker_sha256=hashlib.sha256(broker_body).hexdigest(),
            definition=root / (label + '.plist'))
        definition = service.publish(**publication)
        assert service.publish(**publication) == definition
        assert (program / 'policy.json').stat().st_mode & 0o777 == 0o640
        assert (program / 'policy.json').stat().st_gid == broker.pw_gid
        bootstrapped = False
        try:
            result = launch('bootstrap', 'system', str(definition))
            assert result.returncode == 0, result.stderr
            bootstrapped = True
            job = 'ops-' + str(int(time.time() * 1000)) + '-abcdef123456'
            request = {'schemaVersion': 1, 'jobId': job, 'createdAt': datetime.now(timezone.utc).isoformat(),
                       'kind': 'action', 'target': 'control', 'action': 'host.identity'}
            code = ('import {publishBrokerJson} from ' + repr((root / 'publish.mjs').as_uri()) +
                '; await publishBrokerJson(process.argv[1], process.argv[2], JSON.parse(process.argv[3]));')
            owner_run([node, '--input-type=module', '-e', code, str(state / 'requests'), job, json.dumps(request)])
            deadline = time.monotonic() + 30
            result_path = state / 'results' / (job + '.json')
            value = {}
            while time.monotonic() < deadline:
                if result_path.exists():
                    value = json.loads(result_path.read_text())
                    if value.get('status') in ('succeeded', 'failed', 'awaiting-approval'): break
                time.sleep(0.2)
            logs = state / 'runtime/broker.stderr.log'
            assert value.get('status') == 'succeeded', (value, logs.read_text() if logs.exists() else 'no stderr')
            assert value['steps'][0]['stdout'].strip() == socket.gethostname()
            projected = owner_run(['/usr/bin/python3', '-c', 'import sys; print(open(sys.argv[1]).read())', str(result_path)])
            assert json.loads(projected)['status'] == 'succeeded'
            status = launch('print', target)
            assert status.returncode == 0 and 'state = running' in status.stdout
            first_pid = re.search(r'^\s*pid = (\d+)$', status.stdout, re.M).group(1)
            assert launch('kill', 'SIGTERM', target).returncode == 0
            job = 'ops-' + str(int(time.time() * 1000)) + '-123456abcdef'
            request.update(jobId=job, action='sandbox.boundary', createdAt=datetime.now(timezone.utc).isoformat())
            owner_run([node, '--input-type=module', '-e', code, str(state / 'requests'), job, json.dumps(request)])
            result_path = state / 'results' / (job + '.json')
            deadline = time.monotonic() + 35
            value = {}
            while time.monotonic() < deadline:
                if result_path.exists():
                    value = json.loads(result_path.read_text())
                    if value.get('status') in ('succeeded', 'failed', 'awaiting-approval'): break
                time.sleep(0.2)
            assert value.get('status') == 'succeeded', (value, logs.read_text())
            assert value['steps'][0]['stdout'].strip() == 'denied'
            assert outside.read_text() == 'original'
            status = launch('print', target)
            assert status.returncode == 0
            assert re.search(r'^\s*pid = (\d+)$', status.stdout, re.M).group(1) != first_pid
        finally:
            if bootstrapped:
                result = launch('bootout', target)
                assert result.returncode == 0, result.stderr
                assert launch('print', target).returncode == 113
