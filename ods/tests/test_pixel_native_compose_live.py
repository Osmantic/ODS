"""Opt-in isolated Compose activation; no production services or model calls."""
import http.server
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import urllib.request
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_compose', ROOT / 'installers/macos/lib/pixel-native-compose.py')
native_compose = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native_compose)
pytestmark = pytest.mark.skipif(sys.platform != 'darwin' or
    os.environ.get('ODS_TEST_NATIVE_COMPOSE_LIVE') != '1', reason='explicit native Docker activation qualification')


def test_legacy_retention_and_restore_keep_the_same_real_container(monkeypatch):
    name = 'ods-native-retention-proof-' + uuid.uuid4().hex[:12]
    monkeypatch.setattr(native_compose, 'INGRESS_NAME', name)
    image = os.environ['ODS_TEST_INGRESS_IMAGE']
    user = str(os.getuid()) + ':' + str(os.getgid())
    def run(*args, timeout=45):
        return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
    created = run('create', '--name', name, '--network', 'none', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--user', user,
        '--entrypoint', 'node', image, '-e', 'setInterval(()=>{},1000)')
    assert created.returncode == 0, created.stderr
    identity = created.stdout.strip()
    checkpoints = []
    try:
        assert run('start', identity).returncode == 0
        retained = native_compose.retain_legacy_ingress(run, identity=identity, image=image,
            user=user, project=name, transaction=hashlib.sha256(name.encode()).hexdigest(),
            checkpoint=lambda value: checkpoints.append(dict(value)))
        stopped = native_compose._inspect_ingress(run, identity)
        assert stopped['Name'] == '/' + retained['backup']
        assert stopped['State']['Running'] is False
        # Recover from the durable pre-rename checkpoint as after an interruption.
        restored = native_compose.restore_legacy_ingress(run, checkpoints[1],
            checkpoint=lambda value: checkpoints.append(dict(value)))
        assert restored['requiresRecovery'] is False
        current = native_compose._inspect_ingress(run, identity)
        assert current['Name'] == '/' + name and current['State']['Running'] is True
        assert [value['phase'] for value in checkpoints] == [
            'stopping-legacy', 'renaming-legacy', 'legacy-retained', 'restoring-legacy', 'restored']
    finally:
        # This ID was created by this test, never selected from a production name.
        removed = run('rm', '-f', identity)
        assert removed.returncode == 0, removed.stderr


