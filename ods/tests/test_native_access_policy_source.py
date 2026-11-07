"""The cache reads one canonical literal policy list without executing JS."""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
from pixel_connections import native_access as access, native_mcp as native  # noqa: E402


@pytest.fixture
def canonical(tmp_path, monkeypatch):
    adapter = tmp_path / 'native_mcp_oauth.mjs'
    package = tmp_path / 'package'
    (package / 'dist').mkdir(parents=True)
    entries = {}
    for name in ('materialize', 'effective', 'sandbox', 'names'):
        raw = ('controlled-' + name).encode()
        filename = f'dist/qa-{name}.js'
        (package / filename).write_bytes(raw)
        (package / filename).chmod(0o644)
        entries[name] = [filename, hashlib.sha256(raw).hexdigest()]
    text = 'const POLICY_MODULES = {\n' + ''.join(f'  {name}: {json.dumps(value)},\n' for name, value in entries.items()) + '};\n'
    adapter.write_text(text)
    adapter.chmod(0o644)
    monkeypatch.setattr(access, 'ADAPTER', adapter)
    return adapter, package, {'packageDir': str(package), 'ownerUid': os.getuid()}


def test_canonical_policy_list_is_read_as_literal_and_changed_bytes_refuse_ready(canonical):
    _adapter, package, binding = canonical
    digest = access.policy_fingerprint(binding)
    assert len(digest) == 64 and digest == access.policy_fingerprint(binding)
    (package / 'dist/qa-effective.js').write_text('changed')
    with pytest.raises(native.NativeError, match='native-app-source-unavailable'):
        access.policy_fingerprint(binding)


def test_nonliteral_policy_source_is_never_executed(canonical):
    adapter, _package, binding = canonical
    marker = adapter.parent / 'must-not-exist'
    adapter.write_text('const POLICY_MODULES = {\n  materialize: __import__("pathlib").Path(' + repr(str(marker)) + ').touch(),\n};\n')
    with pytest.raises(native.NativeError, match='native-app-source-unavailable'):
        access.policy_fingerprint(binding)
    assert not marker.exists()


def test_policy_symlink_or_writable_source_is_not_accepted(canonical):
    adapter, _package, binding = canonical
    adapter.chmod(0o666)
    with pytest.raises(native.NativeError, match='native-app-source-unavailable'):
        access.policy_fingerprint(binding)


@pytest.mark.skipif(not os.environ.get('OPENCLAW_PACKAGE_DIR'), reason='Set the qualified pinned SDK directory')
def test_current_protected_adapter_joins_the_actual_reviewed_sdk_bytes(tmp_path, monkeypatch):
    adapter = tmp_path / 'native_mcp_oauth.mjs'
    adapter.write_bytes((ROOT / 'extensions/services/pixel-agent/host/native_mcp_oauth.mjs').read_bytes())
    adapter.chmod(0o644)
    monkeypatch.setattr(access, 'ADAPTER', adapter)
    result = access.policy_fingerprint({'packageDir': os.environ['OPENCLAW_PACKAGE_DIR'], 'ownerUid': os.getuid()})
    assert len(result) == 64
