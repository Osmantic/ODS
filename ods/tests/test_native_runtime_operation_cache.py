"""Actual RuntimeCustody/bridge, invented files and explicit Darwin metadata simulation.

No installed files, Source/provider jobs, SDK, model or real Darwin ACLs are used.
The planner's independent bundle verification remains real on every acquisition;
only verified immutable-file hashes may be reused inside one Root operation.
"""
# ruff: noqa: E402
import contextvars
import copy
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
import threading
from types import SimpleNamespace

import pytest

if sys.platform != 'linux':
    pytest.skip('Linux disposable Darwin metadata simulation', allow_module_level=True)
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'bin'), str(ROOT / 'tests')]
import pixel_access_bridge as bridge
import pixel_macos_custody as mac
from pixel_connections import custody as source
from pixel_provider import runtime_custody as runtime
from test_connection_deployment_paths import deployment  # noqa: F401
_actual_tree_metadata = mac.protected_tree_metadata
_actual_protected_bytes = mac.protected_bytes


@pytest.fixture
def native_runtime(deployment, monkeypatch):  # noqa: F811
    return native_runtime_driver(deployment, monkeypatch)


def native_runtime_driver(f, monkeypatch):
    original_stat, original_lstat, original_fstat = os.stat, os.lstat, os.fstat
    original_fdopen = os.fdopen
    descriptor = runtime.DESCRIPTOR
    manifest = Path(json.loads(descriptor.read_text())['manifest'])
    descriptor.chmod(0o644)
    manifest.chmod(0o644)
    protected = {descriptor, manifest, Path(f.bridge.binary), f.plist,
        Path(json.loads(descriptor.read_text())['hostPython'])}
    roots = (f.root, f.source)

    def root_metadata(path):
        path = Path(path)
        return path in protected or any(path == root or root in path.parents for root in roots)

    def virtual(info, path):
        if not root_metadata(path):
            return info
        value = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
        value.update(st_uid=0, st_gid=0)
        return SimpleNamespace(**value)

    def actual_path(path, kwargs):
        if isinstance(path, int):
            return Path(os.readlink('/proc/self/fd/' + str(path)))
        if kwargs.get('dir_fd') is not None:
            return Path(os.readlink('/proc/self/fd/' + str(kwargs['dir_fd']))) / path
        return Path(path)

    monkeypatch.setattr(os, 'stat', lambda p, *a, **k: virtual(original_stat(p, *a, **k), actual_path(p, k)))
    monkeypatch.setattr(os, 'lstat', lambda p, *a, **k: virtual(original_lstat(p, *a, **k), actual_path(p, k)))
    monkeypatch.setattr(os, 'fstat', lambda fd: virtual(original_fstat(fd), actual_path(fd, {})))
    monkeypatch.setattr(platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(runtime, 'ROOT_UID', 0)
    monkeypatch.setattr(runtime, 'NATIVE_MANIFEST', manifest)

    def parents(path):
        # The owned disposable namespace simulates Root custody; do not accept
        # a real installation or follow symlinks out of this fixture.
        path = Path(path)
        assert path.is_relative_to(f.root.parent.parent)
        for parent in (path.parent, *path.parent.parents):
            if parent == f.root.parent.parent.parent:
                break
            info = original_lstat(parent)
            assert not parent.is_symlink() and not info.st_mode & 0o022

    monkeypatch.setattr(runtime, '_parents', parents)
    acl_reads, rejected_acls = [], set()

    def acl(fd):
        target = Path(os.readlink('/proc/self/fd/' + str(fd)))
        acl_reads.append(target)
        if target in rejected_acls:
            raise bridge.AccessError('provider-runtime-custody-unqualified')

    monkeypatch.setattr(runtime, '_acl', acl)
    monkeypatch.setattr(mac, '_require_no_acl', lambda fd, **_kwargs: acl(fd))

    @contextmanager
    def directory(path):
        path = Path(path)
        assert root_metadata(path) and path.resolve(strict=True) == path
        parents(path / 'entry')
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            yield fd
        finally:
            os.close(fd)

    monkeypatch.setattr(mac, 'protected_directory', directory)
    monkeypatch.setattr(mac, 'protected_tree_metadata', _actual_tree_metadata)
    target = f.root / 'runtime/openclaw.mjs'
    reads, inspections = [], []

    class TracedRead:
        def __init__(self, handle, name):
            self.handle, self.name = handle, name

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def __getattr__(self, key):
            return getattr(self.handle, key)

        def read(self, *args):
            value = self.handle.read(*args)
            if self.name == target:
                reads.append(len(value))
            return value

    def fdopen(fd, *args, **kwargs):
        name = Path(os.readlink('/proc/self/fd/' + str(fd)))
        return TracedRead(original_fdopen(fd, *args, **kwargs), name)

    monkeypatch.setattr(os, 'fdopen', fdopen)
    actual_inspect = runtime.RuntimeCustody.inspect

    def inspect(qualifier):
        inspections.append(qualifier)
        return actual_inspect(qualifier)

    monkeypatch.setattr(runtime.RuntimeCustody, 'inspect', inspect)
    monkeypatch.setattr(source, '_native_root', lambda: None)
    return SimpleNamespace(f=f, reads=reads, inspections=inspections, acl_reads=acl_reads,
        rejected_acls=rejected_acls, target=target, descriptor=descriptor, manifest=manifest)


def select(n, adapter=None):
    adapter = n.f.bridge if adapter is None else adapter
    return bridge.LaunchdAccessBridge._native_runtime_selection(adapter, n.f.environment)


def test_hash_reads_warm_only_inside_exact_operation_and_acl_is_rechecked(native_runtime):
    n = native_runtime
    with n.f.bridge.bounded(30):
        first = select(n)
        qualifier = n.inspections[-1]
        lease = bridge.LaunchdAccessBridge._native_runtime_lease(n.f.bridge)
        assert qualifier is lease.custody and qualifier.bridge is n.f.bridge
        assert sum(n.reads) > 0
        n.reads.clear()
        acls = n.acl_reads.count(n.target)
        assert select(n) == first
        assert n.inspections[-1] is qualifier and n.reads == []
        assert n.acl_reads.count(n.target) > acls
    assert not lease.active and qualifier._cache == {}
    with n.f.bridge.bounded(30):
        assert select(n) == first
        assert n.inspections[-1] is not qualifier and sum(n.reads) > 0
    n.reads.clear()
    select(n)
    cold = n.inspections[-1]
    assert sum(n.reads) > 0
    n.reads.clear()
    select(n)
    assert n.inspections[-1] is not cold and sum(n.reads) > 0


@pytest.mark.parametrize('change', ['content-same-size-mtime', 'inode', 'mode', 'acl', 'namespace'])
def test_cached_runtime_still_refuses_current_file_or_namespace_drift(native_runtime, change):
    n = native_runtime
    with n.f.bridge._native_runtime_operation():
        select(n)
        qualifier = n.inspections[-1]
        if change == 'content-same-size-mtime':
            info = n.target.stat()
            n.target.write_bytes(n.target.read_bytes().replace(b'not', b'now'))
            os.utime(n.target, ns=(info.st_atime_ns, info.st_mtime_ns))
        elif change == 'inode':
            pending = n.target.with_suffix('.next')
            pending.write_bytes(n.target.read_bytes() + b'// different inode\n')
            pending.chmod(0o644)
            os.replace(pending, n.target)
        elif change == 'mode':
            n.target.chmod(0o666)
        elif change == 'acl':
            n.rejected_acls.add(n.target)
        else:
            (n.target.parent / 'unexpected.js').write_text('// current extra entry\n')
        with pytest.raises(bridge.AccessError):
            select(n)
        assert n.inspections[-1] is qualifier
    assert qualifier._cache == {}


def test_actual_source_selection_guard_reuses_hashes_across_real_helper_reload(native_runtime, monkeypatch):
    n = native_runtime
    from pixel_connections import planner
    actual_module = planner.importlib.util.module_from_spec
    modules = []

    def loaded(spec):
        module = actual_module(spec)
        if spec.name == '_connection_bundle':
            modules.append(module)
        return module

    monkeypatch.setattr(planner.importlib.util, 'module_from_spec', loaded)
    with n.f.bridge._native_runtime_operation():
        selection = source.native_source_selection(n.f.bridge, n.f.value)
        qualifier = n.inspections[-1]
        assert sum(n.reads) > 0
        n.reads.clear()
        assert selection.guard() == n.f.value.document
        assert n.inspections[-1] is qualifier and n.reads == []
        assert len(modules) == 2 and modules[0] is not modules[1]
        n.f.env.unlink()
        with pytest.raises((OSError, bridge.AccessError)):
            selection.guard()


def helper(n, name):
    file = n.f.source / 'installers/macos/lib/pixel-runtime-bundle.py'
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bundle_cache_revokes_and_does_not_return_shared_records(native_runtime):
    n = native_runtime
    module = helper(n, 'actual_bundle_cache_scope')
    with n.f.bridge._native_runtime_operation():
        select(n)
        lease = bridge.LaunchdAccessBridge._native_runtime_lease(n.f.bridge)
        module.verify(n.f.root, expected_digest=n.f.root.name, _operation=lease)
        cached = copy.deepcopy(lease.custody._cache[str(n.target)])
        inventory = module.inventory(n.f.root, _operation=lease)
        inventory['runtime/openclaw.mjs'][3] = '0' * 64
        assert lease.custody._cache[str(n.target)] == cached
        with pytest.raises(module.BundleError, match='operation-unqualified'):
            module.verify(n.f.root, _operation={})
        other = copy.copy(n.f.bridge)
        with other._native_runtime_operation():
            select(n, other)
            with pytest.raises(module.BundleError, match='operation-unqualified'):
                module.verify(n.f.root, _operation=lease)
    assert lease.custody._cache == {}
    with pytest.raises(module.BundleError, match='operation-unqualified'):
        module.verify(n.f.root, _operation=lease)
    n.reads.clear()
    module.verify(n.f.root, expected_digest=n.f.root.name)  # Default remains cold.
    assert sum(n.reads) > 0


@pytest.mark.parametrize('change', ['absolute-internal-link', 'setuid'])
def test_warm_bundle_keeps_cold_link_and_setuid_grammar(native_runtime, change):
    n = native_runtime
    module = helper(n, 'actual_bundle_grammar')
    with n.f.bridge._native_runtime_operation():
        select(n)
        lease = bridge.LaunchdAccessBridge._native_runtime_lease(n.f.bridge)
        module.verify(n.f.root, expected_digest=n.f.root.name, _operation=lease)
        if change == 'absolute-internal-link':
            (n.target.parent / 'absolute-link').symlink_to(n.target)
        else:
            n.target.chmod(0o4644)
        with pytest.raises(module.BundleError):
            module.verify(n.f.root, expected_digest=n.f.root.name)
        with pytest.raises(module.BundleError):
            module.verify(n.f.root, expected_digest=n.f.root.name, _operation=lease)


def test_trusted_legacy_helper_signature_remains_cold(native_runtime):
    n = native_runtime
    file = n.f.source / 'installers/macos/lib/pixel-runtime-bundle.py'
    file.write_bytes(file.read_bytes() + b'\n_verify_actual = verify\n'
        b'def verify(root, *, expected_digest=None):\n'
        b'    return _verify_actual(root, expected_digest=expected_digest)\n')
    # This explicitly qualified fixture models an older trusted helper API.
    # Update its real metadata before the operation, never during cache use.
    body = json.loads(n.manifest.read_text())
    body['source'] = runtime.tree_manifest(n.f.source)
    n.manifest.write_text(json.dumps(body) + '\n')
    descriptor = json.loads(n.descriptor.read_text())
    descriptor['manifestSha256'] = hashlib.sha256(n.manifest.read_bytes()).hexdigest()
    n.descriptor.write_text(json.dumps(descriptor))
    with n.f.bridge._native_runtime_operation():
        first = n.f.bridge.connection_deployment()
        qualifier = n.inspections[-1]
        n.reads.clear()
        assert n.f.bridge.connection_deployment() == first
        assert n.inspections[-1] is qualifier and sum(n.reads) > 0


def test_nested_deadlines_and_exception_exit_are_unchanged(monkeypatch):
    adapter = object.__new__(bridge.LaunchdAccessBridge)
    clock = [100.0]
    monkeypatch.setattr(bridge.time, 'monotonic', lambda: clock[0])
    with adapter.bounded(30):
        outer = bridge.LaunchdAccessBridge._native_runtime_lease(adapter)
        assert bridge._DEADLINE.get() == 130
        clock[0] = 105
        with adapter.bounded(5):
            assert bridge._DEADLINE.get() == 110 and bridge.remaining(50) == 5
            assert bridge.LaunchdAccessBridge._native_runtime_lease(adapter) is outer
            clock[0] = 108
        assert outer.active and bridge._DEADLINE.get() == 130 and bridge.remaining(50) == 22
    assert not outer.active and bridge._DEADLINE.get() is None
    with pytest.raises(bridge.AccessError, match='operation-deadline-exceeded'):
        with adapter.bounded(1):
            expired = bridge.LaunchdAccessBridge._native_runtime_lease(adapter)
            clock[0] += 2
    assert not expired.active and bridge._DEADLINE.get() is None
    with pytest.raises(ValueError):
        with adapter._native_runtime_operation():
            failed = bridge.LaunchdAccessBridge._native_runtime_lease(adapter)
            raise ValueError('fixture exception')
    assert not failed.active and bridge._NATIVE_RUNTIME_OPERATION.get() is None


def test_other_bridge_and_copied_thread_do_not_borrow_active_qualifier(native_runtime):
    n = native_runtime
    other = copy.copy(n.f.bridge)
    with n.f.bridge._native_runtime_operation():
        select(n)
        original = n.inspections[-1]
        select(n, other)
        assert n.inspections[-1] is not original and n.inspections[-1].bridge is other
        with other._native_runtime_operation():
            select(n, other)
            own = n.inspections[-1]
            select(n, other)
            assert n.inspections[-1] is own and own is not original
        assert select(n)
        assert n.inspections[-1] is original
        copied, errors, qualifiers = contextvars.copy_context(), [], []

        def child():
            try:
                select(n)
                qualifiers.append(n.inspections[-1])
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=lambda: copied.run(child))
        worker.start()
        worker.join(timeout=10)
        assert not worker.is_alive() and not errors
        assert qualifiers and qualifiers[0] is not original


