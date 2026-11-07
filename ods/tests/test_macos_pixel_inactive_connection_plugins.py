"""Actual candidate producer, bundle copy and Root mapping with inactive limbs.

Acquisition, Apple interpreter discovery, unrelated OpenClaw repairs and the
external Node probe are fixture seams. Plugin selection, Git source binding,
Source payloads, inventory/hash verification and installer mapping are real.
No installation, service, account, model or provider is used.
"""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOST_PLATFORM = sys.platform
sys.path.insert(0, str(ROOT / 'tests'))
from test_macos_pixel_release_selection import source, git, services  # noqa: E402,F401
from test_native_source_component import package, component  # noqa: E402,F401


def load(name, leaf):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'installers/macos/lib' / leaf)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


config = load('inactive_candidate_config', 'pixel-native-config.py')
installer = load('inactive_candidate_installer', 'pixel-macos-access-install.py')


@pytest.fixture
def inactive(source, package, tmp_path, monkeypatch):  # noqa: F811
    tmp_path = tmp_path.resolve()
    release = json.loads(git(package[0], 'show', package[1] + ':scripts/generated/release-constants.json'))
    monkeypatch.setattr(config.bootstrap, 'selected_release', lambda *args: release)
    monkeypatch.setattr(config.sys, 'platform', 'darwin')
    if HOST_PLATFORM != 'darwin':
        import pixel_macos_custody
        def no_acl(fd):
            assert not any('acl' in name.casefold() for name in os.listxattr(fd))
        monkeypatch.setattr(pixel_macos_custody, '_require_no_acl', no_acl)
    assert os.geteuid() != 0, 'owned disposable fixture required'
    for origin in component.ROOT_SOURCE_FILES.values():
        target = source.ods / origin
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / origin).read_bytes())
        target.chmod(0o755 if origin == 'bin/ods-pixel-route-lease' else 0o644)
    plugin = source.ods / 'extensions/services/pixel-agent/plugin'
    (plugin / 'openclaw.plugin.json').write_text('{"id":"pixel-ods"}')
    for leaf in ['cancellable-exec.sh', *(name for name, _ in config.bundle.SHARED_REPAIRS)]:
        relative = Path('extensions/services/pixel-agent/host') / leaf
        (source.ods / relative).write_bytes((ROOT / relative).read_bytes())
    git(source.repository, 'add', '.')
    git(source.repository, 'commit', '-m', 'actual candidate source closure')
    runtime = tmp_path / 'acquired'
    core = runtime / 'node_modules/openclaw'
    core.mkdir(parents=True)
    (core / 'package.json').write_text(json.dumps({'name': 'openclaw', 'version': release['openclaw']}))
    (core / 'openclaw.mjs').write_text('// invented, never executed\n')
    paths = {'pixel-ods': str(plugin)}
    packages = [(name, identifier, release['openclawPlugins'][name], 'node_modules/' + name)
        for _key, identifier, name in config.bootstrap.PLUGINS]
    packages += [(identifier, identifier, release['pixel'], 'pixel-plugins/' + directory)
        for directory, identifier in config.bootstrap.PIXEL_PLUGINS]
    packages += [('parallel', 'parallel', release['openclaw'], 'parallel')]
    for name, identifier, version, relative in packages:
        path = runtime / relative
        path.mkdir(parents=True)
        (path / 'package.json').write_text(json.dumps({'name': name, 'version': version}))
        (path / 'openclaw.plugin.json').write_text(json.dumps({'id': identifier}))
        (path / 'index.mjs').write_text('// ' + identifier + ', never executed\n')
        paths[identifier] = str(path)
    monkeypatch.setattr(config, 'verified_parallel_plugin', lambda path, selected: str(path))
    node = tmp_path / 'node'
    node.write_text('#!/bin/sh\nexit 97\n')
    node.chmod(0o755)
    monkeypatch.setattr(config.native_node, 'acquire', lambda *args, **kwargs: node)
    monkeypatch.setattr(config.bundle, '_apply_shared_repairs',
        lambda *args: [config.bundle._shared_repair_contract(*item) for item in config.bundle.SHARED_REPAIRS])
    monkeypatch.setattr(config.bundle, '_patch_stream_progress', lambda *args, **kwargs: {'fixture': 'unrelated repair'})
    original_spec = config.importlib.util.spec_from_file_location
    def spec(name, path, *args, **kwargs):
        value = original_spec(name, path, *args, **kwargs)
        if name == 'native_source_python':
            execute = value.loader.exec_module
            def wrapped(module):
                execute(module)
                module.select_python = lambda: Path('/usr/bin/python3')
            value.loader.exec_module = wrapped
        return value
    monkeypatch.setattr(config.importlib.util, 'spec_from_file_location', spec)
    candidate = tmp_path / 'candidate'
    candidate.mkdir(mode=0o700)
    active = ['pixel-ods', 'parallel', 'pixel-operations-broker', 'llama-cpp']
    document = {'models': {'fixture': 'existing-model'}, 'tools': {'allow': ['invented_tool'],
        'web': {'search': {'provider': 'parallel-free'}}}, 'plugins': {'allow': active,
        'load': {'paths': [paths[name] for name in active]}, 'entries': {
            'pixel-source-broker': {'enabled': False}, 'pixel-frontier-broker': {'enabled': False}}}}
    original = (json.dumps(document) + '\n').encode()
    (candidate / 'openclaw.json').write_bytes(original)
    (candidate / 'openclaw.json').chmod(0o600)
    sidefiles = {'gateway.env': b"PIXEL_LIMB_CALENDAR_ENABLED='0'\n", 'onboarding.json': b'{"invented":true}\n'}
    for name, body in sidefiles.items():
        (candidate / name).write_bytes(body)
        (candidate / name).chmod(0o600)
    (candidate / 'candidate.json').write_text(json.dumps({'status': 'staged', 'pixelSourceRef': package[1],
        'requiresServiceQualification': True,
        'connectionSidefiles': {name: hashlib.sha256(body).hexdigest() for name, body in sidefiles.items()}}))
    (candidate / 'candidate.json').chmod(0o600)
    for name, relative in config.bundle.ODS_SERVICE_SOURCES.items():
        source.files[name] = (source.ods / relative).read_bytes()
    source.files['helpers/preview-inspection.json'] = json.dumps({
        'imageId': 'sha256:' + 'a' * 64, 'ownerUid': os.getuid(), 'transport': 'docker-desktop',
        'snapshotRoot': '/previews', 'docker': '/Applications/Docker.app/Contents/Resources/bin/docker',
        'dockerSocket': '/Users/fixture/.docker/run/docker.sock', 'dockerSha256': 'b' * 64}).encode()
    service_manifest = services(source)
    service_manifest.update(pixelSourceRef=package[1], candidateConfigSha256=hashlib.sha256(original).hexdigest())
    service_root = tmp_path / 'services'
    for name, body in {**source.files, 'services.json': config.bundle._encode(service_manifest)}.items():
        target = service_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        target.chmod(0o600)
    service_digest = hashlib.sha256((service_root / 'services.json').read_bytes()).hexdigest()
    observed = []
    def validate(path, **kwargs):
        value = json.loads(path.read_bytes())
        staged = kwargs['node'].parent
        assert kwargs['expected'] == {name: str(staged / ('plugins/' + str(i))) for i, name in enumerate(active)}
        value['plugins']['load']['paths'] = document['plugins']['load']['paths']
        assert value == document
        observed.append(True)
    monkeypatch.setattr(config, 'validate_candidate', validate)
    destination = tmp_path / 'new-bundle'
    return dict(source=package[0], ref=package[1], candidate=candidate, node=node, runtime=runtime,
        destination=destination, ods_source=source.ods, services_bundle=service_root,
        services_digest=service_digest), document, original, observed


