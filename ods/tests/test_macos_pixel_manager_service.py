import importlib.util
from contextlib import nullcontext
import hashlib
from pathlib import Path
import plistlib
import http.server
import json
import os
import pwd
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest


SPEC = importlib.util.spec_from_file_location('native_manager',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-manager-service.py')
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


@pytest.fixture
def arguments():
    return dict(python='/Library/Developer/final/Python', program_root='/usr/local/libexec/ods-manager',
        environment='/Users/fixture/ods/.env', owner='fixture', port=3002)


def test_manager_preserves_owner_group_and_limits_network_to_dashboard(arguments):
    result = manager.render(**arguments)
    definition = plistlib.loads(result['plist'])
    assert definition['UserName'] == 'fixture'
    assert 'GroupName' not in definition
    profile = result['profile'].decode()
    assert '(remote tcp "localhost:3002")' in profile
    assert '(allow network*)' not in profile
    assert '(deny process-fork)' in profile
    assert '(literal "/Users/fixture/ods/.env")' in profile
    assert '/pixel-ops-broker/approvals' not in profile


@pytest.mark.parametrize('fault', ['root', 'port', 'relative', 'credential-write', 'program-write'])
def test_invalid_manager_plan_is_refused(arguments, fault):
    if fault == 'root': arguments['owner'] = 'root'
    if fault == 'port': arguments['port'] = 0
    if fault == 'relative': arguments['runtime'] = 'run'
    if fault == 'credential-write': arguments['runtime'] = '/Users/fixture/ods'
    if fault == 'program-write': arguments['logs'] = arguments['program_root']
    with pytest.raises(ValueError): manager.render(**arguments)


@pytest.fixture
def publication(arguments, monkeypatch, tmp_path):
    environment = tmp_path / 'ods.env'
    environment.write_text('DASHBOARD_API_KEY=fixture\n')
    environment.chmod(0o600)
    args = dict(arguments, environment=environment,
        sources={'extension_manager.py': b'"""fixture"""', 'unix_peer.py': b'"""peer"""'},
        definition='/Library/LaunchDaemons/com.ods.pixel-native-manager.plist')
    args['expected_sha256'] = {name: hashlib.sha256(body).hexdigest() for name, body in args['sources'].items()}
    monkeypatch.setattr(manager.sys, 'platform', 'darwin')
    monkeypatch.setattr(manager.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(manager.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=environment.stat().st_uid, pw_gid=os.getgid()))
    monkeypatch.setattr(manager, 'prepare_logs', lambda *args, **kwargs: None)
    events = []
    installer = SimpleNamespace(
        _preflight_file=lambda path, body, **kw: events.append(('preflight', path)),
        _write_exact=lambda path, body, **kw: events.append(('write', path)))
    helpers = SimpleNamespace(installer_helpers=lambda: installer,
        custody=SimpleNamespace(protected_bytes=lambda path, **kw: None,
                                protected_directory=lambda path, **kw: nullcontext()))
    monkeypatch.setattr(manager, 'operations_helpers', lambda: helpers)
    return args, events


def test_manager_plan_is_read_only(publication, monkeypatch):
    args, events = publication
    if args['environment'].stat().st_uid == 0: pytest.skip('owner file fixture requires nonroot test runner')
    def unexpected(*args, **kwargs): pytest.fail('planning created logs')
    monkeypatch.setattr(manager, 'prepare_logs', unexpected)
    files = manager.publication_files(**args)
    assert len(files) == 4 and not events
    assert files[-1][0] == Path(args['definition'])
    assert all(mode == 0o644 and gid == 0 for _, _, mode, gid in files)
    assert all(b'DASHBOARD_API_KEY=fixture' not in body for _, body, _, _ in files)


def test_manager_publication_keeps_credentials_out_of_bundle(publication):
    args, events = publication
    if args['environment'].stat().st_uid == 0: pytest.skip('owner file fixture requires nonroot test runner')
    assert manager.publish(**args) == Path(args['definition'])
    assert [event[0] for event in events] == ['preflight'] * 4 + ['write'] * 4
    assert events[-1][1] == Path(args['definition'])
    assert all(path != args['environment'] for _, path in events)


@pytest.mark.parametrize('fault', ['changed-source', 'extra-source', 'linked-env', 'writable-env', 'readable-env', 'foreign-owner'])
def test_invalid_manager_publication_does_not_write(publication, monkeypatch, fault):
    args, events = publication
    if fault == 'changed-source': args['sources']['unix_peer.py'] = b'changed'
    if fault == 'extra-source': args['sources']['extra.py'] = b'pass'
    if fault == 'linked-env':
        linked = args['environment'].with_name('link.env')
        linked.symlink_to(args['environment'])
        args['environment'] = linked
    if fault == 'writable-env': args['environment'].chmod(0o666)
    if fault == 'readable-env': args['environment'].chmod(0o644)
    if fault == 'foreign-owner': monkeypatch.setattr(manager.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=9999))
    with pytest.raises(ValueError): manager.publish(**args)
    assert not events


