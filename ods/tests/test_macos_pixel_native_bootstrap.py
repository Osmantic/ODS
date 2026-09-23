import base64
import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('native_bootstrap',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-bootstrap.py')
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


@pytest.mark.parametrize('tampered', [False, True])
def test_public_ods_bundle_acquires_without_private_repository(tmp_path, monkeypatch, tampered):
    monkeypatch.setattr(bootstrap.sys, 'platform', 'darwin')
    monkeypatch.setattr(bootstrap.os, 'geteuid', lambda: 501)
    bundle = tmp_path / 'pixel.bundle'
    shutil.copy2(Path(__file__).resolve().parents[1] / 'vendor/pixel.bundle', bundle)
    if tampered:
        with bundle.open('ab') as handle:
            handle.write(b'changed')
    destination = tmp_path / 'source'
    if tampered:
        with pytest.raises(bootstrap.BootstrapError, match='bundled-pixel-source-digest-mismatch'):
            bootstrap.acquire_source(ref=bootstrap.ODS_BUNDLED_REF,
                destination=destination, source_url=str(bundle))
        assert not destination.exists()
    else:
        assert bootstrap.acquire_source(ref=bootstrap.ODS_BUNDLED_REF,
            destination=destination, source_url=str(bundle)) == destination
        assert bootstrap.selected_release(destination, bootstrap.ODS_BUNDLED_REF)['pixel'] == '4.3.27'


def test_source_defaults_to_ods_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap.sys, 'platform', 'darwin')
    monkeypatch.setattr(bootstrap.os, 'geteuid', lambda: 501)
    destination = bootstrap.acquire_source(ref=bootstrap.ODS_BUNDLED_REF,
        destination=tmp_path / 'source')
    origin = subprocess.check_output(['git', 'remote', 'get-url', 'origin'],
        cwd=destination, text=True).strip()
    assert origin == str(Path(bootstrap.__file__).resolve().parents[3] / 'vendor/pixel.bundle')


@pytest.mark.parametrize('source_url', ['https://github.com/Osmantic/Pixel.git',
    'git@github.com:Osmantic/Pixel.git', 'ssh://git@github.com/Osmantic/Pixel.git'])
def test_remote_source_rejected_before_git(tmp_path, monkeypatch, source_url):
    monkeypatch.setattr(bootstrap.sys, 'platform', 'darwin')
    monkeypatch.setattr(bootstrap.os, 'geteuid', lambda: 501)
    monkeypatch.setattr(bootstrap, 'command', lambda *a, **k: pytest.fail('Git must not run'))
    with pytest.raises(bootstrap.BootstrapError, match='bundled-or-local-pixel-source-required'):
        bootstrap.acquire_source(ref=bootstrap.ODS_BUNDLED_REF,
            destination=tmp_path / 'source', source_url=source_url)
    assert not (tmp_path / 'source').exists()


@pytest.mark.parametrize('fault', [None, 'unreferenced', 'ref', 'existing', 'missing-commit', 'release'])
def test_source_acquisition_uses_exact_commit_without_changing_input(tmp_path, release, monkeypatch, fault):
    monkeypatch.setattr(bootstrap.sys, 'platform', 'darwin')
    monkeypatch.setattr(bootstrap.os, 'geteuid', lambda: 501)
    source = tmp_path / 'Original Source'
    source.mkdir()
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=source, text=True).strip()
    git('init', '-q')
    constants = source / 'scripts/generated/release-constants.json'
    constants.parent.mkdir(parents=True)
    constants.write_text(json.dumps({} if fault == 'release' else release))
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'selected')
    ref = git('rev-parse', 'HEAD')
    original_head = ref
    if fault == 'unreferenced':
        git('checkout', '--detach', '-q', original_head)
        (source / 'selected-marker').write_text('selected commit, no branch')
        git('add', '.')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
            '-c', 'commit.gpgsign=false', 'commit', '-qm', 'unreferenced selected commit')
        ref = git('rev-parse', 'HEAD')
        git('checkout', '--detach', '-q', original_head)
    constants.write_text('uncommitted owner change')
    destination = tmp_path / 'Acquired Source'
    if fault == 'existing':
        destination.mkdir()
        (destination / 'keep').write_text('owner file')
    def run():
        return bootstrap.acquire_source(ref=('main' if fault == 'ref' else
            '0' * 40 if fault == 'missing-commit' else ref), destination=destination,
            license_authorized=False, source_url=str(source))
    if fault not in (None, 'unreferenced'):
        with pytest.raises((bootstrap.BootstrapError, KeyError)):
            run()
        if fault == 'existing':
            assert (destination / 'keep').read_text() == 'owner file'
        else:
            assert not destination.exists()
    else:
        assert run() == destination
        assert bootstrap.selected_release(destination, ref) == release
    assert constants.read_text() == 'uncommitted owner change'
    assert git('rev-parse', 'HEAD') == original_head
    assert not list(tmp_path.glob('.pixel-source-*'))