def test_context_copy_after_exit_is_revoked_and_instance_inspect_cannot_replace_method(native_runtime):
    n = native_runtime
    with n.f.bridge._native_runtime_operation():
        select(n)
        original = n.inspections[-1]
        original.inspect = lambda: pytest.fail('instance method must not replace RuntimeCustody.inspect')
        select(n)
        assert n.inspections[-1] is original
        copied = contextvars.copy_context()
    assert original._cache == {}
    copied.run(select, n)
    assert n.inspections[-1] is not original
    seen = []

    def later():
        with n.f.bridge._native_runtime_operation():
            select(n)
            seen.append(n.inspections[-1])

    copied.run(later)
    assert seen[0] is not original and seen[0]._cache == {}


def test_subclass_remains_cold_and_systemd_bounded_does_not_create_native_scope(native_runtime):
    n = native_runtime

    class ReadOnlyBridge(bridge.LaunchdAccessBridge):
        pass

    adapter = object.__new__(ReadOnlyBridge)
    adapter.__dict__.update(n.f.bridge.__dict__)
    with adapter.bounded(30):
        select(n, adapter)
        first = n.inspections[-1]
        select(n, adapter)
        assert n.inspections[-1] is not first
        assert bridge.LaunchdAccessBridge._native_runtime_lease(adapter) is None
    with pytest.raises(source.CustodyError, match='deployment-required'):
        source.native_source_selection(adapter, n.f.value)
    linux = object.__new__(bridge.SystemdAccessBridge)
    with linux.bounded(30):
        assert bridge._NATIVE_RUNTIME_OPERATION.get() is None


