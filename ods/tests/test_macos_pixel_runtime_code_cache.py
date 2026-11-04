"""Code objects only: revocable Root scope, fresh module state, bounded storage.

These portable tests do not qualify filesystem custody. Actual protected reads
and independent Source proofs are exercised by the native runtime fixture.
"""
import builtins
import contextvars
import copy
import importlib.util
from pathlib import Path
import sys
import threading
from types import CodeType

import pytest

if sys.platform == 'win32':
    pytest.skip('POSIX bridge module', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import pixel_access_bridge as bridge  # noqa: E402


@pytest.fixture
def helper_file(tmp_path):
    path = tmp_path.resolve() / 'installers/macos/lib/pixel-runtime-bundle.py'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'state = []\n')
    return path


def adapter():
    return object.__new__(bridge.LaunchdAccessBridge)


def test_code_reuses_compilation_but_never_module_globals(helper_file, monkeypatch):
    owner, calls = adapter(), []
    actual = builtins.compile
    def compile_body(*args, **kwargs):
        if args[1] == str(helper_file):
            calls.append((args, kwargs))
        return actual(*args, **kwargs)
    monkeypatch.setattr(builtins, 'compile', compile_body)
    with owner._native_runtime_operation():
        lease = owner._native_runtime_lease()
        first = bridge._native_runtime_code(lease, helper_file, helper_file.read_bytes())
        second = bridge._native_runtime_code(lease, helper_file, helper_file.read_bytes())
        assert first is second and type(first) is CodeType and len(calls) == 1
        spec = importlib.util.spec_from_file_location('disposable_helper', helper_file)
        one, two = importlib.util.module_from_spec(spec), importlib.util.module_from_spec(spec)
        exec(first, one.__dict__)
        one.state.append('first execution only')
        exec(second, two.__dict__)
        assert two.state == [] and one.state is not two.state
        assert two.__file__ == str(helper_file) and two.__spec__ is spec
        assert two.__loader__ is spec.loader and two.__package__ == spec.parent
        assert all(type(value) is CodeType for value in lease.code.values())
    assert not lease.active and lease.code == {}
    with owner._native_runtime_operation():
        bridge._native_runtime_code(owner._native_runtime_lease(), helper_file, helper_file.read_bytes())
        assert len(calls) == 2


def test_fresh_bytes_and_canonical_filename_choose_compilation(helper_file):
    owner = adapter()
    with owner._native_runtime_operation():
        lease = owner._native_runtime_lease()
        before = bridge._native_runtime_code(lease, helper_file, b'value = 1\n')
        after = bridge._native_runtime_code(lease, helper_file, b'value = 2\n')
        assert after is not before
        values = {}
        exec(after, values)
        assert values['value'] == 2
        alternate = helper_file.parent / 'pixel-native-source-broker.py'
        alternate.write_bytes(b'value = 2\n')
        other = bridge._native_runtime_code(lease, alternate, alternate.read_bytes())
        assert other is not after and other.co_filename == str(alternate)
        assert bridge._native_runtime_code(lease, helper_file, b'value = 2\n') is after


def test_code_cache_bound_and_failed_compiles_are_not_retained(helper_file):
    owner = adapter()
    with owner._native_runtime_operation():
        lease = owner._native_runtime_lease()
        for number in range(40):
            bridge._native_runtime_code(lease, helper_file, ('value = ' + str(number)).encode())
            assert 1 <= len(lease.code) <= 8
        before = dict(lease.code)
        for _ in range(2):
            with pytest.raises(SyntaxError):
                bridge._native_runtime_code(lease, helper_file, b'invalid syntax =\n')
            assert lease.code == before
        body = b'#' + b'x' * (2 * 1024 * 1024)
        oversized = bridge._native_runtime_code(lease, helper_file, body)
        assert type(oversized) is CodeType and lease.code == before


