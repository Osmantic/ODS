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


@pytest.fixture
def native_runtime(deployment, monkeypatch):  # noqa: F811
    f = deployment
    original_stat, original_lstat, original_fstat = os.stat, os.lstat, os.fstat
    original_fdopen = os.fdopen
    descriptor = runtime.DESCRIPTOR
    manifest = Path(json.loads(descriptor.read_text())['manifest'])
    descriptor.chmod(0o644)
    manifest.chmod(0o644)
    protected = {descriptor, manifest, Path(f.bridge.binary), f.plist, f.root.parent.parent / 'python'}
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