@pytest.fixture
def code_loaders(native_runtime, monkeypatch):
    return code_loader_driver(native_runtime, monkeypatch)


def code_loader_driver(n, monkeypatch):
    """Real FD reader; only external directory UID/mode observations are modeled."""
    import builtins
    from pixel_connections import planner
    actual_compile, actual_module = builtins.compile, planner.importlib.util.module_from_spec
    actual_verify_fd, actual_tree = mac._verify_fd, mac.protected_tree_metadata
    # The existing fixture models Root metadata below its disposable roots.
    # /tmp and ancestors outside that namespace are not installation custody.
    ancestors = {(path.stat().st_dev, path.stat().st_ino)
        for root in (n.f.source, n.f.root) for path in root.parents}
    def verify_fd(fd, *, directory):
        info = os.fstat(fd)
        if directory and (info.st_dev, info.st_ino) in ancestors:
            mac._require_no_acl(fd)
            return info
        return actual_verify_fd(fd, directory=directory)
    monkeypatch.setattr(mac, '_verify_fd', verify_fd)
    trace = SimpleNamespace(compiles=[], modules=[], reads=[], trees=[])
    def compile_body(body, filename, *args, **kwargs):
        if str(filename).startswith(str(n.f.source) + '/'):
            trace.compiles.append(str(filename))
        return actual_compile(body, filename, *args, **kwargs)
    def module(spec):
        result = actual_module(spec)
        if spec.name in ('_connection_bundle', 'qualified_native_source_installer_old_publisher', 'verify_native_source'):
            trace.modules.append(result)
        return result
    def read(path, **kwargs):
        trace.reads.append(Path(path))
        return _actual_protected_bytes(path, **kwargs)
    def tree(path):
        trace.trees.append(Path(path))
        return actual_tree(path)
    monkeypatch.setattr(builtins, 'compile', compile_body)
    monkeypatch.setattr(planner.importlib.util, 'module_from_spec', module)
    monkeypatch.setattr(mac, 'protected_bytes', read)
    monkeypatch.setattr(mac, 'protected_tree_metadata', tree)
    return n, trace