@pytest.mark.skipif(sys.platform != 'darwin' or os.geteuid() != 0 or
    os.environ.get('ODS_TEST_MANAGER_LIVE') != '1', reason='explicit root manager qualification')
def test_confined_manager_uses_private_credential_and_projects_inventory():
    libraries = Path(__file__).resolve().parents[1] / 'installers/macos/lib'
    def load(name):
        spec = importlib.util.spec_from_file_location(name, libraries / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    ops = load('pixel-native-ops-state')
    python = load('pixel-native-ops-service').select_python()
    owner = pwd.getpwuid(int(os.environ['SUDO_UID']))
    broker = pwd.getpwnam('_ods_pixel_ops')
    calls = []
    credential = 'a' * 64  # Synthetic fixture key, never a live ODS credential.
    class API(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get('Authorization')))
            body = b'{"extensions":[]}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args): pass
    api = http.server.HTTPServer(('127.0.0.1', 0), API)
    thread = threading.Thread(target=api.serve_forever)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='ods-manager-', dir='/private/var/lib') as temporary:
            root = Path(temporary)
            root.chmod(0o755)
            code, logs = root / 'code', root / 'logs'
            code.mkdir(mode=0o755)
            logs.mkdir(mode=0o755)
            runtime = ops.provision_manager_runtime(runtime=root / 'run', gateway_uid=owner.pw_uid,
                broker_uid=broker.pw_uid, broker_gid=broker.pw_gid)
            host = Path(__file__).resolve().parents[1] / 'extensions/services/pixel-agent/host'
            sources = {name: (host / name).read_bytes() for name in ('extension_manager.py', 'unix_peer.py')}
            environment = root / 'ods.env'
            environment.write_text('DASHBOARD_API_KEY=' + credential + '\n')
            environment.chmod(0o600)
            os.chown(environment, owner.pw_uid, owner.pw_gid)
            profile = code / 'manager.sb'
            publication = dict(python=python, program_root=code, environment=environment,
                owner=owner.pw_name, port=api.server_port, runtime=runtime, logs=logs, sources=sources,
                expected_sha256={name: hashlib.sha256(body).hexdigest() for name, body in sources.items()},
                definition=root / 'com.ods.pixel-native-manager.plist')
            assert manager.publish(**publication) == manager.publish(**publication)
            for name in ('stdout.log', 'stderr.log'):
                log = logs / name
                assert log.stat().st_uid == owner.pw_uid
                assert log.stat().st_mode & 0o777 == 0o600
            (logs / 'stdout.log').write_text('preserve existing logs\n')
            manager.publish(**publication)
            assert (logs / 'stdout.log').read_text() == 'preserve existing logs\n'
            endpoint = runtime / 'extension-manager.sock'
            harness = ('import importlib.util,sys; from pathlib import Path; '
                's=importlib.util.spec_from_file_location("fixture",sys.argv[1]); '
                'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                'm.SOCKET_PATH=Path(sys.argv[2]); m.serve(m.SOCKET_PATH,Path(sys.argv[3]),int(sys.argv[4]))')
            command = ['/usr/bin/sandbox-exec', '-f', str(profile), str(python), '-I', '-B']
            env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'TMPDIR': str(runtime)}
            with (logs / 'stderr').open('wb') as stderr:
                process = subprocess.Popen([*command, '-c', harness, str(code / 'extension_manager.py'),
                    str(endpoint), str(environment), str(api.server_port)], user=owner.pw_uid,
                    group=owner.pw_gid, extra_groups=[], cwd='/', env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
                try:
                    deadline = time.monotonic() + 10
                    while not endpoint.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.05)
                    assert endpoint.exists(), (logs / 'stderr').read_text()
                    client = ('import socket,sys; s=socket.socket(socket.AF_UNIX); s.settimeout(10); '
                        's.connect(sys.argv[1]); s.sendall(b\'{"schemaVersion":1,"action":"list","extensionId":"all"}\\n\'); '
                        'print(s.makefile().readline())')
                    def run_as(user, argv):
                        return subprocess.run(argv, user=user.pw_uid, group=user.pw_gid, extra_groups=[],
                            cwd='/', env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True, timeout=15)
                    response = run_as(broker, [str(python), '-I', '-c', client, str(endpoint)])
                    assert response.returncode == 0, response.stderr
                    assert json.loads(response.stdout)['outcome'] == 'succeeded', response.stdout
                    assert credential not in response.stdout
                    assert calls == [('/api/extensions/catalog', 'Bearer ' + credential)]
                    rejected = run_as(owner, [str(python), '-I', '-c', client, str(endpoint)])
                    assert rejected.returncode == 0
                    assert json.loads(rejected.stdout)['outcome'] != 'succeeded'
                    assert len(calls) == 1
                    private = run_as(broker, [str(python), '-I', '-c', 'import sys; open(sys.argv[1]).read()', str(environment)])
                    assert private.returncode != 0 and 'PermissionError' in private.stderr
                    with socket.socket() as forbidden:
                        forbidden.bind(('127.0.0.1', 0))
                        forbidden.listen()
                        probe = ('import socket,sys\ntry:\n socket.create_connection(("127.0.0.1",int(sys.argv[1])),1)\n'
                            'except PermissionError:\n print("denied")\nelse:\n raise SystemExit("network escaped")')
                        denied = run_as(owner, [*command, '-c', probe, str(forbidden.getsockname()[1])])
                        assert denied.returncode == 0 and denied.stdout.strip() == 'denied', denied.stderr
                    # Exercise the real broker -> manager -> dashboard chain.
                    # The wrapper changes only the fixture socket constant.
                    bridge = code / 'manager-client.py'
                    bridge.write_text('import importlib.util\nfrom pathlib import Path\n'
                        's=importlib.util.spec_from_file_location("manager",Path(__file__).with_name("extension_manager.py"))\n'
                        'm=importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\n'
                        'm.SOCKET_PATH=Path(' + repr(str(endpoint)) + ')\n'
                        'm.client(m.SOCKET_PATH,"list","all")\n')
                    bridge.chmod(0o644)
                    state = ops.provision(state=root / 'broker-state', gateway_uid=owner.pw_uid,
                        broker_uid=broker.pw_uid, broker_gid=broker.pw_gid)
                    broker_policy = {'schemaVersion': 2, 'targets': {'ods-host': {
                        'enabled': True, 'backend': 'local', 'environment': 'lab',
                        'expectedHostname': socket.gethostname(), 'defaultCwd': str(state),
                        'allowedRoots': [str(state), str(runtime), str(code)], 'allowRaw': False}},
                        'actions': {'ods.extensions.list': {'description': 'list', 'tier': 'read',
                            'effect': 'observe', 'defaultAuthority': 'observe', 'idempotent': True,
                            'reversible': False, 'targets': ['ods-host'], 'argv': [str(python), str(bridge)]}},
                        'authority': {'defaultLevel': 'propose', 'grants': []}}
                    broker_source = (Path(os.environ['ODS_TEST_PIXEL_SOURCE']) / 'deploy/ops-broker/broker.py').read_bytes()
                    operations = load('pixel-native-ops-service')
                    broker_definition = operations.publish(broker_body=broker_source,
                        expected_broker_sha256=hashlib.sha256(broker_source).hexdigest(),
                        policy_body=json.dumps(broker_policy).encode(),
                        identity={'name': broker.pw_name, 'uid': broker.pw_uid, 'gid': broker.pw_gid},
                        python=python, program_root=root / 'broker-code', state=state,
                        definition=root / 'com.ods.pixel-native-operations.plist')
                    job = 'ops-' + str(int(time.time() * 1000)) + '-abcdef123456'
                    request_file = state / 'requests' / (job + '.json')
                    request_file.write_text(json.dumps({'schemaVersion': 1, 'jobId': job,
                        'createdAt': datetime.now(timezone.utc).isoformat(), 'kind': 'action',
                        'target': 'ods-host', 'action': 'ods.extensions.list'}))
                    request_file.chmod(0o640)
                    os.chown(request_file, owner.pw_uid, broker.pw_gid)
                    broker_command = plistlib.loads(broker_definition.read_bytes())['ProgramArguments'] + ['--once']
                    completed = run_as(broker, broker_command)
                    assert completed.returncode == 0, completed.stderr
                    result = json.loads((state / 'results' / (job + '.json')).read_text())
                    assert result['status'] == 'succeeded', result
                    assert json.loads(result['steps'][0]['stdout'])['outcome'] == 'succeeded'
                    assert credential not in json.dumps(result)
                    assert calls == [('/api/extensions/catalog', 'Bearer ' + credential)] * 2
                    # Persistent socket-directory ACLs survive an abrupt stop;
                    # the new owner process replaces only its stale socket.
                    process.kill()
                    process.wait(timeout=5)
                    assert endpoint.is_socket()
                    process = subprocess.Popen([*command, '-c', harness, str(code / 'extension_manager.py'),
                        str(endpoint), str(environment), str(api.server_port)], user=owner.pw_uid,
                        group=owner.pw_gid, extra_groups=[], cwd='/', env=env,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
                    deadline = time.monotonic() + 10
                    while True:
                        response = run_as(broker, [str(python), '-I', '-c', client, str(endpoint)])
                        if response.returncode == 0:
                            assert json.loads(response.stdout)['outcome'] == 'succeeded', response.stdout
                            break
                        assert process.poll() is None and time.monotonic() < deadline, (logs / 'stderr').read_text()
                        time.sleep(0.05)
                    assert credential not in response.stdout
                    assert calls == [('/api/extensions/catalog', 'Bearer ' + credential)] * 3
                finally:
                    process.terminate()
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
    finally:
        api.shutdown()
        api.server_close()
        thread.join(timeout=5)