@pytest.fixture
def release():
    body = b'fixture archive'
    value = {'schemaVersion': 1, 'pixel': '4.3.27', 'node': '>=22', 'openclaw': '2026.6.33',
            'openclawPackage': {'url': 'https://registry.npmjs.org/openclaw/-/openclaw-2026.6.33.tgz',
                'sha256': hashlib.sha256(body).hexdigest(),
                'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(body).digest()).decode()}}
    value['openclawPlugins'] = {name: '2026.6.33' for _, _, name in bootstrap.PLUGINS}
    value['openclawPluginPackages'] = {key: {**value['openclawPackage'],
        'url': 'https://registry.npmjs.org/' + name + '/-/' + name.split('/')[-1] + '-2026.6.33.tgz'}
        for key, _, name in bootstrap.PLUGINS}
    return value


@pytest.mark.parametrize('fault', [None, 'ref', 'dirty', 'url', 'digest', 'changed', 'plugin-url'])
def test_release_requires_exact_clean_source(tmp_path, release, monkeypatch, fault):
    if fault == 'url': release['openclawPackage']['url'] = 'https://example.com/package'
    if fault == 'digest': release['openclawPackage']['sha256'] = 'missing'
    if fault == 'plugin-url': release['openclawPluginPackages']['searxng']['url'] = 'https://example.com/plugin'
    path = tmp_path / 'scripts/generated/release-constants.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(release))
    def command(args, **kwargs):
        if args[1] == 'rev-parse': return 'b' * 40 if fault == 'ref' else 'a' * 40
        if args[1] == 'status': return ' M changed' if fault == 'dirty' else ''
        return '{}' if fault == 'changed' else json.dumps(release)
    monkeypatch.setattr(bootstrap, 'command', command)
    if fault:
        with pytest.raises(bootstrap.BootstrapError): bootstrap.selected_release(tmp_path, 'a' * 40)
    else:
        assert bootstrap.selected_release(tmp_path, 'a' * 40) == release


@pytest.mark.parametrize('fault', [None, 'checksum', 'integrity'])
def test_download_verifies_both_hashes(tmp_path, release, monkeypatch, fault):
    import io
    monkeypatch.setattr(bootstrap.urllib.request, 'build_opener',
        lambda *a: SimpleNamespace(open=lambda *a, **k: io.BytesIO(b'fixture archive')))
    package = release['openclawPackage']
    if fault == 'checksum': package['sha256'] = '0' * 64
    if fault == 'integrity': package['integrity'] = 'sha512-' + 'A' * 86 + '=='
    if fault:
        with pytest.raises(bootstrap.BootstrapError): bootstrap.download(package, tmp_path / 'archive')
    else:
        bootstrap.download(package, tmp_path / 'archive')
        assert (tmp_path / 'archive').read_bytes() == b'fixture archive'


@pytest.mark.parametrize('fault', [None, 'root', 'arch', 'npm', 'version', 'probe', 'plugin-version', 'plugin-id'])
def test_staging_is_nonroot_and_publishes_only_verified_runtime(tmp_path, release, monkeypatch, fault):
    for directory, plugin_id in bootstrap.PIXEL_PLUGINS:
        path = tmp_path / directory
        path.mkdir()
        (path / 'package.json').write_text(json.dumps({'version': release['pixel']}))
        (path / 'openclaw.plugin.json').write_text(json.dumps({'id': plugin_id}))
    monkeypatch.setattr(bootstrap.sys, 'platform', 'darwin')
    monkeypatch.setattr(bootstrap.os, 'geteuid', lambda: 0 if fault == 'root' else 501)
    monkeypatch.setattr(bootstrap, 'selected_release', lambda *a: release)
    node, npm = tmp_path / 'node', tmp_path / 'npm-cli.js'
    node.touch()
    npm.touch()
    ca_bundle = tmp_path / 'system-ca.pem'
    ca_bundle.write_text('fixture CA bundle')
    monkeypatch.setattr(bootstrap, 'MACOS_CA_BUNDLE', ca_bundle)
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        assert kwargs['env']['PATH'].startswith(str(node.parent))
        assert kwargs['env']['NODE_EXTRA_CA_CERTS'] == str(ca_bundle)
        if args[1] == '-p':
            return json.dumps({'platform': 'darwin', 'arch': 'x64' if fault == 'arch' else 'arm64', 'major': 22})
        if 'plugins' in args:
            config = json.loads(Path(kwargs['env']['OPENCLAW_CONFIG_PATH']).read_text())
            return json.dumps({'plugins': [{'id': plugin_id, 'rootDir': path, 'status': 'loaded'}
                for plugin_id, path in zip(config['plugins']['allow'], config['plugins']['load']['paths'])]})
        if 'ci' in args:
            return ''
        if 'install' in args:
            # npm may be a Volta shell launcher, not JavaScript for node to run.
            assert args[0] == str(npm)
            assert '--install-strategy=nested' in args and '--omit=dev' in args
            if fault == 'npm': raise bootstrap.BootstrapError('npm failure')
            runtime = Path(args[args.index('--prefix') + 1]) / 'node_modules/openclaw'
            runtime.mkdir(parents=True)
            (runtime / 'package.json').write_text(json.dumps({'name': 'openclaw',
                'version': 'other' if fault == 'version' else release['openclaw']}))
            (runtime / 'openclaw.mjs').touch()
            for _, plugin_id, name in bootstrap.PLUGINS:
                path = runtime.parent / name
                path.mkdir(parents=True)
                (path / 'package.json').write_text(json.dumps({'name': name,
                    'version': 'wrong' if fault == 'plugin-version' else release['openclawPlugins'][name]}))
                (path / 'openclaw.plugin.json').write_text(json.dumps({
                    'id': 'wrong' if fault == 'plugin-id' else plugin_id}))
            return ''
        return 'other' if fault == 'probe' else release['openclaw']
    monkeypatch.setattr(bootstrap, 'command', command)
    monkeypatch.setattr(bootstrap, 'download', lambda package, path: path.write_bytes(b'archive'))
    destination = tmp_path / 'Owner Runtime'
    def run():
        return bootstrap.stage(source=tmp_path, ref='a' * 40, destination=destination, node=node, npm=npm)
    if fault:
        with pytest.raises(bootstrap.BootstrapError): run()
        assert not destination.exists()
    else:
        assert run() == destination / 'node_modules/openclaw'
        receipt = json.loads((destination / 'ods-bootstrap.json').read_text())
        assert receipt['pixelSourceRef'] == 'a' * 40
        assert [p['id'] for p in receipt['plugins']] == ['discord', 'searxng', 'llama-cpp']
        assert [p['id'] for p in receipt['pixelPlugins']] == [item[1] for item in bootstrap.PIXEL_PLUGINS]
    assert not list(tmp_path.glob('.pixel-bootstrap-*'))
    if fault == 'root': assert not calls


@pytest.mark.parametrize('fault', [None, 'missing', 'failed', 'wrong-path', 'duplicate'])
def test_plugin_probe_checks_loaded_identity_and_isolated_state(tmp_path, monkeypatch, fault):
    plugins = [{'id': 'searxng', 'path': 'plugins/0'}]
    runtime = tmp_path / 'bundle'
    runtime.mkdir()
    def command(args, **kwargs):
        assert args[1] == str(runtime / 'runtime/openclaw.mjs')
        assert kwargs['env']['HOME'] == str(tmp_path / 'probe-home')
        assert kwargs['env']['OPENCLAW_STATE_DIR'] == str(tmp_path / 'probe-home/state')
        config = Path(kwargs['env']['OPENCLAW_CONFIG_PATH'])
        assert config.stat().st_mode & 0o777 == 0o600
        result = [{'id': 'searxng', 'rootDir': str(runtime / 'plugins/0'), 'status': 'loaded'}]
        if fault == 'missing': result = []
        if fault == 'failed': result[0]['status'] = 'error'
        if fault == 'wrong-path': result[0]['rootDir'] = '/other/plugin'
        if fault == 'duplicate': result += result
        return json.dumps({'plugins': result})
    monkeypatch.setattr(bootstrap, 'command', command)
    def run():
        bootstrap.probe_plugins(Path('/node'), runtime, plugins, tmp_path, {}, entrypoint='runtime/openclaw.mjs')
    if fault:
        with pytest.raises(bootstrap.BootstrapError, match='load-proof-failed'): run()
    else:
        run()


@pytest.mark.parametrize('fault', [None, 'root', 'arch', 'user', 'label', 'uid', 'tools', 'run', 'changed'])
@pytest.mark.parametrize('existing', [False, True])
def test_sandbox_build_and_proof_are_owner_bound(tmp_path, release, monkeypatch, fault, existing):
    monkeypatch.setattr(bootstrap.sys, 'platform', 'darwin')
    monkeypatch.setattr(bootstrap.os, 'geteuid', lambda: 0 if fault == 'root' else 501)
    monkeypatch.setattr(bootstrap.os, 'getuid', lambda: 501)
    monkeypatch.setattr(bootstrap, 'selected_release', lambda *a: release)
    docker = tmp_path / 'docker'
    docker.touch()
    calls, inspections = [], []
    def command(args, **kwargs):
        calls.append(args)
        if args[1:3] == ['image', 'ls']: return '{}' if existing else ''
        if args[1] == 'build':
            assert 'PIXEL_SANDBOX_UID=501' in args and 'linux/arm64' in args
            return ''
        if args[1:3] == ['image', 'inspect']:
            inspections.append(True)
            return json.dumps([{'Id': 'sha256:' + ('b' if fault == 'changed' and len(inspections) > 1 else 'a') * 64,
                'Architecture': 'amd64' if fault == 'arch' else 'arm64', 'Os': 'linux',
                'Config': {'User': 'root' if fault == 'user' else 'sandbox', 'Labels': {
                    'org.osmantic.pixel.sandbox-version': release['pixel'],
                    'org.osmantic.pixel.sandbox-uid': '501',
                    'org.osmantic.pixel.source-ref': 'wrong' if fault == 'label' else 'a' * 40}}}])
        assert args[1] == 'run'
        assert 'sha256:' + 'a' * 64 in args
        assert args[args.index('--network') + 1] == 'none'
        assert '--read-only' in args and '--cap-drop' in args
        assert 'no-new-privileges:true' in args and '--memory' in args
        assert not any(x in args for x in ('--privileged', '--volume', '-v'))
        if fault == 'run': raise bootstrap.BootstrapError('run-failed')
        mount = args[args.index('--mount') + 1]
        workspace = Path(mount.removeprefix('type=bind,source=').removesuffix(',target=/workspace'))
        assert workspace.name.startswith('ods-sandbox-workspace-')
        assert workspace.parent == tmp_path.parent
        (workspace / 'ods-proof.txt').write_text('ods-sandbox-proof')
        return json.dumps({'uid': 0 if fault == 'uid' else 501,
                           'readback': 'ods-sandbox-proof', 'tools': fault != 'tools'})
    monkeypatch.setattr(bootstrap, 'command', command)
    cleanups = []
    monkeypatch.setattr(bootstrap.subprocess, 'run', lambda args, **kwargs: cleanups.append(args))
    def run():
        return bootstrap.prepare_sandbox(source=tmp_path, ref='a' * 40, docker=docker)
    if fault:
        with pytest.raises(bootstrap.BootstrapError): run()
    else:
        receipt = run()
        assert receipt['ownerUid'] == 501 and receipt['imageId'] == 'sha256:' + 'a' * 64
    if fault != 'root':
        assert any(args[1] == 'build' for args in calls) is not existing
    ran = [args for args in calls if args[1] == 'run']
    assert bool(cleanups) == bool(ran)
    if ran:
        assert cleanups[0] == [str(docker), 'rm', '-f', ran[0][ran[0].index('--name') + 1]]