def test_planner_code_hit_keeps_fresh_reads_tree_proof_and_module_globals(code_loaders):
    n, trace = code_loaders
    path = n.f.source / 'installers/macos/lib/pixel-runtime-bundle.py'
    with n.f.bridge._native_runtime_operation():
        first = n.f.bridge.connection_deployment()
        trace.modules[-1].fixture_poison = object()
        before = (trace.reads.count(path), len(trace.trees), len(n.acl_reads))
        assert n.f.bridge.connection_deployment() == first
        assert trace.compiles.count(str(path)) == 1
        assert trace.reads.count(path) > before[0] and len(trace.trees) > before[1]
        assert len(n.acl_reads) > before[2]
        assert trace.modules[-2] is not trace.modules[-1]
        assert not hasattr(trace.modules[-1], 'fixture_poison')
        module = trace.modules[-1]
        assert module.__file__ == str(path) and module.__spec__.origin == str(path)
        assert module.__loader__ is module.__spec__.loader and module.__package__ == module.__spec__.parent


@pytest.mark.parametrize('change', ['bytes', 'inode', 'acl', 'mode', 'symlink', 'missing'])
def test_planner_code_hit_cannot_hide_fresh_source_drift(code_loaders, change):
    n, trace = code_loaders
    path = n.f.source / 'installers/macos/lib/pixel-runtime-bundle.py'
    with n.f.bridge._native_runtime_operation():
        n.f.bridge.connection_deployment()
        compiled = len(trace.compiles)
        if change == 'bytes':
            info = path.stat()
            path.write_bytes(path.read_bytes().replace(b'import os', b'import io', 1))
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
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
            target = path.with_suffix('.moved')
            path.rename(target)
            path.symlink_to(target)
        else:
            path.unlink()
        with pytest.raises((bridge.AccessError, mac.CustodyError, OSError)):
            n.f.bridge.connection_deployment()
        assert len(trace.compiles) == compiled


