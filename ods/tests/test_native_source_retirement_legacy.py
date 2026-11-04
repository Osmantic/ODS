"""Actual immutable descriptor-only Core classification; no Source adoption."""
# ruff: noqa: E402,F401,F811
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs Apple metadata harness, not physical Darwin', allow_module_level=True)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from pixel_connections import custody as c
from pixel_provider import runtime_custody
import pixel_access_bridge as bridge

spec = importlib.util.spec_from_file_location('retirement_legacy_classification', ROOT / 'installers/macos/lib/pixel-native-uninstall.py')
retirement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retirement)


@pytest.fixture
def old_core(native, monkeypatch):
    n = native
    # Remove only the test's unpublished inert images/retained identity before
    # any product operation; the underlying canonical legacy runtime is real.
    for path in n.kit.iterdir(): path.unlink()
    n.kit.rmdir()
    (n.metadata / 'source-identity.json').unlink()
    n.records['Users'] = n.records['Groups'] = None
    descriptor = json.loads(runtime_custody.DESCRIPTOR.read_bytes())
    monkeypatch.setattr(c, 'NATIVE_RUNTIME_DESCRIPTOR', runtime_custody.DESCRIPTOR)
    monkeypatch.setattr(c, 'NATIVE_RUNTIME_MANIFEST', Path(descriptor['manifest']))
    monkeypatch.setattr(c, 'NATIVE_RUNTIMES', n.f.root.parent)
    monkeypatch.setattr(c, 'NATIVE_LOGS', n.world / 'logs')
    monkeypatch.setattr(n.f.bridge, 'state', n.metadata, raising=False)
    raw_clock = lambda: time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    monkeypatch.setattr(bridge, '_native_source_clock_ns', raw_clock)
    monkeypatch.setattr(c, '_native_clock_ns', raw_clock)
    monkeypatch.setattr(c, '_native_boot_uuid', lambda: '01234567-89AB-4DEF-8123-0123456789AB')
    def command(argv, **_kwargs):
        if argv == ['/bin/launchctl', 'print', 'system']: return 'system = {\n}\n'
        if argv == ['/bin/ps', '-axo', 'pid=,uid=']: return '1 0\n'
        raise AssertionError('No job mutation or other command: ' + repr(argv))
    monkeypatch.setattr(n.f.bridge, 'command', command)
    with n.f.bridge.bounded(300): yield n


def test_known_descriptor_only_old_core_has_no_source_data_or_authority(old_core):
    n = old_core
    proof = retirement._legacy_source_descriptor_proof(n.f.bridge)
    assert proof['state'][:3] == (None, {}, {'kind': 'absent'})
    assert retirement._verify_legacy_source_descriptor_proof(n.f.bridge, proof) is None
    assert not list(n.metadata.iterdir())
    assert not c.NATIVE_STATE.exists() and not n.kit.exists()


@pytest.mark.parametrize('fault', ['private-state', 'metadata', 'account', 'runtime', 'loaded-source', 'new-boot'])
def test_descriptor_only_classification_preserves_unknown_or_changed_source(old_core, monkeypatch, fault):
    n = old_core
    proof = retirement._legacy_source_descriptor_proof(n.f.bridge)
    if fault == 'private-state':
        c.NATIVE_STATE.mkdir(mode=0o700)
        (c.NATIVE_STATE / 'private-marker').write_bytes(b'MUST_BE_PRESERVED_UNREAD')
    elif fault == 'metadata':
        path = n.metadata / 'source-unknown.json'; path.write_bytes(b'{}'); path.chmod(0o600)
    elif fault == 'account':
        n.records['Users'] = {'UniqueID': ['60001']}
    elif fault == 'runtime':
        with (n.f.root / 'runtime/openclaw.mjs').open('ab') as stream: stream.write(b'// drift')
    elif fault == 'loaded-source':
        original = n.f.bridge.command
        monkeypatch.setattr(n.f.bridge, 'command', lambda argv, **kw: 'system = {\n' + c.NATIVE_LABEL + 'sync\n}\n'
            if argv == ['/bin/launchctl', 'print', 'system'] else original(argv, **kw))
    else:
        monkeypatch.setattr(c, '_native_boot_uuid', lambda: 'AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE')
    before = {p: p.stat().st_ino for p in n.metadata.iterdir()}
    with pytest.raises((ValueError, c.CustodyError, bridge.AccessError)):
        retirement._verify_legacy_source_descriptor_proof(n.f.bridge, proof)
    assert {p: p.stat().st_ino for p in n.metadata.iterdir()} == before
    assert not (n.metadata / 'retirement.json').exists()
