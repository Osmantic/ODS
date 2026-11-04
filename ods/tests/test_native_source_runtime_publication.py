"""Actual bundle/manifest/inspect with relocated files; no SDK or Darwin proof.

Only fixed paths, Root ownership/ACL metadata are simulated. Git, copying,
hashing, import entrypoints and RuntimeCustody.inspect are actual production.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

if not sys.platform.startswith('linux'):
    pytest.skip('Linux disposable Root-metadata harness', allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'bin')]
from test_macos_pixel_release_selection import source, services, git  # noqa: E402,F401
from test_native_source_component import package  # noqa: E402,F401
from pixel_provider import runtime_custody  # noqa: E402
from pixel_connections.native_mcp import _parents  # noqa: E402
from pixel_access_bridge import AccessError  # noqa: E402


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load(ROOT / 'installers/macos/lib/pixel-native-source-broker.py', 'source_runtime_helper')
bundle = load(ROOT / 'installers/macos/lib/pixel-runtime-bundle.py', 'source_runtime_bundle_test')


@pytest.fixture
def built(source, package, tmp_path):  # noqa: F811
    s = source
    for _target, origin in helper.ROOT_SOURCE_FILES.items():
        path = s.ods / origin
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / origin).read_bytes())
        path.chmod(0o755 if origin == 'bin/ods-pixel-route-lease' else 0o644)
    plugin = s.ods / 'extensions/services/pixel-agent/plugin'
    (plugin / 'openclaw.plugin.json').write_text('{"id":"pixel-ods"}')
    git(s.repository, 'add', '.')
    git(s.repository, 'commit', '-m', 'complete protected source fixture')
    s.ref = git(s.repository, 'rev-parse', 'HEAD')
    # Some existing service bindings (e.g. pixel_macos_custody.py) are also in
    # the real Root source closure. Keep the fixture's expected service bytes
    # coherent with the same actual Git commit, not its earlier invented body.
    for name, relative in bundle.ODS_SERVICE_SOURCES.items():
        s.files[name] = (s.ods / relative).read_bytes()
    manifest = services(s)
    manifest['pixelSourceRef'] = package[1]
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'package.json').write_text('{"name":"openclaw","version":"2026.6.33"}')
    (runtime / 'openclaw.mjs').write_text('// invented, never executed\n')
    for file in runtime.iterdir(): file.chmod(0o644)
    node = tmp_path / 'node'
    node.write_text('#!/bin/sh\nexit 97\n')
    node.chmod(0o755)
    python = tmp_path / 'qualified-python-copy'
    python.write_bytes(Path(sys.executable).resolve().read_bytes())
    python.chmod(0o755)
    component = helper.source_payloads(package[0], package[1], python)
    destination = tmp_path / 'staged'
    plugins = [plugin]
    for name in ('pixel-source-broker', 'pixel-operations-broker', 'pixel-frontier-broker'):
        addon = tmp_path / 'source-plugins' / name
        addon.mkdir(parents=True)
        (addon / 'openclaw.plugin.json').write_text(json.dumps({'id':name}))
        (addon / 'index.mjs').write_text('// invented loader metadata, never executed\n')
        plugins.append(addon)
    build_args = dict(node=node, runtime=runtime,
        plugins=plugins, ods_source=s.ods,
        pixel_source_ref=package[1], ods_plugin_indices=[0], service_manifest=manifest,
        services_digest=__import__('hashlib').sha256(bundle._encode(manifest)).hexdigest(),
        source_component=component)
    digest = bundle.build(destination=destination, **build_args)
    return SimpleNamespace(source=s, root=destination, digest=digest, package=package, python=python,
        build_args=build_args)


def test_private_umask_preserves_source_bundle_identity(built, tmp_path):
    destination = tmp_path / 'private-umask-bundle'
    previous = os.umask(0o077)
    try:
        digest = bundle.build(destination=destination, **built.build_args)
    finally:
        os.umask(previous)
    assert digest == built.digest
    bundle.verify(destination, expected_digest=digest)
    (destination / helper.COMPONENT).chmod(0o700)
    with pytest.raises(bundle.BundleError, match='bundle-content-changed'):
        bundle.verify(destination, expected_digest=digest)


def test_actual_bundle_copies_only_reviewed_closed_source_and_binds_inputs(built):
    f = built
    manifest, checksum = bundle.verify(f.root, expected_digest=f.digest)
    assert checksum == f.digest
    files = {p.relative_to(f.root / 'ods-source').as_posix() for p in (f.root / 'ods-source').rglob('*') if p.is_file()}
    assert files == set(helper.ROOT_SOURCE_FILES) and len(files) == 107
    proof = json.loads((f.root / 'ods-source.json').read_bytes())
    assert proof['commit'] == f.source.ref
    for target, origin in helper.ROOT_SOURCE_FILES.items():
        assert (f.root / 'ods-source' / target).read_bytes() == (ROOT / origin).read_bytes()
    assert not any('vendor' in name for name in files)
    assert set(p.name for p in (f.root / 'native-source').iterdir()) == set(helper.FILES)
    # No descriptor/manifest self digest is embedded in the protected bundle.
    assert 'pixel-provider-runtime.json' not in manifest['entries']
    assert 'source-runtime-manifest.json' not in manifest['entries']


def test_actual_imports_from_the_qualified_copy_without_executing_entrypoints(built):
    f = built
    copied = f.root / 'ods-source/bin'
    imports = ['pixel_connections.planner', 'pixel_connections.custody', 'pixel_connections.coordinator',
        'pixel_provider.advice_runtime', 'pixel_provider.runtime_custody', 'pixel_provider.route_worker',
        'pixel_settings.coordinator', 'connection_transaction', 'native_mcp_owner']
    code = ('import importlib,json,pathlib,sys; sys.path.insert(0,sys.argv[1]); '
        'names=json.loads(sys.argv[2]); modules=[importlib.import_module(n) for n in names]; '
        'assert all(pathlib.Path(m.__file__).resolve().is_relative_to(pathlib.Path(sys.argv[1]).resolve()) for m in modules); '
        'print(len(modules))')
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(copied), json.dumps(imports)],
        cwd=f.root, env={'HOME': str(f.root), 'PATH': '/usr/bin:/bin'}, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(len(imports))
    bundle.verify(f.root, expected_digest=f.digest)


def test_different_deployment_source_upgrade_refuses_before_config_or_publication(built):
    installer = load(ROOT / 'installers/macos/lib/pixel-macos-access-install.py', 'source_initial_only_test')
    with pytest.raises(installer.InstallError, match='native-source-upgrade-not-qualified'):
        installer._bundle_plan(built.root, built.digest, {}, SimpleNamespace(pw_uid=os.getuid()),
            upgrade=('0' * 64, 'stream-progress'))
    bundle.verify(built.root, expected_digest=built.digest)


def test_new_flat_source_workers_use_only_actual_bound_bundle_bytes(built):
    installer = load(ROOT / 'installers/macos/lib/pixel-macos-access-install.py', 'source_flat_test')
    selection = {'source': str(built.root), 'digest': built.digest}
    expected = {**{name: (built.root / 'ods-source/bin' / name).read_bytes() for name in installer.SOURCE_HOST_FILES},
        **{'pixel_connections/' + name: (built.root / 'ods-source/bin/pixel_connections' / name).read_bytes()
           for name in installer.CONNECTION_FILES}}
    (built.source.ods / 'bin/pixel_connections/custody.py').write_bytes(b'owner checkout changed, never published')
    assert dict(installer._source_flat_files(selection)) == expected
    assert len(expected) == 26
    (built.root / 'ods-source/bin/pixel_connections/custody.py').write_bytes(b'changed approved copy')
    with pytest.raises((bundle.BundleError, installer._bundle.BundleError)):
        installer._source_flat_files(selection)


def test_self_resealed_private_template_is_refused_before_public_build_copy(built, monkeypatch):
    component = {name: (built.root / 'native-source' / name).read_bytes() for name in helper.FILES}
    component['source-broker.env'] = component['source-broker.env'].replace(
        b"PIXEL_SOURCE_GMAIL_QUERY='in:inbox'", b"PIXEL_SOURCE_GMAIL_QUERY='subject:invented-confidential-b3-probe'")
    manifest = json.loads(component['component.json'])
    manifest['files']['source-broker.env'] = helper.sha(component['source-broker.env'])
    component['component.json'] = helper.encoded(manifest)
    def forbidden(*args, **kwargs):
        raise AssertionError('Public builder must reject before copying any input')
    monkeypatch.setattr(bundle, '_copy_tree', forbidden)
    destination = built.root.parent / 'refused-private-component'
    with pytest.raises(ValueError, match='component-changed'):
        bundle.build(node=built.root.parent / 'node', runtime=built.root.parent / 'runtime',
            destination=destination, plugins=[], ods_source=built.source.ods,
            pixel_source_ref=built.package[1], source_component=component)
    assert not destination.exists()


def test_protected_verify_refuses_private_template_even_with_all_envelopes_coherently_resealed(built):
    root = built.root
    env = root / 'native-source/source-broker.env'
    env.write_bytes(env.read_bytes().replace(b"PIXEL_SOURCE_GMAIL_QUERY='in:inbox'",
        b"PIXEL_SOURCE_GMAIL_QUERY='subject:invented-confidential-b3-probe'"))
    manifest = root / 'native-source/component.json'
    component = json.loads(manifest.read_bytes())
    component['files']['source-broker.env'] = helper.sha(env.read_bytes())
    manifest.write_bytes(helper.encoded(component))
    # Act like an adversarial owner who can reseal every nonsecret envelope,
    # not a test that is stopped only by a stale outer content hash.
    selection_path = root / bundle.RELEASE_SELECTION
    selection = json.loads(selection_path.read_bytes())
    entries = bundle.inventory(root, exclude_manifest=True, normalize_modes=False)
    entries.pop(bundle.RELEASE_SELECTION)
    selection['artifactInventorySha256'] = hashlib.sha256(bundle._encode(entries)).hexdigest()
    selection_path.write_bytes(bundle._encode(selection))
    outer = json.loads((root / 'bundle.json').read_bytes())
    outer['entries'] = bundle.inventory(root, exclude_manifest=True, normalize_modes=False)
    body = bundle._encode(outer)
    (root / 'bundle.json').write_bytes(body)
    checksum = hashlib.sha256(body).hexdigest()
    with pytest.raises(ValueError, match='component-changed'):
        bundle.verify(root, expected_digest=checksum)


def test_producer_descriptor_last_and_linux_private_reader_remains_private(built, tmp_path, monkeypatch):
    f = built
    # Explicit relocation of ONLY fixed path constants; no inspect stub.
    tree = ast.parse((ROOT / 'installers/macos/lib/pixel-native-source-broker.py').read_text())
    relocated = tmp_path / 'protected'
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ('RUNTIME_ROOT', 'PROGRAM_ROOT', 'ROOT_META', 'DESCRIPTOR', 'RUNTIME_MANIFEST')):
            node.value.args[0].value = str(relocated) + node.value.args[0].value
    module = ModuleType('relocated_source_runtime')
    module.__file__ = str(ROOT / 'installers/macos/lib/pixel-native-source-broker.py')
    exec(compile(ast.fix_missing_locations(tree), module.__file__, 'exec'), module.__dict__)
    published = module.RUNTIME_ROOT / f.digest
    published.parent.mkdir(parents=True)
    f.root.rename(published)
    launcher = module.PROGRAM_ROOT / 'openclaw-gateway-launcher'
    launcher.parent.mkdir(parents=True)
    body = ('#!/bin/sh\nexec /usr/bin/env -u NODE_OPTIONS -u NODE_PATH ' + str(published / 'node')
        + ' ' + str(published / 'runtime/openclaw.mjs') + ' "$@"\n').encode()
    launcher.write_bytes(body)
    launcher.chmod(0o755)
    # Root metadata only is explicitly simulated. Hashing the actual private
    # executable copy, complete trees and launcher remains production code.
    monkeypatch.setattr(runtime_custody, 'ROOT_UID', os.getuid())
    monkeypatch.setattr(runtime_custody, '_parents', lambda path: _parents(path, os.getuid()))
    monkeypatch.setattr(runtime_custody, '_acl', lambda fd: None)
    original_read = runtime_custody._read
    monkeypatch.setattr(runtime_custody, '_read', lambda path, _uid, maximum:
                        original_read(path, os.getuid(), maximum))
    files = module.runtime_files(published, f.digest, body, str(f.python))
    assert files[-1][0] == module.DESCRIPTOR
    assert files[-1][2] == 0o644 and files[0][0] == module.RUNTIME_MANIFEST and files[0][2] == 0o644
    for path, data, mode in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
        path.chmod(mode)
    monkeypatch.setattr(runtime_custody, 'DESCRIPTOR', module.DESCRIPTOR)
    bridge = SimpleNamespace(binary=str(launcher))
    # Darwin now has a distinct public-metadata reader. Do not pretend this
    # Linux UID simulation proves Darwin or real owner DAC. The separate real
    # Root/chroot DAC control qualifies that read boundary with distinct UIDs.
    with pytest.raises(AccessError, match='unsafe-settings-source'):
        runtime_custody.RuntimeCustody(bridge).inspect()


@pytest.mark.parametrize('change', ['extra', 'missing', 'component-ref', 'body'])
def test_changed_source_component_or_code_refuses_before_runtime_metadata(built, change):
    f = built
    if change == 'extra':
        (f.root / 'ods-source/extra.py').write_text('extra')
    elif change == 'missing':
        (f.root / 'ods-source/bin/connection_transaction.py').unlink()
    elif change == 'component-ref':
        path = f.root / 'native-source/component.json'
        value = json.loads(path.read_bytes())
        value['sourceRef'] = '0' * 40
        path.write_bytes(helper.encoded(value))
    else:
        (f.root / 'ods-source/bin/pixel_connections/custody.py').write_text('changed')
    with pytest.raises(bundle.BundleError):
        bundle.verify(f.root, expected_digest=f.digest)