def old_publisher_authority(n, monkeypatch):
    path = n.f.source / 'installers/macos/lib/pixel-native-source-broker.py'
    path.write_bytes((ROOT / 'installers/macos/lib/pixel-native-source-broker.py').read_bytes())
    path.chmod(0o644)
    descriptor = json.loads(n.descriptor.read_bytes())
    manifest = json.loads(n.manifest.read_bytes())
    manifest['source'] = runtime.tree_manifest(n.f.source)
    body = json.dumps(manifest).encode()
    descriptor['manifestSha256'] = hashlib.sha256(body).hexdigest()
    monkeypatch.setattr(source, 'NATIVE_RUNTIME_DESCRIPTOR', n.descriptor)
    monkeypatch.setattr(source, 'NATIVE_RUNTIME_MANIFEST', n.manifest)
    monkeypatch.setattr(source, 'NATIVE_RUNTIMES', n.f.root.parent)
    authority = {'files': [{'path': str(n.descriptor), 'before': json.dumps(descriptor).encode()},
                          {'path': str(n.manifest), 'before': body}],
        'proof': {'runtime': {'custodySha256': runtime.digest([descriptor, manifest])}},
        'transaction': {'currentDigest': n.f.root.name}}
    return path, authority


def test_old_publisher_code_hit_requalifies_immutable_proof_and_new_globals(code_loaders, monkeypatch):
    n, trace = code_loaders
    path, authority = old_publisher_authority(n, monkeypatch)
    actual_tree, proofs, caches = runtime.tree_manifest, [], []
    def tree(root, *args, **kwargs):
        proofs.append(Path(root))
        caches.append(args[0] if args else kwargs.get('cache'))
        return actual_tree(root, *args, **kwargs)
    monkeypatch.setattr(runtime, 'tree_manifest', tree)
    with n.f.bridge._native_runtime_operation():
        first = source._native_installer_old_publisher(authority, _bridge=n.f.bridge)
        first.fixture_poison = object()
        before = (trace.reads.count(path), len(proofs), len(n.acl_reads))
        second = source._native_installer_old_publisher(authority, _bridge=n.f.bridge)
        assert second is not first and not hasattr(second, 'fixture_poison')
        assert second.__file__ == str(path) and second.__spec__.origin == str(path)
        assert second.__loader__ is second.__spec__.loader and second.__package__ == second.__spec__.parent
        assert trace.compiles.count(str(path)) == 1
        assert trace.reads.count(path) > before[0] and len(proofs) > before[1] and len(n.acl_reads) > before[2]
        lease = n.f.bridge._native_runtime_lease()
        assert caches and all(cache is lease.custody._cache for cache in caches)
        authority['proof']['runtime']['custodySha256'] = '0' * 64
        with pytest.raises(source.CustodyError, match='old-runtime-unqualified'):
            source._native_installer_old_publisher(authority, _bridge=n.f.bridge)
        assert trace.compiles.count(str(path)) == 1


