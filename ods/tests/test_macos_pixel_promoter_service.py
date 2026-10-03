import importlib.util
from contextlib import nullcontext
from pathlib import Path
import plistlib
import hashlib
import json
import os
import pwd
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('native_promoter',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-promoter-service.py')
promoter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(promoter)


@pytest.fixture
def arguments():
    return dict(python='/Library/Developer/CommandLineTools/usr/bin/python3',
                program_root='/usr/local/libexec/ods-pixel-promoter',
                workspace='/Users/fixture/Pixel Workspace', owner='fixture')


def test_root_service_has_only_local_transport_and_no_children(arguments):
    result = promoter.render(**arguments)
    document = plistlib.loads(result['plist'])
    assert document['UserName'] == 'root'
    assert document['ProgramArguments'][:2] == ['/usr/bin/env', '-i']
    assert '-I' in document['ProgramArguments']
    profile = result['profile'].decode()
    assert '(deny default)' in profile and '(deny process-fork)' in profile
    assert '(allow network*)' not in profile and '(remote' not in profile
    assert '(allow network-bind network-inbound network-outbound (literal "/private/var/lib/ods-pixel-artifact-promoter/promoter.sock"))' in profile
    assert '(deny file-write* (subpath "/usr/local/libexec/ods-pixel-promoter") (subpath "/private/var/lib/pixel-ops-broker"))' in profile
    assert '/private/var/lib/pixel-ops-broker/approvals' not in profile


@pytest.mark.parametrize('fault', ['root-owner', 'owner-injection', 'relative', 'overlap', 'code-workspace', 'python-workspace'])
def test_unsafe_profiles_are_refused(arguments, fault):
    if fault == 'root-owner': arguments['owner'] = 'root'
    if fault == 'owner-injection': arguments['owner'] = 'someone --root'
    if fault == 'relative': arguments['workspace'] = '../workspace'
    if fault == 'overlap': arguments['workspace'] = '/private/var/lib/pixel-ops-broker/workspace'
    if fault == 'code-workspace': arguments['workspace'] = arguments['program_root']
    if fault == 'python-workspace': arguments['workspace'] = '/Library/Developer'
    with pytest.raises(ValueError): promoter.render(**arguments)


@pytest.fixture
def publication(arguments, monkeypatch):
    args = dict(arguments, sources={'artifact_promoter.py': b'"""fixture"""\n', 'unix_peer.py': b'"""peer"""\n',
                                   'pixel_macos_custody.py': b'"""custody"""\n'},
                definition='/Library/LaunchDaemons/com.ods.pixel-native-promoter.plist')
    args['expected_sha256'] = {name: hashlib.sha256(body).hexdigest() for name, body in args['sources'].items()}
    events = []
    monkeypatch.setattr(promoter.sys, 'platform', 'darwin')
    monkeypatch.setattr(promoter.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(promoter.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=501))
    installer = SimpleNamespace(
        _preflight_file=lambda path, body, **kw: events.append(('preflight', path)),
        _write_exact=lambda path, body, **kw: events.append(('write', path)))
    helpers = SimpleNamespace(installer_helpers=lambda: installer,
        custody=SimpleNamespace(protected_bytes=lambda path, **kw: events.append(('python', path)),
                                protected_directory=lambda path, **kw: nullcontext()))
    monkeypatch.setattr(promoter, 'operations_helpers', lambda: helpers)
    return args, events, installer


def test_promoter_plan_is_read_only(publication, monkeypatch):
    args, events, _ = publication
    def unexpected(*args, **kwargs): pytest.fail('planning created logs')
    monkeypatch.setattr(promoter.operations_helpers().custody, 'protected_directory', unexpected)
    files = promoter.publication_files(**args)
    assert len(files) == 5
    assert [item[0] for item in events] == ['python']
    assert files[-1][0] == Path(args['definition'])
    assert all(mode == 0o644 and gid == 0 for _, _, mode, gid in files)


def test_preflights_all_sources_before_writing_and_publishes_definition_last(publication):
    args, events, _ = publication
    assert promoter.publish(**args) == Path(args['definition'])
    assert [item[0] for item in events] == ['python'] + ['preflight'] * 5 + ['write'] * 5
    assert events[-1][1] == Path(args['definition'])


@pytest.mark.parametrize('fault', ['changed-bytes', 'extra-source', 'syntax', 'root-owner', 'preflight', 'logs'])
def test_invalid_publication_never_writes(publication, monkeypatch, fault):
    args, events, installer = publication
    if fault == 'changed-bytes': args['sources']['unix_peer.py'] = b'changed'
    if fault == 'extra-source': args['sources']['extra.py'] = b'pass'
    if fault == 'syntax':
        args['sources']['unix_peer.py'] = b'not valid python!'
        args['expected_sha256']['unix_peer.py'] = hashlib.sha256(args['sources']['unix_peer.py']).hexdigest()
    if fault == 'root-owner': monkeypatch.setattr(promoter.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=0))
    if fault == 'preflight':
        def fail(*args, **kwargs): raise ValueError('conflicting destination')
        installer._preflight_file = fail
    if fault == 'logs':
        def fail(*args, **kwargs): raise ValueError('untrusted log directory')
        monkeypatch.setattr(promoter.operations_helpers().custody, 'protected_directory', fail)
    with pytest.raises((ValueError, SyntaxError)): promoter.publish(**args)
    assert not any(item[0] == 'write' for item in events)


