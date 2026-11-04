"""Repeated Root Source verification executes every proof with fresh modules/FDs."""
# ruff: noqa: E402,F401,F811
import copy
import json
import os
from pathlib import Path
import sys

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs Darwin metadata observation driver', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from test_native_source_publication import material
from test_native_source_runtime_publication import built, source, package
from test_native_runtime_operation_cache import native_runtime_driver, code_loader_driver
import pixel_access_bridge as bridge
import pixel_macos_custody as mac


@pytest.fixture
def complete_loaders(material, monkeypatch):
    # Reuse the actual FD/metadata proof driver on a complete canonical bundle,
    # including Source provenance and payloads. Only Darwin UID/ACL observations
    # and external temporary-directory ancestors are simulated.
    material.f.source = Path(material.f.value.document['sourceRoot'])
    return code_loader_driver(native_runtime_driver(material.f, monkeypatch), monkeypatch)


def test_source_verifier_code_hit_keeps_all_proofs_fresh_and_modules_independent(complete_loaders):
    n, trace = complete_loaders
    path = n.f.source / 'installers/macos/lib/pixel-native-source-broker.py'
    calls = []
    previous = sys.getprofile()
    def profile(frame, event, _arg):
        if event == 'call' and frame.f_code.co_name in ('_artifact_source_record', 'validate_payloads'):
            calls.append(frame.f_code.co_name)
    sys.setprofile(profile)
    try:
        with n.f.bridge._native_runtime_operation():
            first = n.f.bridge.connection_deployment()
            modules = [item for item in trace.modules if item.__name__ == 'verify_native_source']
            modules[-1].fixture_poison = object()
            before = (trace.reads.count(path), len(n.acl_reads), len(calls))
            assert n.f.bridge.connection_deployment() == first
            modules = [item for item in trace.modules if item.__name__ == 'verify_native_source']
            assert len(modules) == 2 and modules[0] is not modules[1]
            assert not hasattr(modules[1], 'fixture_poison')
            assert modules[1].__file__ == str(path) and modules[1].__spec__.origin == str(path)
            assert modules[1].__loader__ is modules[1].__spec__.loader
            assert modules[1].__package__ == modules[1].__spec__.parent
            assert trace.compiles.count(str(path)) == 1
            assert trace.reads.count(path) > before[0] and len(n.acl_reads) > before[1]
            assert {'_artifact_source_record', 'validate_payloads'} <= set(calls[:before[2]])
            assert {'_artifact_source_record', 'validate_payloads'} <= set(calls[before[2]:])
            # Current custody/hash is checked after a safe inode replacement;
            # the contract does not pin this immutable helper's previous inode.
            pending, old_inode = path.with_suffix('.next'), path.stat().st_ino
            pending.write_bytes(path.read_bytes())
            pending.chmod(0o644)
            os.replace(pending, path)
            assert path.stat().st_ino != old_inode
            assert n.f.bridge.connection_deployment() == first
            assert trace.compiles.count(str(path)) == 1
            assert trace.reads.count(path) > before[0] + 1
    finally:
        sys.setprofile(previous)


@pytest.mark.parametrize('change', ['bytes', 'inode', 'acl', 'mode', 'symlink', 'missing', 'payload', 'namespace'])
def test_warm_source_verifier_refuses_current_custody_and_payload_drift(complete_loaders, change):
    n, trace = complete_loaders
    path = n.f.source / 'installers/macos/lib/pixel-native-source-broker.py'
    with n.f.bridge._native_runtime_operation():
        n.f.bridge.connection_deployment()
        compiled = len(trace.compiles)
        if change == 'bytes':
            path.write_bytes(path.read_bytes() + b'\n# unexpected current body\n')
        elif change == 'inode':
            pending = path.with_suffix('.next')
            pending.write_bytes(path.read_bytes())
            pending.chmod(0o666)
            os.replace(pending, path)
        elif change == 'acl':
            n.rejected_acls.add(path)
        elif change == 'mode':
            path.chmod(0o666)
        elif change == 'symlink':
            target = path.with_suffix('.saved')
            path.rename(target)
            path.symlink_to(target)
        elif change == 'missing':
            path.unlink()
        elif change == 'payload':
            target = n.f.root / 'native-source/source-broker.env'
            target.write_bytes(target.read_bytes() + b'# unexpected\n')
        else:
            (n.f.root / 'native-source/unknown.py').write_bytes(b'pass\n')
        with pytest.raises((bridge.AccessError, mac.CustodyError, OSError, ValueError)):
            n.f.bridge.connection_deployment()
        assert len(trace.compiles) == compiled