@pytest.mark.parametrize('change', ['bytes', 'inode', 'acl', 'mode', 'symlink', 'missing'])
def test_old_publisher_code_hit_cannot_hide_current_source_drift(code_loaders, monkeypatch, change):
    n, trace = code_loaders
    path, authority = old_publisher_authority(n, monkeypatch)
    with n.f.bridge._native_runtime_operation():
        source._native_installer_old_publisher(authority, _bridge=n.f.bridge)
        if change == 'bytes':
            path.write_bytes(path.read_bytes() + b'\n# current unexpected body\n')
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
            target = path.with_suffix('.moved')
            path.rename(target)
            path.symlink_to(target)
        else:
            path.unlink()
        with pytest.raises((source.CustodyError, bridge.AccessError, mac.CustodyError, OSError)):
            source._native_installer_old_publisher(authority, _bridge=n.f.bridge)
        assert trace.compiles.count(str(path)) == 1


@pytest.mark.parametrize('loader', ['planner', 'old-publisher'])
def test_safe_new_inode_rechecks_custody_without_inventing_inode_pinning(code_loaders, monkeypatch, loader):
    n, trace = code_loaders
    path = n.f.source / 'installers/macos/lib/pixel-runtime-bundle.py'
    if loader == 'old-publisher':
        path, authority = old_publisher_authority(n, monkeypatch)
        call = lambda: source._native_installer_old_publisher(authority, _bridge=n.f.bridge)
    else:
        call = n.f.bridge.connection_deployment
    with n.f.bridge._native_runtime_operation():
        first = call()
        old_inode, before_reads, before_acl = path.stat().st_ino, trace.reads.count(path), len(n.acl_reads)
        pending = path.with_suffix('.next')
        pending.write_bytes(path.read_bytes())
        pending.chmod(0o644)
        os.replace(pending, path)
        assert path.stat().st_ino != old_inode
        second = call()
        if loader == 'planner':
            assert second == first
        else:
            assert second is not first
        assert trace.reads.count(path) > before_reads and len(n.acl_reads) > before_acl
        assert trace.compiles.count(str(path)) == 1


def test_warm_code_does_not_bypass_current_operation_deadline(code_loaders, monkeypatch):
    n, trace = code_loaders
    clock = [100.0]
    monkeypatch.setattr(bridge.time, 'monotonic', lambda: clock[0])
    with pytest.raises(bridge.AccessError, match='operation-deadline-exceeded'):
        with n.f.bridge.bounded(30):
            n.f.bridge.connection_deployment()
            compiled, modules = len(trace.compiles), len(trace.modules)
            lease = n.f.bridge._native_runtime_lease()
            clock[0] += 31
            with pytest.raises(bridge.AccessError, match='operation-deadline-exceeded'):
                n.f.bridge.connection_deployment()
            assert len(trace.modules) == modules
    assert len(trace.compiles) == compiled and not lease.active and lease.code == {}
