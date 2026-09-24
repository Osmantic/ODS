"""Portable source-binding tests; these do not qualify macOS custody or launch.

On Windows only, the file-reading adapter bypasses unavailable POSIX dirfd
operations. The POSIX integration test below uses the real bundle copier.
"""
import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import patch

import pytest


SPEC = importlib.util.spec_from_file_location('release_selection_bundle',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-runtime-bundle.py')
bundle = importlib.util.module_from_spec(SPEC)
if os.name == 'nt':
    with patch.dict(sys.modules, {'fcntl': types.SimpleNamespace()}):
        SPEC.loader.exec_module(bundle)
else:
    SPEC.loader.exec_module(bundle)


def git(root, *args):
    return subprocess.run(['git', '-c', 'core.autocrlf=false', *args], cwd=root,
        capture_output=True, check=True, timeout=20).stdout.decode().strip()


def sha(body):
    return hashlib.sha256(body).hexdigest()


def put(root, path, body):
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.delenv('ODS_BOOTSTRAP_SOURCE_DIR', raising=False)
    repository = tmp_path / 'repo'
    repository.mkdir()
    ods = repository / 'ods'
    files = {name: b'VALUE = 1\n' for name in bundle.ODS_SERVICE_SOURCES}
    for name, relative in bundle.ODS_SERVICE_SOURCES.items():
        put(ods, relative, files[name])
    files.update({'operations/broker.py': b'PIXEL = 1\n',
                  'operations/policy.json': b'{"schemaVersion":1}\n',
                  'helpers/extension-catalog.json': b'{"extensions":[]}\n'})
    files['helpers/preview-inspection.json'] = b'{"fixture":"image-identity"}\n'
    put(ods, 'extensions/services/pixel-agent/plugin/index.mjs', b'export default {};\n')
    put(ods, 'extensions/services/pixel-agent/plugin/lib/tool.mjs', b'export const tool = 1;\n')
    put(ods, 'extensions/services/pixel-agent/host/cancellable-exec.sh', b'#!/bin/sh\nexit 0\n')
    git(repository, 'init')
    git(repository, 'config', 'user.name', 'Source Binding Test')
    git(repository, 'config', 'user.email', 'source-binding@example.invalid')
    git(repository, 'config', 'core.autocrlf', 'false')
    git(repository, 'add', '.')
    git(repository, 'commit', '-m', 'fixture')
    ref = git(repository, 'rev-parse', 'HEAD')
    if os.name == 'nt':
        monkeypatch.setattr(bundle, '_open_file', lambda root, relative:
            os.open(Path(root) / relative, os.O_RDONLY | os.O_BINARY))
    return types.SimpleNamespace(repository=repository, ods=ods, files=files, ref=ref, tmp=tmp_path)


def services(source):
    selected = bundle._selected_release_source(source.ods, (), wrapper=False, repairs=False,
        source_paths=set(bundle.ODS_SERVICE_SOURCES.values()))
    return {'schemaVersion': 1, 'status': 'staged', 'requiresServiceQualification': True,
        'pixelSourceRef': 'a' * 40, 'candidateConfigSha256': 'b' * 64,
        'files': {name: {'sha256': sha(body), 'bytes': len(body)} for name, body in source.files.items()},
        'sourceProvenance': bundle.service_source_provenance(selected, source.files)}


def test_complete_legacy_service_bundle_remains_readable_but_partial_inspection_does_not(source):
    manifest = services(source)
    old = json.loads(json.dumps(manifest))
    for name in bundle.INSPECTION_SERVICE_ARTIFACTS:
        old['files'].pop(name)
        old['sourceProvenance']['sourceBindings'].pop(name, None)
        old['sourceProvenance']['generatedArtifacts'].pop(name, None)
    assert bundle.validate_service_manifest_provenance(old)['odsSource']['commit'] == source.ref
    for missing in bundle.INSPECTION_SERVICE_ARTIFACTS:
        partial = json.loads(json.dumps(manifest))
        partial['files'].pop(missing)
        with pytest.raises(bundle.BundleError):
            bundle.validate_service_manifest_provenance(partial)


def staged(source, manifest=None, *, selected=None):
    manifest = manifest or services(source)
    selected = selected or bundle._selected_release_source(source.ods, (0,), wrapper=True, repairs=False,
        expected_ref=manifest['sourceProvenance']['odsSource']['commit'])
    root = source.tmp / 'staged'
    root.mkdir(exist_ok=True)
    entries = {'plugins': ['directory', 0o755], 'plugins/0': ['directory', 0o755],
               'plugins/0/lib': ['directory', 0o755]}
    for relative, name in [('plugin/index.mjs', 'plugins/0/index.mjs'),
                           ('plugin/lib/tool.mjs', 'plugins/0/lib/tool.mjs'),
                           ('host/cancellable-exec.sh', 'cancellable-exec.sh')]:
        body = (source.ods / ('extensions/services/pixel-agent/' + relative)).read_bytes()
        put(root, name, body)
        entries[name] = ['file', 0o755 if name.endswith('.sh') else 0o644, len(body), sha(body)]
    digest = sha(bundle._encode(manifest))
    binding = bundle._encode({'schemaVersion': 1, 'serviceBundleDigest': digest})
    put(root, 'ods-service-binding.json', binding)
    entries['ods-service-binding.json'] = ['file', 0o644, len(binding), sha(binding)]
    selection = bundle._release_selection(root, entries, selected, pixel_ref='a' * 40,
        services_digest=digest, repairs=[], service_manifest=manifest)
    return root, entries, selection


def test_exact_source_bindings_are_scoped_and_preserve_generated_unknown(source):
    manifest = services(source)
    provenance = bundle.validate_service_manifest_provenance(manifest)
    assert provenance['odsSource'] == {'state': 'verified-source-bindings', 'commit': source.ref, 'reason': None}
    assert set(provenance['sourceBindings']) == set(bundle.ODS_SERVICE_SOURCES)
    assert all(item['sourceState'] == 'unknown' for item in provenance['generatedArtifacts'].values())
    assert 'operations/broker.py' not in provenance['sourceBindings']  # Pixel, not ODS source.
    _, entries, value = staged(source, manifest)
    assert bundle._validate_release_selection(value, entries) is value
    assert value['odsSource'] == provenance['odsSource']
    assert value['sourceScope'] == 'recorded-bindings-only'
    assert value['scope'] == 'expected-artifacts-not-running'
    assert value['runtimeMatchesRelease'] is None
    assert str(source.repository) not in json.dumps(value)


@pytest.mark.parametrize('fault', ['dirty', 'untracked', 'non-git', 'missing'])
def test_unavailable_source_is_unknown_not_an_installation_gate(source, fault):
    root = source.ods
    if fault == 'dirty':
        put(root, next(iter(bundle.ODS_SERVICE_SOURCES.values())), b'VALUE = 2\n')
    elif fault == 'untracked':
        put(root, 'untracked.txt', b'new')
    else:
        root = source.tmp / fault
        if fault == 'non-git': root.mkdir()
    selected = bundle._selected_release_source(root, (), wrapper=False, repairs=False,
        source_paths=set(bundle.ODS_SERVICE_SOURCES.values()))
    value = bundle.service_source_provenance(selected, source.files)
    assert value['odsSource']['state'] == 'unknown'
    assert value['odsSource']['commit'] is None
    assert value['sourceBindings'] == {}


def test_mutated_service_copy_cannot_claim_selected_commit(source):
    selected = bundle._selected_release_source(source.ods, (), wrapper=False, repairs=False,
        source_paths=set(bundle.ODS_SERVICE_SOURCES.values()))
    source.files['manager/extension_manager.py'] = b'VALUE = 2\n'
    value = bundle.service_source_provenance(selected, source.files)
    assert value['odsSource']['reason'] == 'source-bytes-mismatch'
    assert value['odsSource']['commit'] is None


def test_bootstrap_archive_binds_actual_copies_to_git_objects(source, monkeypatch):
    installed = source.tmp / 'installed'
    shutil.copytree(source.ods, installed)
    original = source.ods
    source.ods = installed
    monkeypatch.setenv('ODS_BOOTSTRAP_SOURCE_DIR', str(original))
    monkeypatch.setenv('ODS_REF', 'f' * 40)  # A label cannot substitute for Git identity.
    manifest = services(source)
    _, entries, value = staged(source, manifest)
    assert value['odsSource'] == {'state': 'verified-source-bindings', 'commit': source.ref, 'reason': None}
    assert bundle._validate_release_selection(value, entries) is value
    assert str(original) not in json.dumps(value)
    put(installed, 'extensions/services/pixel-agent/plugin/index.mjs', b'changed installed artifact\n')
    _, _, changed = staged(source, manifest)
    assert changed['odsSource']['reason'] == 'source-bytes-mismatch'
    assert changed['sourceBindings'] == {}
    source.files['manager/extension_manager.py'] = b'changed installed service\n'
    assert services(source)['sourceProvenance']['odsSource']['reason'] == 'source-bytes-mismatch'


@pytest.mark.parametrize('fault', ['missing', 'non-git', 'dirty', 'relative', 'empty'])
def test_bootstrap_source_hint_cannot_fabricate_provenance(source, monkeypatch, fault):
    if fault == 'dirty':
        put(source.ods, 'untracked.txt', b'not a clean checkout')
        hint = str(source.ods)
    elif fault == 'non-git':
        root = source.tmp / 'archive'
        shutil.copytree(source.ods, root)
        hint = str(root)
    else:
        hint = {'missing': str(source.tmp / 'missing'), 'relative': 'relative', 'empty': ''}[fault]
    monkeypatch.setenv('ODS_BOOTSTRAP_SOURCE_DIR', hint)
    monkeypatch.setenv('ODS_REF', source.ref)
    value = services(source)['sourceProvenance']
    assert value['odsSource']['state'] == 'unknown'
    assert value['odsSource']['commit'] is None
    assert value['sourceBindings'] == {}


@pytest.mark.skipif(os.name == 'nt', reason='POSIX bootstrap handoff')
def test_standard_bootstrap_hands_clean_checkout_to_installed_entrypoint(source):
    put(source.ods, 'install.sh', b'#!/bin/bash\nset -eu\nprintf "%s\\n" "$ODS_BOOTSTRAP_SOURCE_DIR" > source-path.txt\n')
    (source.ods / 'install.sh').chmod(0o755)
    git(source.repository, 'add', '.')
    git(source.repository, 'commit', '-m', 'bootstrap entrypoint fixture')
    ref = git(source.repository, 'rev-parse', 'HEAD')
    home, temporary, bins = (source.tmp / n for n in ('home', 'temporary', 'bin'))
    for directory in (home, temporary, bins): directory.mkdir()
    put(bins, 'docker', b'#!/bin/sh\nexit 0\n')
    (bins / 'docker').chmod(0o755)
    # Darwin intentionally resets TMPDIR during bootstrap. Keep this fixture's
    # real clone inside its test tree without depending on that OS policy.
    put(bins, 'mktemp', b'#!/bin/sh\n[ "$#" = 1 ] && [ "$1" = -d ] || exit 64\nexec /usr/bin/mktemp -d "$ODS_TEST_TEMP_ROOT/checkout.XXXXXX"\n')
    (bins / 'mktemp').chmod(0o755)
    env = dict(os.environ, HOME=str(home), TMPDIR=str(temporary),
        PATH=str(bins) + os.pathsep + os.environ['PATH'], ODS_REPO_URL=str(source.repository),
        ODS_INSTALL_DIR=str(home / 'ods'), ODS_REF=ref, ODS_ALLOW_LEGACY_PARALLEL='1',
        ODS_TEST_TEMP_ROOT=str(temporary))
    bootstrap = Path(__file__).resolve().parents[1] / 'get-ods.sh'
    result = subprocess.run(['bash', str(bootstrap), '--non-interactive'], env=env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    handed = Path((home / 'ods/source-path.txt').read_text().strip())
    assert handed.is_relative_to(temporary) and handed.name == 'ods'
    assert git(handed, 'rev-parse', 'HEAD') == ref
    assert git(handed, 'status', '--porcelain') == ''
    assert not (home / 'ods/.git').exists()


@pytest.mark.parametrize('change_plugin', [False, True])
def test_head_change_never_relabels_earlier_service_copy(source, change_plugin):
    manifest = services(source)
    path = 'extensions/services/pixel-agent/plugin/index.mjs' if change_plugin else 'unrelated.txt'
    put(source.ods, path, b'changed\n')
    git(source.repository, 'add', '.')
    git(source.repository, 'commit', '-m', 'later checkout')
    later = git(source.repository, 'rev-parse', 'HEAD')
    _, entries, value = staged(source, manifest)
    assert later != source.ref
    assert value['odsSource']['commit'] != later
    if change_plugin:
        assert value['odsSource']['reason'] == 'source-bytes-mismatch'
    else:
        assert value['odsSource']['commit'] == source.ref
        assert bundle._validate_release_selection(value, entries) is value


def test_phase_mismatch_is_explicitly_unknown(source):
    manifest = services(source)
    selected = bundle._selected_release_source(source.ods, (0,), wrapper=True, repairs=False)
    selected['commit'] = 'f' * 40
    _, entries, value = staged(source, manifest, selected=selected)
    assert value['odsSource']['reason'] == 'source-phase-mismatch'
    assert value['odsSource']['commit'] is None
    bundle._validate_release_selection(value, entries)


def test_git_replace_cannot_relabel_plugin_content(source):
    manifest = services(source)
    original = source.ref
    put(source.ods, 'extensions/services/pixel-agent/plugin/index.mjs', b'changed\n')
    git(source.repository, 'add', '.')
    git(source.repository, 'commit', '-m', 'replacement')
    later = git(source.repository, 'rev-parse', 'HEAD')
    git(source.repository, 'replace', original, later)
    _, _, value = staged(source, manifest)
    assert value['odsSource']['reason'] == 'source-bytes-mismatch'
    assert value['odsSource']['commit'] is None


@pytest.mark.parametrize('legacy', [False, True])
def test_missing_service_source_proof_cannot_verify_runtime_selection(source, legacy):
    manifest = services(source) if legacy else None
    if legacy: del manifest['sourceProvenance']
    selected = bundle._selected_release_source(source.ods, (0,), wrapper=True, repairs=False)
    root, entries, _ = staged(source)
    value = bundle._release_selection(root, entries, selected, pixel_ref='a' * 40,
        services_digest=sha(bundle._encode(manifest)) if legacy else None,
        repairs=[], service_manifest=manifest)
    assert value['odsSource']['reason'] == 'service-source-unavailable'
    assert value['runtimeMatchesRelease'] is None
    bundle._validate_release_selection(value, entries)


@pytest.mark.parametrize('fault', [None, 'copied-receipt', 'manifest-file'])
def test_repair_manifest_bindings_match_selected_tree_and_applied_receipts(source, monkeypatch, fault):
    host = source.ods / 'extensions/services/pixel-agent/host'
    for name, _ in bundle.SHARED_REPAIRS:
        put(host, name, b'{"sourceSha256":"fixture"}\n')
    git(source.repository, 'add', '.')
    git(source.repository, 'commit', '-m', 'repair fixtures')
    monkeypatch.setattr(bundle, 'REPAIR_ROOT', host)
    manifest = services(source)
    if fault == 'manifest-file':
        # A different caller checkout may supply repair files; it must not be
        # attributed to the selected ODS tree even when that tree is clean.
        other = source.tmp / 'other-repairs'
        for name, _ in bundle.SHARED_REPAIRS: put(other, name, b'changed\n')
        monkeypatch.setattr(bundle, 'REPAIR_ROOT', other)
    selected = bundle._selected_release_source(source.ods, (0,), wrapper=True, repairs=True,
        expected_ref=manifest['sourceProvenance']['odsSource']['commit'])
    root, entries, _ = staged(source, manifest, selected=selected)
    repairs = [{'manifest': name, 'manifestSha256': sha((host / name).read_bytes())}
               for name, _ in bundle.SHARED_REPAIRS]
    if fault == 'copied-receipt': repairs[0]['manifestSha256'] = 'f' * 64
    value = bundle._release_selection(root, entries, selected, pixel_ref='a' * 40,
        services_digest=sha(bundle._encode(manifest)), repairs=repairs, service_manifest=manifest)
    if fault:
        assert value['odsSource']['reason'] == 'source-bytes-mismatch'
        assert value['repairManifestBindings'] == {}
    else:
        assert set(value['repairManifestBindings']) == {name for name, _ in bundle.SHARED_REPAIRS}
        bundle._validate_release_selection(value, entries, repairs)


@pytest.mark.parametrize('fault', ['file-changed', 'extra-file', 'missing-file'])
def test_copy_mismatch_cannot_claim_source_binding(source, fault):
    manifest = services(source)
    selected = bundle._selected_release_source(source.ods, (0,), wrapper=True, repairs=False)
    root, entries, _ = staged(source, manifest, selected=selected)
    if fault == 'file-changed':
        put(root, 'plugins/0/index.mjs', b'changed after inventory\n')
    elif fault == 'missing-file':
        del entries['plugins/0/index.mjs']
    else:
        put(root, 'plugins/0/untracked.mjs', b'new')
        entries['plugins/0/untracked.mjs'] = ['file', 0o644, 3, sha(b'new')]
    value = bundle._release_selection(root, entries, selected, pixel_ref='a' * 40,
        services_digest=sha(bundle._encode(manifest)), repairs=[], service_manifest=manifest)
    assert value['odsSource']['reason'] == 'source-bytes-mismatch'
    assert value['sourceBindings'] == {}


@pytest.mark.parametrize('fault', ['runtime-green', 'scope', 'commit', 'inventory', 'service-digest',
    'service-commit', 'source-path', 'source-hash', 'source-missing', 'source-extra', 'repair', 'plugin-shape'])
def test_selection_tampering_is_rejected(source, fault):
    _, entries, value = staged(source)
    if fault == 'runtime-green': value['runtimeMatchesRelease'] = True
    if fault == 'scope': value['sourceScope'] = 'whole-release'
    if fault == 'commit': value['odsSource']['commit'] = 'f' * 40
    if fault == 'inventory': entries['new'] = ['file', 0o644, 1, sha(b'x')]
    if fault == 'service-digest': value['serviceBundleDigest'] = 'f' * 64
    if fault == 'service-commit':
        value['serviceManifest']['sourceProvenance']['odsSource']['commit'] = 'f' * 40
        value['serviceBundleDigest'] = sha(bundle._encode(value['serviceManifest']))
    if fault == 'source-path': value['sourceBindings']['plugins/0/index.mjs']['source'] = 'unrelated/index.mjs'
    if fault == 'source-hash': value['sourceBindings']['plugins/0/index.mjs']['sha256'] = 'f' * 64
    if fault == 'source-missing': del value['sourceBindings']['plugins/0/index.mjs']
    if fault == 'source-extra': value['sourceBindings']['runtime/vendor.js'] = {}
    if fault == 'repair': value['repairManifestBindings']['unqualified.json'] = {}
    if fault == 'plugin-shape': value['odsPluginDirectories'] = [{}]
    with pytest.raises(bundle.BundleError):
        bundle._validate_release_selection(value, entries)


@pytest.mark.parametrize('fault', ['generated-green', 'pixel-as-ods', 'path-prefix', 'reason-shape', 'null-provenance'])
def test_service_provenance_cannot_expand_its_scope(source, fault):
    manifest = services(source)
    value = manifest['sourceProvenance']
    if fault == 'generated-green': value['generatedArtifacts']['operations/policy.json']['sourceState'] = 'verified'
    if fault == 'pixel-as-ods': value['sourceBindings']['operations/broker.py'] = {'source': 'vendor/pixel/broker.py', 'sha256': 'f' * 64}
    if fault == 'path-prefix': value['sourceBindings']['manager/extension_manager.py']['source'] = 'other/' + bundle.ODS_SERVICE_SOURCES['manager/extension_manager.py']
    if fault == 'reason-shape':
        value['odsSource'] = {'state': 'unknown', 'commit': None, 'reason': []}
        value['sourceBindings'] = {}
    if fault == 'null-provenance': manifest['sourceProvenance'] = None
    with pytest.raises(bundle.BundleError):
        bundle.validate_service_manifest_provenance(manifest)


def test_legacy_service_and_bundle_are_unknown_without_rewriting(source):
    manifest = services(source)
    del manifest['sourceProvenance']
    assert bundle.validate_service_manifest_provenance(manifest)['odsSource']['reason'] == 'legacy-bundle'
    root, entries, _ = staged(source)
    before = sorted(path.relative_to(root) for path in root.rglob('*'))
    value = bundle._read_release_selection(root, entries)
    assert value['odsSource']['reason'] == 'legacy-bundle'
    assert value['runtimeMatchesRelease'] is None
    assert before == sorted(path.relative_to(root) for path in root.rglob('*'))


@pytest.mark.parametrize('fault', [None, 'noncanonical', 'deep', 'oversize', 'service-binding', 'missing-binding'])
def test_receipt_reader_is_bounded_canonical_and_service_bound(source, fault):
    root, entries, value = staged(source)
    body = bundle._encode(value)
    if fault == 'noncanonical': body += b' '
    if fault == 'deep': body = b'[' * 2000 + b'0' + b']' * 2000
    if fault == 'oversize': body = b' ' * (bundle.MAX_SELECTION + 1)
    if fault == 'service-binding': put(root, 'ods-service-binding.json', b'{}\n')
    if fault == 'missing-binding':
        (root / 'ods-service-binding.json').unlink()
        del entries['ods-service-binding.json']
        value['artifactInventorySha256'] = sha(bundle._encode(entries))
        body = bundle._encode(value)
    put(root, bundle.RELEASE_SELECTION, body)
    entries[bundle.RELEASE_SELECTION] = ['file', 0o644, len(body), sha(body)]
    if fault:
        with pytest.raises(bundle.BundleError): bundle._read_release_selection(root, entries)
    else:
        assert bundle._read_release_selection(root, entries) == value


@pytest.mark.skipif(os.name == 'nt', reason='Requires actual POSIX dirfd, executable modes and nofollow')
def test_full_bundle_migration_preserves_legacy_and_binds_new_receipt(source):
    node, runtime = source.tmp / 'node', source.tmp / 'runtime'
    node.write_bytes(b'fixture binary, never executed')
    node.chmod(0o755)
    put(runtime, 'package.json', b'{"name":"openclaw","version":"2026.6.33"}\n')
    put(runtime, 'openclaw.mjs', b'// fixture\n')
    args = dict(node=node, runtime=runtime,
        plugins=[source.ods / 'extensions/services/pixel-agent/plugin'])
    legacy = source.tmp / 'legacy'
    legacy_digest = bundle.build(destination=legacy, **args)
    assert bundle.expected_release_selection(legacy, expected_digest=legacy_digest)['odsSource']['reason'] == 'legacy-bundle'
    manifest = services(source)
    current = source.tmp / 'current'
    digest = bundle.build(destination=current, ods_source=source.ods, ods_plugin_indices=[0],
        pixel_source_ref='a' * 40, service_manifest=manifest, services_digest=sha(bundle._encode(manifest)), **args)
    value = bundle.expected_release_selection(current, expected_digest=digest)
    assert value['odsSource']['commit'] == source.ref
    assert value['runtimeMatchesRelease'] is None
    # Selecting a new bundle never rewrites the prior rollback artifact.
    bundle.verify(legacy, expected_digest=legacy_digest)
    body = (current / bundle.RELEASE_SELECTION).read_bytes()
    (current / bundle.RELEASE_SELECTION).write_bytes(body.replace(source.ref.encode(), b'f' * 40))
    with pytest.raises(bundle.BundleError): bundle.verify(current, expected_digest=digest)


@pytest.mark.skipif(os.name == 'nt', reason='Requires actual POSIX private service-file custody')
@pytest.mark.parametrize('changed_copy', [False, True])
def test_actual_service_stage_selects_before_copy_and_embeds_provenance(source, monkeypatch, changed_copy):
    spec = importlib.util.spec_from_file_location('release_selection_config',
        Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-config.py')
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    monkeypatch.setattr(config.sys, 'platform', 'darwin')
    monkeypatch.setattr(config.os, 'geteuid', lambda: 501)
    put(source.repository, 'deploy/ops-broker/broker.py', source.files['operations/broker.py'])
    git(source.repository, 'add', '.')
    git(source.repository, 'commit', '-m', 'Pixel broker fixture')
    ref = git(source.repository, 'rev-parse', 'HEAD')
    events = []
    def qualify(root, selected_ref):
        assert root == source.repository and selected_ref == ref
        events.append('pixel-selection')
    monkeypatch.setattr(config.bootstrap, 'selected_release', qualify)
    original_select = config.bundle._selected_release_source
    def select(*args, **kwargs):
        events.append('ods-selection')
        return original_select(*args, **kwargs)
    monkeypatch.setattr(config.bundle, '_selected_release_source', select)
    monkeypatch.setattr(config, 'service_catalog', lambda *args: source.files['helpers/extension-catalog.json'])
    original_snapshot = config.service_snapshot
    def snapshot(root, relative, **kwargs):
        if root == source.ods:
            events.append('ods-copy')
            if changed_copy: return b'VALUE = 2\n'
        return original_snapshot(root, relative, **kwargs)
    monkeypatch.setattr(config, 'service_snapshot', snapshot)
    candidate = source.tmp / 'candidate'
    documents = {'candidate.json': {'pixelSourceRef': ref, 'status': 'staged', 'requiresServiceQualification': True},
                 'openclaw.json': {'private': 'not-in-receipt'}, 'operations-policy.json': {'schemaVersion': 1}}
    for name, document in documents.items():
        put(candidate, name, bundle._encode(document))
        (candidate / name).chmod(0o600)
    destination = source.tmp / 'services'
    digest = config.stage_services(source=source.repository, ref=ref, ods_source=source.ods,
        candidate=candidate, destination=destination, inspection_config={
            'imageId': 'sha256:' + 'a' * 64, 'docker': '/Applications/Docker.app/Contents/Resources/bin/docker',
            'snapshotRoot': '/previews', 'ownerUid': os.getuid(), 'transport': 'docker-desktop',
                'dockerSocket': '/Users/fixture/.docker/run/docker.sock', 'dockerSha256': 'b' * 64})
    body = (destination / 'services.json').read_bytes()
    manifest = json.loads(body)
    assert digest == sha(body)
    assert events[:2] == ['pixel-selection', 'ods-selection']
    assert events.index('ods-selection') < events.index('ods-copy')
    assert b'not-in-receipt' not in body
    provenance = config.bundle.validate_service_manifest_provenance(manifest)
    assert provenance['odsSource']['commit'] == (None if changed_copy else ref)
    assert provenance['odsSource']['reason'] == ('source-bytes-mismatch' if changed_copy else None)
    config.verified_services(destination, expected_digest=digest, expected_ref=ref,
        expected_config_digest=sha((candidate / 'openclaw.json').read_bytes()))
