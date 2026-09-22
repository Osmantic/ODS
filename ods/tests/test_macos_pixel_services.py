import importlib.util
import hashlib
import http.server
import json
import os
import plistlib
from pathlib import Path
import pwd
import sys
import socket
import subprocess
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('native_services',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-services.py')
services = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(services)


@pytest.mark.parametrize('planning', [False, True])
@pytest.mark.parametrize('fault', [None, 'owner', 'bundle', 'render', 'plan', 'duplicate', 'preflight', 'publish'])
def test_joint_publication_verifies_before_writes_and_never_starts_jobs(monkeypatch, fault, planning):
    if planning and fault in ('preflight', 'publish'):
        pytest.skip('planning does not invoke publication')
    monkeypatch.setattr(services.sys, 'platform', 'darwin')
    monkeypatch.setattr(services.os, 'geteuid', lambda: 501 if fault == 'owner' else 0)
    calls = []
    snapshots = {'operations/broker.py': b'broker', 'operations/policy.json': b'{"schemaVersion":1}',
        'manager/extension_manager.py': b'manager', 'manager/unix_peer.py': b'peer',
        'promoter/artifact_promoter.py': b'promoter', 'promoter/unix_peer.py': b'peer',
        'promoter/pixel_macos_custody.py': b'custody', 'helpers/example.py': b'helper'}
    def verify(path, **kwargs):
        calls.append('verify')
        assert kwargs == dict(expected_digest='a' * 64, expected_ref='b' * 40, expected_config_digest='c' * 64)
        if fault == 'bundle': raise ValueError('tampered')
        return snapshots
    def module(name):
        if name == 'config': return SimpleNamespace(verified_services=verify)
        def render(**kwargs):
            calls.append('render:' + name)
            if fault == 'render' and name == 'promoter-service': raise ValueError('invalid paths')
        def publish(**kwargs):
            calls.append('publish:' + name)
            if fault == 'publish': raise ValueError('existing file conflict')
            if name == 'ops-service':
                assert kwargs['broker_body'] == snapshots['operations/broker.py']
            else:
                prefix = name.removesuffix('-service')
                assert kwargs['sources'] == {p.split('/', 1)[1]: b for p, b in snapshots.items() if p.startswith(prefix + '/')}
            return kwargs['definition']
        def publication_files(**kwargs):
            calls.append('plan:' + name)
            if fault == 'plan' and name == 'ops-service': raise ValueError('invalid policy')
            path = kwargs['definition']
            if fault == 'duplicate': path = Path('/duplicate')
            return [(path, b'planned', 0o644, 0)]
        def preflight(*args, **kwargs):
            calls.append('preflight')
            if fault == 'preflight': raise ValueError('existing file conflict')
        installer = SimpleNamespace(_preflight_file=preflight,
            _write_exact=lambda *a, **k: calls.append('write-helper'))
        return SimpleNamespace(render=render, publish=publish, publication_files=publication_files,
            installer_helpers=lambda: installer)
    monkeypatch.setattr(services, 'helper', module)
    def run():
        method = services.publication_files if planning else services.publish
        return method(bundle='/candidate', expected_digest='a' * 64, expected_ref='b' * 40,
            expected_config_digest='c' * 64, identity={'name': '_ods_pixel_ops'}, owner='fixture',
            environment='/owner/.env', workspace='/owner/workspace', port=3002, python='/python')
    if fault:
        with pytest.raises(ValueError): run()
        if fault == 'owner': assert not calls
        if fault in ('bundle', 'render', 'plan', 'duplicate', 'preflight'):
            assert not any(c.startswith('publish:') or c == 'write-helper' for c in calls)
        if fault == 'publish': assert calls[-1] == 'publish:manager-service'
    else:
        result = run()
        expected = ['verify', 'render:ops-service', 'render:manager-service',
            'render:promoter-service', 'plan:manager-service', 'plan:promoter-service', 'plan:ops-service']
        if planning:
            assert len(result) == 4
            assert result[0] == (Path('/usr/local/libexec/ods-pixel-services/helpers/example.py'), b'helper', 0o644, 0)
        else:
            assert set(result) == {'manager', 'promoter', 'operations'}
            expected += ['preflight'] * 4 + ['write-helper', 'publish:manager-service',
                'publish:promoter-service', 'publish:ops-service']
        assert calls == expected


@pytest.mark.skipif(sys.platform != 'darwin' or os.geteuid() != 0 or
    os.environ.get('ODS_TEST_SERVICES_LIVE') != '1', reason='explicit root joint publication qualification')
def test_real_joint_publication_and_replay_without_loading_jobs(dashboard, monkeypatch):
    ods = Path(__file__).resolve().parents[1]
    config = services.helper('config')
    owner = pwd.getpwuid(int(os.environ['SUDO_UID']))
    broker = pwd.getpwnam('_ods_pixel_ops')
    source = Path(os.environ['ODS_TEST_PIXEL_SOURCE'])
    python = services.helper('ops-service').select_python()
    with tempfile.TemporaryDirectory(prefix='ods-services-', dir='/private/var/lib') as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        state = services.helper('ops-state').provision(state=root / 'state', gateway_uid=owner.pw_uid,
            broker_uid=broker.pw_uid, broker_gid=broker.pw_gid)
        bundle = root / 'bundle'
        bundle.mkdir(mode=0o700)
        files = {name: (ods / path).read_bytes() for name, path in config.SERVICE_SOURCES.items()}
        files['operations/broker.py'] = (source / 'deploy/ops-broker/broker.py').read_bytes()
        helper_root = root / 'programs/helpers'
        search_harness = ('import importlib.util,sys; from pathlib import Path; '
            's=importlib.util.spec_from_file_location("search",sys.argv[1]); '
            'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
            'm.CATALOG_PATH=Path(sys.argv[2]); m.main(sys.argv[2:])')
        commands = {
            'extensions.search': [str(python), '-I', '-B', '-c', search_harness, str(helper_root / 'extension_search.py'),
                str(helper_root / 'extension-catalog.json'), 'all'],
            'host.os-release': ['/usr/bin/python3', str(helper_root / 'system_observe.py'), 'os-release']}
        policy = {'schemaVersion': 2, 'targets': {'ods-host': {'enabled': True, 'backend': 'local',
            'environment': 'lab', 'expectedHostname': socket.gethostname(), 'defaultCwd': str(state),
            'allowedRoots': [str(state), str(helper_root)], 'allowRaw': False}},
            'actions': {name: {'description': name, 'tier': 'read', 'effect': 'observe',
                'defaultAuthority': 'observe', 'idempotent': True, 'reversible': False,
                'targets': ['ods-host'], 'argv': argv} for name, argv in commands.items()},
            'authority': {'defaultLevel': 'propose', 'grants': []}}
        files['operations/policy.json'] = json.dumps(policy).encode()
        entry = dict(id='fixture', name='Fixture', description='Test extension', category='tools',
            gpuBackends=[], dependsOn=[], requiredConfiguration=[], optionalConfiguration=[], tags=[], featureNames=[])
        files['helpers/extension-catalog.json'] = json.dumps({'schemaVersion': 1,
            'kind': 'ods-pixel-extension-catalog', 'sourceSha256': 'd' * 64, 'extensions': [entry]}).encode()
        manifest = {'schemaVersion': 1, 'status': 'staged', 'requiresServiceQualification': True,
            'pixelSourceRef': 'a' * 40, 'candidateConfigSha256': 'b' * 64,
            'files': {name: {'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}
                for name, body in files.items()}}
        # Synthetic approved transaction: this exercises publication, not source acquisition.
        manifest_body = json.dumps(manifest).encode()
        for name, body in {**files, 'services.json': manifest_body}.items():
            path = bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            path.chmod(0o600)
        environment = root / 'ods.env'
        environment.write_text('DASHBOARD_API_KEY=' + 'd' * 64 + '\n')
        environment.chmod(0o600)
        os.chown(environment, owner.pw_uid, owner.pw_gid)
        workspace = root / 'workspace'
        workspace.mkdir(mode=0o700)
        os.chown(workspace, owner.pw_uid, owner.pw_gid)
        arguments = dict(bundle=bundle, expected_digest=hashlib.sha256(manifest_body).hexdigest(),
            expected_ref='a' * 40, expected_config_digest='b' * 64,
            identity={'name': broker.pw_name, 'uid': broker.pw_uid, 'gid': broker.pw_gid},
            owner=owner.pw_name, environment=environment, workspace=workspace, port=dashboard,
            python=python, program_root=root / 'programs', definitions=root / 'definitions', state=state)
        planned = services.publication_files(**arguments)
        assert not (root / 'programs').exists()
        assert not (root / 'definitions').exists()
        result = services.publish(**arguments)
        for path, body, mode, gid in planned:
            assert path.read_bytes() == body
            info = path.stat()
            assert info.st_mode & 0o777 == mode
            assert info.st_uid == 0 and info.st_gid == gid
        assert result == services.publish(**arguments)
        assert set(result) == {'manager', 'promoter', 'operations'}
        for path in result.values():
            assert path.is_file() and path.stat().st_uid == 0
        if os.environ.get('ODS_TEST_SERVICE_ACTIVATION_LIVE') == '1':
            # Only rebind the promoter's fixed spool roots to this temporary
            # fixture; production still accepts solely its fixed state paths.
            promoter = plistlib.loads(result['promoter'].read_bytes())
            argv = promoter['ProgramArguments']
            offset = argv.index(str(python)) + 3
            harness = ('import importlib.util,sys; from pathlib import Path; '
                's=importlib.util.spec_from_file_location("promoter",sys.argv[1]); '
                'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                'm.RESULTS_ROOT=Path(sys.argv[4]); m.ARTIFACTS_ROOT=Path(sys.argv[5]); '
                'raise SystemExit(m.serve(Path(sys.argv[3]),Path(sys.argv[4]),Path(sys.argv[5]),Path(sys.argv[6]),sys.argv[7]))')
            promoter['ProgramArguments'] = argv[:offset] + ['-c', harness] + argv[offset:]
            result['promoter'].write_bytes(plistlib.dumps(promoter))
        expected = {name: path.read_bytes() for name, path in result.items()}
        def save_stop(name, value):
            from pixel_access_bridge import atomic_json
            atomic_json(root / ('stop-' + name + '.json'), value)
        def load_stop(name):
            from pixel_access_bridge import private_json
            return private_json(root / ('stop-' + name + '.json'), 0, 1024 * 1024)
        adapters = services.activation_adapters(definitions=result, expected=expected, owner=owner.pw_name,
            identity=arguments['identity'], python=python, save_stop=save_stop, load_stop=load_stop)
        assert adapters['manager'].process == {'uid': owner.pw_uid, 'gid': owner.pw_gid, 'executable': str(python)}
        assert adapters['promoter'].process['uid'] == 0
        assert adapters['operations'].process['uid'] == broker.pw_uid
        for adapter in adapters.values(): adapter.verify_definition()
        original = result['manager'].read_bytes()
        result['manager'].write_bytes(original + b'\n')
        with pytest.raises(ValueError, match='definition-changed'):
            adapters['manager'].verify_definition()
        result['manager'].write_bytes(original)
        for name, body in files.items():
            assert (root / 'programs' / name).read_bytes() == body
        catalog = helper_root / 'extension-catalog.json'
        assert catalog.stat().st_mode & 0o777 == 0o640
        assert catalog.stat().st_gid == broker.pw_gid
        command = plistlib.loads(result['operations'].read_bytes())['ProgramArguments'] + ['--once']
        for action in commands:
            probe = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(root / 'programs/operations/broker.sb'),
                *commands[action]], user=broker.pw_uid, group=broker.pw_gid, extra_groups=[],
                cwd=state, env={'PATH': '/usr/bin:/bin', 'TMPDIR': str(state / 'runtime')}, capture_output=True, text=True, timeout=30)
            assert probe.returncode == 0, probe.stderr + probe.stdout
            job = 'ops-' + str(int(datetime.now().timestamp() * 1000)) + '-' + uuid.uuid4().hex[:12]
            request = state / 'requests' / (job + '.json')
            request.write_text(json.dumps({'schemaVersion': 1, 'jobId': job,
                'createdAt': datetime.now(timezone.utc).isoformat(), 'kind': 'action',
                'target': 'ods-host', 'action': action}))
            request.chmod(0o640)
            os.chown(request, owner.pw_uid, broker.pw_gid)
            completed = subprocess.run(command, user=broker.pw_uid, group=broker.pw_gid, extra_groups=[],
                cwd='/', env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True, timeout=30)
            assert completed.returncode == 0, completed.stderr
            receipt = json.loads((state / 'results' / (job + '.json')).read_text())
            assert receipt['status'] == 'succeeded', json.dumps(receipt)
            output = json.loads(receipt['steps'][0]['stdout'])
            if action == 'extensions.search': assert 'fixture' in json.dumps(output)
            else: assert output['available'] is True
        if os.environ.get('ODS_TEST_SERVICE_ACTIVATION_LIVE') == '1':
            manager_runtime = Path('/private/var/lib/ods-pixel-manager')
            promoter_runtime = Path('/private/var/lib/ods-pixel-artifact-promoter')
            assert not os.path.lexists(manager_runtime) and not os.path.lexists(promoter_runtime)
            installer = services.helper('ops-service').installer_helpers()
            for adapter in adapters.values():
                result = subprocess.run(['/bin/launchctl', 'print', adapter.target], capture_output=True)
                assert result.returncode == 113
                assert not installer._job_disabled(adapter.target)
            services.helper('ops-state').provision_manager_runtime(runtime=manager_runtime,
                gateway_uid=owner.pw_uid, broker_uid=broker.pw_uid, broker_gid=broker.pw_gid)
            records = []
            ready = services.readiness_checks(owner=owner.pw_name, identity=arguments['identity'],
                python=python, program_root=root / 'programs', state=state)
            try:
                services.activate_new(services=adapters, readiness=ready, checkpoint=records.append)
                assert records[-1]['phase'] == 'services-active'
                for adapter in adapters.values(): adapter.process_identity()
                services.stop_new(services=adapters, attempted=['manager', 'promoter', 'operations'],
                    checkpoint=records.append)
                assert records[-1]['phase'] == 'services-stopped'
                assert records[-1]['stopUnconfirmed'] == []
                for adapter in adapters.values():
                    adapter.assert_stopped()
                    assert installer._job_disabled(adapter.target)
                recovery = {'schemaVersion': 1, 'owner': owner.pw_name,
                    'identity': arguments['identity'], 'python': str(python),
                    'definitions': {name: {'path': str(adapter.plist),
                        'body': services.base64.b64encode(expected[name]).decode('ascii')}
                        for name, adapter in adapters.items()}}
                recovered = services.recovery_adapters(recovery, owner=owner.pw_name,
                    save_stop=save_stop, load_stop=load_stop)
                services.stop_new(services=recovered, attempted=['manager', 'promoter', 'operations'],
                    checkpoint=records.append)
                assert records[-1]['stopUnconfirmed'] == []
                from pixel_access_bridge import atomic_json, private_json
                monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', root)
                selection = {'fixture': 'approved-service-selection'}
                journal = root / 'service-installation.json'
                atomic_json(journal, {'schemaVersion': 1, 'owner': owner.pw_uid, 'selection': selection,
                    'recovery': recovery, 'attempted': ['manager', 'promoter', 'operations'],
                    'stopWitnesses': {name: load_stop(name) for name in adapters}})
                installer._restore_new_services({'owner': owner, 'native_services': selection})
                restored = private_json(journal, 0, 2 * 1024 * 1024)
                assert restored['progress']['phase'] == 'services-stopped'
                assert restored['progress']['stopUnconfirmed'] == []
            finally:
                for name in ('operations', 'promoter', 'manager'):
                    adapter = adapters[name]
                    state = subprocess.run(['/bin/launchctl', 'print', adapter.target], capture_output=True)
                    if state.returncode == 0:
                        if adapter.pid() == 0:
                            adapter.verify()
                            subprocess.run(['/bin/launchctl', 'bootout', adapter.target], check=True)
                        else:
                            adapter.stop()
                            adapter.assert_stopped()
                    else: assert state.returncode == 113
                    if any(record.get('service') == name for record in records):
                        subprocess.run(['/bin/launchctl', 'enable', adapter.target], check=True)
                shutil.rmtree(manager_runtime)
                if promoter_runtime.exists(): shutil.rmtree(promoter_runtime)


@pytest.fixture
def dashboard():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == '/api/extensions/catalog'
            assert self.headers.get('Authorization') == 'Bearer ' + 'd' * 64
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"extensions":[]}')
        def log_message(self, *args): pass
    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try: yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