def test_real_producer_packages_inactive_connections_without_enabling_them(inactive):
    arguments, document, original, observed = inactive
    digest = config.stage_bundle(**arguments)
    root = arguments['destination']
    manifest, actual = config.bundle.verify(root, expected_digest=digest)
    assert actual == digest and observed == [True]
    required = config.bundle.connection_plugins(root, manifest)
    assert required == {'pixel-operations-broker': 'plugins/2',
        'pixel-source-broker': 'plugins/4', 'pixel-frontier-broker': 'plugins/5'}
    assert (arguments['candidate'] / 'openclaw.json').read_bytes() == original
    selected = installer._configured_bundle_plugins(root, manifest, document)
    assert selected == ['plugins/' + str(i) for i in range(4)]
    plan = installer._bundle_plan(root, digest,
        {'OPENCLAW_CONFIG_PATH': str(arguments['candidate'] / 'openclaw.json')}, pwd.getpwuid(os.getuid()))
    mapped = json.loads(plan['config_bytes'])
    assert mapped['plugins']['load']['paths'] == [str(Path(plan['destination']) / relative) for relative in selected]
    mapped['plugins']['load']['paths'] = document['plugins']['load']['paths']
    assert mapped == document
    # Existing partial histories still verify for recovery, but candidate
    # admission must reject their incomplete connection payloads.
    incomplete = dict(manifest, plugins=manifest['plugins'][:4])
    with pytest.raises(config.bundle.BundleError, match='plugins-incomplete'):
        config.bundle.connection_plugins(root, incomplete)


