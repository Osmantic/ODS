"""Closed kit metadata contracts, not Seatbelt/launchd policy qualification."""
# ruff: noqa: E402,F811 -- imported pytest fixture names are also test parameters.
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

if not sys.platform.startswith('linux') or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs FD harness; Darwin metadata/ACL explicitly simulated', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_connection_deployment_paths import deployment  # noqa: F401
from test_native_source_custody import native  # noqa: F401
from pixel_connections import custody as source


def complete_kit(n):
    manifest = json.loads((n.kit/'source-kit.json').read_bytes())
    manifest['schemaVersion'] = 2
    for name in source.NATIVE_PROFILES:
        body = ('(version 1)\n(deny default)\n; invented '+name+'\n').encode()
        (n.kit/name).write_bytes(body)
        (n.kit/name).chmod(0o644)
        manifest['files'][name] = hashlib.sha256(body).hexdigest()
    (n.kit/'source-kit.json').write_text(json.dumps(manifest))
    return manifest


def test_preparation_prefix_never_qualifies_complete_profiles(native):
    n = native
    manifest, _digest = source.native_verify_kit(n.selected)
    assert manifest['schemaVersion'] == 1
    with pytest.raises(source.CustodyError, match='profiles-incomplete'):
        source.native_verify_kit(n.selected, require_profiles=True)
    assert not (n.metadata/'source-custody-prepare.json').exists()


def test_only_all_six_hash_bound_profiles_qualify_readonly(native, monkeypatch):
    n = native
    expected = complete_kit(n)
    monkeypatch.setattr(source.subprocess, 'run', lambda *a, **kw: pytest.fail('No actor or publication'))
    manifest, checksum = source.native_verify_kit(n.selected, require_profiles=True)
    assert manifest == expected and len(manifest['files']) == 8
    assert len(source.NATIVE_PROFILES) == 6
    assert checksum == hashlib.sha256((n.kit/'source-kit.json').read_bytes()).hexdigest()
    assert set(os.listdir(n.kit)) == {'source-kit.json','broker.py','source-broker.env',*source.NATIVE_PROFILES}
    assert not (n.metadata/'source-custody-prepare.json').exists()


@pytest.mark.parametrize('change', ['missing','extra','hash','version','mode','link','acl'])
def test_partial_or_drifted_profiles_are_preserved_and_never_adopted(native, change):
    n = native
    complete_kit(n)
    path = n.kit/source.NATIVE_PROFILES[0]
    if change == 'missing': path.unlink()
    elif change == 'extra': (n.kit/'unknown.sb').write_text('preserve')
    elif change == 'hash': path.write_text('changed profile')
    elif change == 'version':
        file = n.kit/'source-kit.json'
        manifest = json.loads(file.read_bytes())
        manifest['schemaVersion'] = 1
        file.write_text(json.dumps(manifest))
    elif change == 'mode': path.chmod(0o666)
    elif change == 'link': os.link(path,n.world/'outside-profile-hardlink')
    else:
        with path.open('rb') as stream:
            n.acl.acls[n.acl.key(stream.fileno())] = {'entries':[n.acl.entry(n.identity['id'])]}
    before = {p.name:p.read_bytes() for p in n.kit.iterdir()}
    writes = n.acl.writes
    with pytest.raises((source.CustodyError, OSError)): source.native_verify_kit(n.selected,require_profiles=True)
    assert {p.name:p.read_bytes() for p in n.kit.iterdir()} == before and n.acl.writes == writes


def test_a_prepared_descriptor_is_not_silently_rebound_to_a_new_kit(native):
    n = native
    source.native_prepare_state(n.selected,n.identity)
    before = (source.NATIVE_STATE/'custody.json').read_bytes()
    complete_kit(n)
    source.native_verify_kit(n.selected,require_profiles=True)
    with pytest.raises(source.CustodyError,match='state-intent-changed'):
        source.native_prepare_state(n.selected,n.identity)
    assert (source.NATIVE_STATE/'custody.json').read_bytes() == before