def test_warm_verifier_rejects_changed_provenance_and_another_bundle_root(complete_loaders):
    n, trace = complete_loaders
    with n.f.bridge._native_runtime_operation():
        n.f.bridge.connection_deployment()
        lease = n.f.bridge._native_runtime_lease()
        module = next(item for item in trace.modules if item.__name__ == '_connection_bundle')
        manifest = json.loads((n.f.root / module.MANIFEST).read_bytes())
        selected = module._read_release_selection(n.f.root, manifest['entries'])
        invalid = copy.deepcopy(selected)
        invalid['odsSource']['state'] = 'unqualified-source'
        with pytest.raises(module.BundleError, match='capability-incomplete'):
            module._verify_native_source(n.f.root, manifest['entries'], invalid, _operation=lease)
        entries = copy.deepcopy(manifest['entries'])
        key = next(key for key in entries if key.startswith('ods-source/') and entries[key][0] == 'file')
        entries[key][3] = '0' * 64
        with pytest.raises(module.BundleError, match='provenance-changed'):
            module._verify_native_source(n.f.root, entries, selected, _operation=lease)
        compiled = len(trace.compiles)
        with pytest.raises(module.BundleError, match='operation-unqualified'):
            module._verify_native_source(n.f.source, manifest['entries'], selected, _operation=lease)
        assert len(trace.compiles) == compiled
    with pytest.raises(module.BundleError, match='operation-unqualified'):
        module._verify_native_source(n.f.root, manifest['entries'], selected, _operation=lease)


def material_selection(n, trace):
    from pixel_connections import custody
    value = n.f.bridge.connection_deployment()
    selected = custody.native_source_selection(n.f.bridge, value)
    publisher = next(item for item in reversed(trace.modules) if item.__name__ == 'verify_native_source')
    return publisher, selected


def material_operations(publisher, selected):
    operations, previous = [], sys.getprofile()
    def profile(frame, event, _arg):
        if (event == 'call' and frame.f_code.co_name == 'verify'
                and frame.f_back.f_code.co_name == '_material'):
            operations.append(frame.f_locals.get('_operation'))
    sys.setprofile(profile)
    try: result = publisher._material(selected)
    finally: sys.setprofile(previous)
    return result, operations


def test_material_reuses_only_current_root_operation_with_fresh_proofs(complete_loaders):
    n, trace = complete_loaders
    path = n.f.source / 'installers/macos/lib/pixel-native-source-broker.py'
    with n.f.bridge._native_runtime_operation():
        publisher, selected = material_selection(n, trace)
        lease = n.f.bridge._native_runtime_lease()
        first, operations = material_operations(publisher, selected)
        before = trace.reads.count(path), len(n.acl_reads)
        second, repeated = material_operations(publisher, selected)
        assert first == second and operations == repeated == [lease]
        assert trace.reads.count(path) > before[0] and len(n.acl_reads) > before[1]
        assert trace.compiles.count(str(path)) == 1
        modules = [item for item in trace.modules if item.__name__ == 'verify_native_source']
        assert len({id(module) for module in modules}) == len(modules)


@pytest.mark.parametrize('state', ['different-root', 'foreign-bridge', 'revoked'])
def test_material_keeps_unrelated_or_revoked_operation_cold(complete_loaders, monkeypatch, state):
    import contextvars
    n, trace = complete_loaders
    with n.f.bridge._native_runtime_operation():
        publisher, selected = material_selection(n, trace)
        lease = n.f.bridge._native_runtime_lease()
        saved = lease.bundle_root
        copied = contextvars.copy_context()
        if state == 'different-root':
            authority = publisher._source_authority
            def changed(selection):
                result = authority(selection)
                lease.bundle_root = saved.parent / ('f' * 64)
                return result
            monkeypatch.setattr(publisher, '_source_authority', changed)
            try:
                _result, operations = material_operations(publisher, selected)
                assert operations == [None]
            finally: lease.bundle_root = saved
        elif state == 'foreign-bridge':
            other = copy.copy(n.f.bridge)
            with other._native_runtime_operation():
                foreign = other._native_runtime_lease()
                _result, operations = material_operations(publisher, selected)
                assert operations == [None] and foreign.bundle_root is None and foreign.custody._cache == {}
    assert not lease.active and lease.code == {}
    if state == 'revoked':
        _result, operations = copied.run(material_operations, publisher, selected)
        assert operations == [None] and lease.code == {}


@pytest.mark.parametrize('change', ['bytes', 'acl', 'namespace'])
def test_material_warm_operation_still_refuses_current_source_drift(complete_loaders, change):
    n, trace = complete_loaders
    with n.f.bridge._native_runtime_operation():
        publisher, selected = material_selection(n, trace)
        material_operations(publisher, selected)
        path = n.f.source / 'installers/macos/lib/pixel-native-source-broker.py'
        if change == 'bytes': path.write_bytes(path.read_bytes() + b'\n# unexpected\n')
        elif change == 'acl': n.rejected_acls.add(path)
        else: (n.f.root / 'native-source/unknown.py').write_bytes(b'pass')
        compiled = len(trace.compiles)
        with pytest.raises((bridge.AccessError, mac.CustodyError, OSError, ValueError)):
            material_operations(publisher, selected)
        assert len(trace.compiles) == compiled


def test_material_legacy_verify_signature_stays_cold_without_retry(complete_loaders, monkeypatch):
    import inspect
    n, trace = complete_loaders
    original = inspect.signature
    def signature(function):
        value = original(function)
        if function.__module__ == 'publication_bundle' and function.__name__ == 'verify':
            return value.replace(parameters=[item for name, item in value.parameters.items() if name != '_operation'])
        return value
    with n.f.bridge._native_runtime_operation():
        publisher, selected = material_selection(n, trace)
        monkeypatch.setattr(inspect, 'signature', signature)
        _result, operations = material_operations(publisher, selected)
        assert operations == [None]