@pytest.mark.parametrize('fault', ['active-omitted', 'unknown-unloaded', 'duplicate', 'missing-allow', 'metadata-change'])
def test_root_candidate_mapping_refuses_unqualified_extra_payloads(inactive, fault):
    arguments, document, _original, _observed = inactive
    digest = config.stage_bundle(**arguments)
    root = arguments['destination']
    manifest, _actual = config.bundle.verify(root, expected_digest=digest)
    if fault == 'active-omitted': document['plugins']['allow'].append('pixel-source-broker')
    elif fault == 'unknown-unloaded': document['plugins']['load']['paths'].pop()
    elif fault == 'duplicate':
        manifest['plugins'].append('plugins/4')
    elif fault == 'missing-allow': document['plugins'].pop('allow')
    else: (root / 'plugins/4/openclaw.plugin.json').write_text('{"id":"unknown"}')
    with pytest.raises((installer.InstallError, installer._bundle.BundleError)):
        installer._configured_bundle_plugins(root, manifest, document)


def test_existing_incomplete_bundle_remains_recoverable_but_new_admission_refuses(inactive, monkeypatch):
    arguments, document, original, _observed = inactive
    old = arguments['destination'].with_name('retained-four-plugin-bundle')
    build = config.bundle.build
    def previous_producer(**kwargs):
        # Reproduce the actual old producer input, retaining the complete real
        # build/copy/provenance verification. Only the two inactive payloads
        # were omitted by that producer, as on the physical Mac.
        kwargs['plugins'] = kwargs['plugins'][:4]
        digest = build(**kwargs)
        shutil.copytree(kwargs['destination'], old, symlinks=True)
        return digest
    monkeypatch.setattr(config.bundle, 'build', previous_producer)
    with pytest.raises(config.bundle.BundleError, match='plugins-incomplete'):
        config.stage_bundle(**arguments)
    assert not arguments['destination'].exists()
    assert (arguments['candidate'] / 'openclaw.json').read_bytes() == original
    manifest, _digest = config.bundle.verify(old)
    assert installer._configured_bundle_plugins(old, manifest, document, _recovery=True) == manifest['plugins']
    with pytest.raises(installer._bundle.BundleError, match='plugins-incomplete'):
        installer._configured_bundle_plugins(old, manifest, document)


def test_installed_active_subset_preserves_order_after_a_connection(inactive):
    arguments, document, _original, _observed = inactive
    digest = config.stage_bundle(**arguments)
    root = arguments['destination']
    manifest, _digest = config.bundle.verify(root, expected_digest=digest)
    # A later explicit Frontier opt-in may load index5 while Source index4
    # remains inactive. A runtime patch must retain that exact subset/order.
    selected = ['plugins/' + str(i) for i in (0, 1, 2, 3, 5)]
    document['plugins']['allow'].append('pixel-frontier-broker')
    document['plugins']['entries']['pixel-frontier-broker']['enabled'] = True
    document['plugins']['load']['paths'] = [str(root / relative) for relative in selected]
    assert installer._configured_bundle_plugins(root, manifest, document, current=root) == selected