@pytest.mark.parametrize('change', ['seal', 'inactive', 'bridge', 'dictionary', 'duck'])
def test_unqualified_lease_never_compiles(helper_file, monkeypatch, change):
    owner = adapter()
    with owner._native_runtime_operation():
        lease = owner._native_runtime_lease()
        invalid = copy.copy(lease)
        if change == 'seal':
            invalid.seal = object()
        elif change == 'inactive':
            invalid.active = False
        elif change == 'bridge':
            invalid.bridge = adapter()
        elif change == 'dictionary':
            invalid.code = []
        else:
            invalid = {}
        monkeypatch.setattr(builtins, 'compile', lambda *args, **kwargs: pytest.fail('unqualified compilation'))
        with pytest.raises(bridge.AccessError, match='operation-unqualified'):
            bridge._native_runtime_code(invalid, helper_file, b'pass')


def test_nested_other_bridge_thread_and_revoked_context_cannot_borrow(helper_file):
    owner, other, errors = adapter(), adapter(), []
    body = helper_file.read_bytes()
    with owner._native_runtime_operation():
        lease = owner._native_runtime_lease()
        code = bridge._native_runtime_code(lease, helper_file, body)
        with owner._native_runtime_operation():
            assert owner._native_runtime_lease() is lease
            assert bridge._native_runtime_code(lease, helper_file, body) is code
        with other._native_runtime_operation():
            second = other._native_runtime_lease()
            with pytest.raises(bridge.AccessError, match='operation-unqualified'):
                bridge._native_runtime_code(lease, helper_file, body)
            assert bridge._native_runtime_code(second, helper_file, body) is not code
        assert second.code == {} and bridge._native_runtime_code(lease, helper_file, body) is code
        copied = contextvars.copy_context()
        def child():
            try:
                bridge._native_runtime_code(lease, helper_file, body)
            except bridge.AccessError as error:
                errors.append(error.code)
        worker = threading.Thread(target=lambda: copied.run(child))
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive() and errors == ['provider-runtime-operation-unqualified']
    assert lease.code == {}
    with pytest.raises(bridge.AccessError, match='operation-unqualified'):
        copied.run(bridge._native_runtime_code, lease, helper_file, body)


@pytest.mark.parametrize('change', ['family', 'symlink', 'relative', 'body', 'cached-type', 'cached-filename'])
def test_fixed_path_body_and_cached_code_grammar(helper_file, change):
    owner = adapter()
    with owner._native_runtime_operation():
        lease = owner._native_runtime_lease()
        body = helper_file.read_bytes()
        bridge._native_runtime_code(lease, helper_file, body)
        path = helper_file
        if change == 'family':
            path = helper_file.parent / 'other.py'
            path.write_bytes(body)
        elif change == 'symlink':
            path = helper_file.parent / 'pixel-native-source-broker.py'
            path.symlink_to(helper_file)
        elif change == 'relative':
            path = Path('installers/macos/lib/pixel-runtime-bundle.py')
        elif change == 'body':
            body = bytearray(body)
        else:
            key = next(iter(lease.code))
            lease.code[key] = object() if change == 'cached-type' else compile(body, '/unrelated.py', 'exec')
        with pytest.raises(bridge.AccessError, match='operation-unqualified'):
            bridge._native_runtime_code(lease, path, body)


def test_exception_exit_revokes_code_cache(helper_file):
    owner = adapter()
    with pytest.raises(ValueError):
        with owner._native_runtime_operation():
            lease = owner._native_runtime_lease()
            bridge._native_runtime_code(lease, helper_file, helper_file.read_bytes())
            raise ValueError('fixture')
    assert not lease.active and lease.code == {}


def test_compile_semantics_do_not_inherit_bridge_future_flags(helper_file):
    import __future__
    owner = adapter()
    with owner._native_runtime_operation():
        lease = owner._native_runtime_lease()
        plain = bridge._native_runtime_code(lease, helper_file, b'debug = __debug__\n')
        future = bridge._native_runtime_code(lease, helper_file,
            b'from __future__ import annotations\ndef value(arg: Missing): pass\n')
        assert not plain.co_flags & __future__.annotations.compiler_flag
        assert future.co_flags & __future__.annotations.compiler_flag
        namespace = {}
        exec(plain, namespace)
        assert namespace['debug'] is (not sys.flags.optimize)