@pytest.mark.skipif(sys.platform != 'darwin' or os.geteuid() != 0 or
    os.environ.get('ODS_TEST_PROMOTER_LIVE') != '1', reason='explicit root promoter confinement test')
def test_real_root_promoter_profile_and_owner_rpc():
    owner = pwd.getpwuid(int(os.environ['SUDO_UID']))
    broker = pwd.getpwnam('_ods_pixel_ops')
    host = Path(__file__).resolve().parents[1] / 'extensions/services/pixel-agent/host'
    spec = importlib.util.spec_from_file_location('promoter_python_selection',
        Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-ops-service.py')
    operations = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operations)
    python = operations.select_python()
    with tempfile.TemporaryDirectory(prefix='ods-promoter-', dir='/private/var/lib') as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        program, workspace, state, runtime, logs = [root / name for name in ('code', 'workspace', 'state', 'run', 'logs')]
        for directory in (program, workspace, state): directory.mkdir(mode=0o755)
        workspace.chmod(0o700)
        os.chown(workspace, owner.pw_uid, owner.pw_gid)
        os.chown(state, broker.pw_uid, broker.pw_gid)
        state.chmod(0o700)
        for name in ('results', 'artifacts'):
            (state / name).mkdir(mode=0o700)
            os.chown(state / name, broker.pw_uid, broker.pw_gid)
        sources = {name: (host / name).read_bytes() for name in ('artifact_promoter.py', 'unix_peer.py')}
        sources['pixel_macos_custody.py'] = (Path(__file__).resolve().parents[1] / 'bin/pixel_macos_custody.py').read_bytes()
        job = 'ops-1788130169655-22b40ab50141'
        content = b'<html>native exact bytes</html>\n'
        digest = hashlib.sha256(content).hexdigest()
        folder = state / 'artifacts' / job
        folder.mkdir(mode=0o700)
        os.chown(folder, broker.pw_uid, broker.pw_gid)
        artifact = folder / 'page.html'
        artifact.write_bytes(content)
        artifact.chmod(0o600)
        os.chown(artifact, broker.pw_uid, broker.pw_gid)
        receipt = {'schemaVersion': 2, 'jobId': job, 'status': 'succeeded', 'steps': [{
            'target': 'broker', 'action': 'download.stage', 'exitCode': 0, 'artifact': {
                'path': str(artifact), 'filename': 'page.html', 'bytes': len(content),
                'sha256': digest, 'source': 'https://example.com/page.html', 'redirects': [], 'executable': False}}]}
        result_path = state / 'results' / (job + '.json')
        result_path.write_text(json.dumps(receipt))
        result_path.chmod(0o600)
        os.chown(result_path, broker.pw_uid, broker.pw_gid)
        (state / 'approvals').mkdir(mode=0o700)
        (state / 'approvals/secret.json').write_text('{"private":true}')
        arguments = dict(python=python, program_root=program, workspace=workspace,
            owner=owner.pw_name, state=state, runtime=runtime, logs=logs, sources=sources,
            expected_sha256={name: hashlib.sha256(body).hexdigest() for name, body in sources.items()},
            definition=root / 'com.ods.pixel-native-promoter.plist')
        assert promoter.publish(**arguments) == promoter.publish(**arguments)
        profile = program / 'promoter.sb'
        command = ['/usr/bin/sandbox-exec', '-f', str(profile), str(python), '-I', '-B']
        environment = {'PATH': '/usr/bin:/bin', 'HOME': str(runtime), 'TMPDIR': str(runtime)}
        # Only test locations are rebound; the original listener, peer checks,
        # receipt verification and create-only publication execute unchanged.
        harness = ('import importlib.util,sys; from pathlib import Path; '
            's=importlib.util.spec_from_file_location("fixture",sys.argv[1]); '
            'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
            'm.SOCKET_PATH=Path(sys.argv[2]); m.RESULTS_ROOT=Path(sys.argv[3]); '
            'm.ARTIFACTS_ROOT=Path(sys.argv[4]); '
            'm.serve(m.SOCKET_PATH,m.RESULTS_ROOT,m.ARTIFACTS_ROOT,Path(sys.argv[5]),sys.argv[6])')
        endpoint = runtime / 'promoter.sock'
        assert not runtime.exists()
        with (logs / 'fixture.stderr').open('wb') as stderr:
            process = subprocess.Popen([*command, '-c', harness, str(program / 'artifact_promoter.py'),
                str(endpoint), str(state / 'results'), str(state / 'artifacts'), str(workspace), owner.pw_name],
                cwd='/', env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
            try:
                deadline = time.monotonic() + 10
                while not endpoint.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.1)
                assert endpoint.exists(), (logs / 'fixture.stderr').read_text()
                assert runtime.stat().st_uid == 0 and runtime.stat().st_gid == owner.pw_gid
                assert runtime.stat().st_mode & 0o777 == 0o710
                client = ('import socket,sys; s=socket.socket(socket.AF_UNIX); s.settimeout(10); '
                    's.connect(sys.argv[1]); s.sendall((sys.argv[2]+"\\n").encode()); '
                    'print(s.makefile().readline())')
                request = {'schemaVersion': 1, 'action': 'promote', 'jobId': job, 'filename': 'page.html',
                    'relativePath': 'nested/page.html', 'sha256': digest, 'sourceUrl': 'https://example.com/page.html'}
                def rpc():
                    response = subprocess.run([str(python), '-I', '-c', client, str(endpoint), json.dumps(request)],
                        user=owner.pw_uid, group=owner.pw_gid, extra_groups=[], cwd='/',
                        env=environment, capture_output=True, text=True, timeout=15)
                    assert response.returncode == 0, response.stderr
                    return json.loads(response.stdout)
                assert rpc()['status'] == 'succeeded'
                saved = workspace / 'nested/page.html'
                assert saved.read_bytes() == content
                assert saved.stat().st_uid == owner.pw_uid and saved.stat().st_mode & 0o777 == 0o600
                assert rpc()['status'] != 'succeeded'
                assert saved.read_bytes() == content
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1', 0))
                    listener.listen()
                    probe = ('import socket,sys\ntry:\n socket.create_connection(("127.0.0.1",int(sys.argv[1])),1)\n'
                             'except PermissionError:\n print("denied")\nelse:\n raise SystemExit("network allowed")')
                    denied = subprocess.run([*command, '-c', probe, str(listener.getsockname()[1])],
                        cwd='/', env=environment, capture_output=True, text=True, timeout=5)
                    assert denied.returncode == 0 and denied.stdout.strip() == 'denied', denied.stderr
                for operation in (
                    'open(' + repr(str(state / 'approvals/secret.json')) + ').read()',
                    'open(' + repr(str(artifact)) + ', "wb").write(b"altered")',
                    'subprocess.run(["/usr/bin/true"], check=True)',
                ):
                    probe = ('import subprocess\ntry:\n ' + operation + '\n'
                             'except PermissionError:\n print("denied")\nelse:\n raise SystemExit("boundary escaped")')
                    denied = subprocess.run([*command, '-c', probe], cwd='/', env=environment,
                        capture_output=True, text=True, timeout=5)
                    assert denied.returncode == 0 and denied.stdout.strip() == 'denied', denied.stderr
                assert artifact.read_bytes() == content
                process.terminate()
                process.wait(timeout=10)
                endpoint.unlink()
                runtime.rmdir()
                process = subprocess.Popen([*command, '-c', harness, str(program / 'artifact_promoter.py'),
                    str(endpoint), str(state / 'results'), str(state / 'artifacts'), str(workspace), owner.pw_name],
                    cwd='/', env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
                deadline = time.monotonic() + 10
                while not endpoint.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.1)
                assert endpoint.exists(), (logs / 'fixture.stderr').read_text()
                request = {'schemaVersion': 1, 'action': 'health'}
                assert rpc()['status'] == 'ok'
                assert saved.read_bytes() == content
            finally:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