def test_native_compose_services_start_and_reach_host(tmp_path, monkeypatch):
    class Gateway(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        def log_message(self, *args): pass
    gateway = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Gateway, bind_and_activate=False)
    gateway.server_bind()
    thread = threading.Thread(target=gateway.serve_forever)
    project = 'ods-native-proof-' + uuid.uuid4().hex[:10]
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'index.html').write_text('<!doctype html><title>Native preview proof</title>')
    project_dir = workspace / 'projects' / 'html-proof'
    project_dir.mkdir(parents=True)
    html = b'<!doctype html><title>Published from native workspace</title><h1>Ready</h1>'
    (project_dir / 'index.html').write_bytes(html)
    config = tmp_path / 'gateway.json'
    config.write_text(json.dumps({'gateway': {'auth': {'token': 'fixture-only-token'}}}))
    config.chmod(0o600)
    with socket.socket() as port:
        port.bind(('127.0.0.1', 0))
        preview_port = port.getsockname()[1]
    base = tmp_path / 'base.json'
    base.write_text(json.dumps({'services': {name: {'image': 'busybox:1.36.1'}
        for name in ('open-webui', 'dashboard-api')}}))
    override = tmp_path / 'override.json'
    override.write_text(json.dumps({'services': {
        'pixel-edge': {'container_name': project + '-edge', 'image': 'ods-pixel-edge:compose-test'},
        'pixel-workspace-preview': {'container_name': project + '-preview', 'image': 'ods-pixel-preview:compose-test'}}}))
    env = {key: value for key, value in os.environ.items() if not key.startswith(('PIXEL_', 'COMPOSE_'))}
    env.update(PIXEL_NATIVE_UID=str(os.getuid()), PIXEL_INGRESS_GID=str(os.getgid()),
        PIXEL_NATIVE_INGRESS_IMAGE=os.environ['ODS_TEST_INGRESS_IMAGE'],
        PIXEL_NATIVE_CONFIG_PATH=str(config), PIXEL_NATIVE_WORKSPACE=str(workspace),
        PIXEL_NATIVE_GATEWAY_PORT=str(gateway.server_port), PIXEL_PREVIEW_PORT=str(preview_port),
        PIXEL_OPENWEBUI_KEY='a' * 64, DASHBOARD_API_KEY='b' * 64,
        PIXEL_INGRESS_RUNTIME_DIR=str(tmp_path / 'unused-runtime'),
        PIXEL_PREVIEW_RUNTIME_DIR=str(tmp_path / 'unused-preview'))
    command = ['docker', 'compose', '--project-name', project, '--project-directory', str(ROOT),
        '--env-file', '/dev/null', '-f', str(base), '-f', str(ROOT / 'extensions/services/pixel-edge/compose.yaml.disabled'),
        '-f', str(ROOT / 'installers/macos/pixel-native.compose.yaml.disabled'), '-f', str(override)]
    def run(*args, timeout=60, input=None):
        return subprocess.run([*command, *args], env=env, capture_output=True, text=True, timeout=timeout, input=input)
    def docker_run(*args, timeout=60, input=None):
        return subprocess.run(['docker', *args], env=env, capture_output=True, text=True,
            timeout=timeout, input=input)
    original_named = native_compose._named_ingress
    monkeypatch.setattr(native_compose, '_named_ingress', lambda runner, name:
        original_named(runner, project + '-edge' if name == 'ods-pixel-edge' else name))
    legacy_identity = None
    try:
        phase = native_compose.start_infrastructure(run, dashboard_key='b' * 64)
        assert phase == {'phase': 'infrastructure-ready', 'requiresGatewayActivation': True}
        result = run('exec', '-T', 'pixel-edge', 'python3', '-c', native_compose.TRANSITION_PROBE,
            input=json.dumps({'key': 'b' * 64}))
        assert result.returncode == 0, result.stderr
        binding = {'revision': json.loads(result.stdout)['revision'],
            'token': hashlib.sha256(project.encode()).hexdigest()}
        payload = json.dumps({'key': 'b' * 64, 'binding': binding})
        result = run('exec', '-T', 'pixel-edge', 'python3', '-c', native_compose.TRANSITION_PROBE,
            input=payload)
        assert result.returncode == 0 and json.loads(result.stdout)['phase'] == 'held'
        result = run('up', '-d', '--force-recreate', '--no-deps', 'pixel-edge')
        assert result.returncode == 0, result.stderr
        # The same persisted token must survive replacement without admitting chat.
        assert native_compose.start_infrastructure(run, dashboard_key='b' * 64,
            admission=binding)['admissionHeld'] is True
        # Admission control must work before any native gateway is listening.
        result = run('exec', '-T', 'pixel-edge', 'python3', '-c',
            'import urllib.request,urllib.error; '
            '\ntry: urllib.request.urlopen("http://127.0.0.1:9595/health",timeout=5)'
            '\nexcept urllib.error.HTTPError as e: assert e.code==503'
            '\nelse: raise AssertionError("Gateway unexpectedly ready")')
        assert result.returncode == 0, result.stdout + result.stderr
        gateway.server_activate()
        thread.start()
        assert native_compose.wait_ready(run) == {'phase': 'docker-ready'}
        handover = {'phase': 'infrastructure-ready-held', 'requiresRecovery': True,
            'project': project, 'edge': native_compose._managed_edge(docker_run, project), 'binding': binding}
        release_journal = []
        finished = native_compose.finish_migration_infrastructure(run, docker_run, handover,
            dashboard_key='b' * 64, checkpoint=lambda value: release_journal.append(dict(value)))
        assert finished['phase'] == 'infrastructure-ready'
        assert finished['requiresRecovery'] is False
        # Replaying the pre-release journal models a successful release whose reply
        # was lost. It must not acquire another token or conflict with the revision.
        recovered = native_compose.finish_migration_infrastructure(run, docker_run, release_journal[0],
            dashboard_key='b' * 64, checkpoint=lambda value: release_journal.append(dict(value)))
        assert recovered['phase'] == 'infrastructure-ready'
        result = run('exec', '-T', 'pixel-native-ingress', 'node', '-e',
            "require('http').get({socketPath:'/runtime/pixel-ingress.sock',path:'/health'},r=>{let b='';r.on('data',x=>b+=x);r.on('end',()=>{console.log(b);process.exit(r.statusCode===200?0:1)})}).on('error',()=>process.exit(1))")
        assert result.returncode == 0, result.stdout + result.stderr
        result = run('exec', '-T', 'pixel-edge', 'python3', '-c',
            'import json,urllib.request; request=urllib.request.Request("http://127.0.0.1:9595/v1/models",'
            'headers={"Authorization":"Bearer ' + 'a' * 64 + '"}); '
            'value=json.load(urllib.request.urlopen(request,timeout=10)); '
            'assert any(item["id"]=="pixel/default" for item in value["data"]); print("models-ok")')
        assert result.returncode == 0, result.stdout + result.stderr
        assert (workspace / 'index.html').read_text().startswith('<!doctype html>')
        result = run('exec', '-T', 'pixel-workspace-preview', 'python3', '/source/workspace_preview.py', 'request',
            input=json.dumps({'schemaVersion': 1, 'action': 'publish', 'relativeDirectory': 'projects/html-proof'}) + '\n')
        assert result.returncode == 0, result.stdout + result.stderr
        publication = json.loads(result.stdout)
        assert publication.get('readbackVerified') is True, publication
        assert publication['entrySha256'] == hashlib.sha256(html).hexdigest()
        site = publication['siteId']
        request = urllib.request.Request(f'http://127.0.0.1:{preview_port}/{site}/',
            headers={'Host': f'{site}.localhost:{preview_port}'})
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200 and response.read() == html
        (project_dir / 'index.html').write_text('<!doctype html><title>Unpublished edit</title>')
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.read() == html
        # Exercise the full Docker writer handover and rollback using this test's
        # own legacy container and volumes, never production names or data.
        monkeypatch.setattr(native_compose, 'INGRESS_NAME', project + '-legacy')
        monkeypatch.setattr(native_compose, 'EDGE_NAME', project + '-edge')
        monkeypatch.setattr(native_compose, 'PREVIEW_NAME', project + '-preview')
        result = run('ps', '-q', 'pixel-native-ingress')
        assert result.returncode == 0
        initial_ingress = native_compose._inspect_ingress(docker_run, result.stdout.strip())
        runtime_volume = next(value['Name'] for value in initial_ingress['Mounts'] if value['Destination'] == '/runtime')
        assert run('stop', 'pixel-native-ingress').returncode == 0
        assert docker_run('rm', initial_ingress['Id']).returncode == 0
        args = ['create', '--name', project + '-legacy', '--read-only', '--cap-drop', 'ALL',
            '--user', str(os.getuid()) + ':' + str(os.getgid()), '--network', project + '_default',
            '--volume', runtime_volume + ':/runtime', '--volume', str(config) + ':/run/gateway.json:ro',
            '--volume', str(ROOT / 'extensions/services/pixel-agent/host') + ':/source:ro']
        legacy_env = {'PIXEL_GATEWAY_TRANSPORT': 'docker-desktop-host',
            'PIXEL_GATEWAY_PORT': str(gateway.server_port), 'PIXEL_GATEWAY_TOKEN_FILE': '/run/gateway.json',
            'PIXEL_INGRESS_SOCKET': '/runtime/pixel-ingress.sock', 'PIXEL_STATUS_FILE': '/runtime/status.json',
            'PIXEL_CHAT_STATE_DIR': '/runtime/history', 'PIXEL_INGRESS_GID': str(os.getgid())}
        for key, value in legacy_env.items(): args.extend(['--env', key + '=' + value])
        args.extend(['--entrypoint', 'node', env['PIXEL_NATIVE_INGRESS_IMAGE'], '/source/pixel_ingress.mjs'])
        created = docker_run(*args)
        assert created.returncode == 0, created.stderr
        legacy_identity = created.stdout.strip()
        assert docker_run('start', legacy_identity).returncode == 0
        preview_identity = native_compose._named_ingress(docker_run, project + '-preview')
        edge_identity = native_compose._managed_edge(docker_run, project)
        storage = native_compose.legacy_storage_override(
            ingress=native_compose._inspect_ingress(docker_run, legacy_identity),
            preview=native_compose._inspect_ingress(docker_run, preview_identity),
            edge=native_compose._inspect_ingress(docker_run, edge_identity),
            project=project, workspace=workspace)
        journal = []
        migrated = native_compose.migrate_infrastructure(run, docker_run, ingress=legacy_identity,
            image=env['PIXEL_NATIVE_INGRESS_IMAGE'], user=str(os.getuid()) + ':' + str(os.getgid()),
            project=project, transaction=hashlib.sha256((project + '-migration').encode()).hexdigest(),
            storage=storage, workspace=workspace, dashboard_key='b' * 64,
            checkpoint=lambda value: journal.append(json.loads(json.dumps(value))))
        assert migrated['phase'] == 'infrastructure-ready-held'
        recovered = native_compose.restore_migration_infrastructure(run, docker_run, migrated,
            dashboard_key='b' * 64, checkpoint=lambda value: journal.append(json.loads(json.dumps(value))))
        assert recovered['phase'] == 'infrastructure-rolled-back'
        assert native_compose._inspect_ingress(docker_run, legacy_identity)['State']['Running'] is True
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.read() == html
    finally:
        if legacy_identity is not None:
            removed = docker_run('rm', '-f', legacy_identity)
            assert removed.returncode == 0, removed.stderr
        cleanup = run('down', '--volumes', '--remove-orphans', timeout=90)
        if thread.is_alive(): gateway.shutdown()
        gateway.server_close()
        if thread.ident is not None: thread.join(timeout=5)
        assert cleanup.returncode == 0, cleanup.stderr
