"""First-v1 publication interruption and refusal controls on actual Root files."""
# ruff: noqa: E402,F401,F811
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux metadata driver, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_initial_backend import (initial, material, native, deployment, built, source,
    package, publication, helper, qualification_operation, fresh_file_metadata)
from test_native_source_installer_withdrawal import files
from pixel_connections import custody as c
from test_native_source_custody import AppleACL


def test_initial_unqualified_new_inode_does_not_retain_a_retired_environment_acl(tmp_path, monkeypatch):
    world = tmp_path / 'native'
    world.mkdir()
    acl = AppleACL()
    driver = SimpleNamespace(world=world, acl=acl, owners={}, real_fstat=os.fstat)
    original_open = os.open
    retired_acl = {'entries': [{'tag': 1, 'mask': 1 << 1, 'flags': 0, 'uuid': b'R' * 16}], 'flags': 0}
    def recycled(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if flags & os.O_CREAT and flags & os.O_EXCL:
            info = driver.real_fstat(fd)
            key = (info.st_dev, info.st_ino)
            # A retired env inode can be reused by the next metadata file.
            # Only the simulated side tables still remember the old inode.
            acl.acls[key] = retired_acl
            driver.owners[key] = (60001, 60001)
        return fd
    monkeypatch.setattr(os, 'open', recycled)
    monkeypatch.setattr(c, '_native_acl_library', lambda: acl)
    fresh_file_metadata(driver, monkeypatch)
    path = world / 'fresh-metadata.json'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        c._native_no_acl(fd)
        info = driver.real_fstat(fd)
        key = (info.st_dev, info.st_ino)
        assert key not in driver.owners
        # Reopening an existing file must retain the modeled ACL and refusal.
        acl.acls[key] = retired_acl
    finally:
        os.close(fd)
    fd = os.open(path, os.O_RDONLY | os.O_CREAT, 0o600)
    try:
        with pytest.raises(c.CustodyError, match='^native-source-acl-rights$'):
            c._native_no_acl(fd)
        assert acl.acls[key] == retired_acl
    finally:
        os.close(fd)
    fd = os.open(tmp_path / 'outside.json', os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with pytest.raises(c.CustodyError, match='^native-source-acl-rights$'):
            c._native_no_acl(fd)
    finally:
        os.close(fd)


@pytest.mark.parametrize('where', ['publication-before', 'publication-after', 'child', 'terminal'])
def test_initial_lost_reply_keeps_original_publication_and_fixed_child(initial, monkeypatch, where):
    n = initial
    before = (n.metadata / 'source-publication.json').stat().st_ino
    rename, save = os.rename, c._native_save_at
    hit = []
    def renamed(first, second, **kwargs):
        result = rename(first, second, **kwargs)
        chosen = (where == 'publication-before' and str(second).startswith('.source-initial-publication-')
            and not str(second).startswith('.source-initial-publication-next-')
            or where == 'publication-after' and str(first).startswith('.source-initial-publication-next-')
            or where == 'child' and first == '.gate-next')
        if chosen and not hit:
            hit.append(True)
            raise OSError('invented first-generation lost reply')
        return result
    def saved(selection, directory, name, value, **kwargs):
        result = save(selection, directory, name, value, **kwargs)
        if where == 'terminal' and not hit and name == 'source-initial-generation.json' and value['phase'] == 'committed':
            hit.append(True)
            raise OSError('invented first-generation lost reply')
        return result
    monkeypatch.setattr(os, 'rename', renamed)
    monkeypatch.setattr(c, '_native_save_at', saved)
    with pytest.raises(OSError, match='first-generation lost reply'):
        n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    parent = json.loads((n.metadata / 'source-initial-generation.json').read_bytes())
    monkeypatch.setattr(os, 'rename', rename)
    monkeypatch.setattr(c, '_native_save_at', save)
    n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    final = json.loads((n.metadata / 'source-initial-generation.json').read_bytes())
    assert hit and final['phase'] == 'committed' and final['generation'] == parent['generation']
    assert final['nonce'] == parent['nonce']
    retained = n.metadata / ('.source-initial-publication-' + final['nonce'] + '.json')
    assert retained.stat().st_ino == before and retained.read_bytes() == n.before
    assert helper.verify_initial_generation_child(n.selected) == {'generation': final['generation'], 'phase': 'committed-inactive'}
    snapshot = files(n)
    assert helper.publish_initial_generation(n.selected)['generation'] == final['generation']
    assert files(n) == snapshot
    assert not any(argv[:2] in (['/bin/launchctl', 'bootstrap'], ['/bin/launchctl', 'kickstart']) for argv in n.commands)


@pytest.mark.parametrize('change', ['witness-uid', 'witness-boot', 'grant', 'token', 'unknown-kit', 'prior-execution'])
def test_initial_unqualified_witness_or_private_history_refuses_without_root_reset(initial, monkeypatch, change):
    n = initial
    publish = helper.publish_initial_generation
    snapshots = []
    def changed(selection):
        if change.startswith('witness-'):
            path = n.metadata / 'source-publication.json'
            value = json.loads(path.read_bytes())
            if change == 'witness-uid': value['witness']['brokerUid'] = n.f.bridge.owner.pw_uid
            else: value['witness']['boot'] = '-' * 36
            path.write_bytes(c._native_encoded(value))
        elif change == 'grant':
            text = c.env_text(c.parse_env(c.NATIVE_ENV.read_text()), {'PIXEL_LIMB_EMAIL_ENABLED': '1'})
            c.native_publish_environment(selection, text)
        elif change == 'token':
            path = c.NATIVE_STATE / 'private/google-token.json'
            path.write_bytes(b'opaque fixture token, preserved')
            path.chmod(0o600)
        elif change == 'unknown-kit':
            (n.kit.parent / '.source-kit-unknown').mkdir(mode=0o700)
        else:
            (n.metadata / 'source-execution.json').write_bytes(b'unknown prior authority, preserved')
        snapshots.append(files(n))
        return publish(selection)
    monkeypatch.setattr(helper, 'publish_initial_generation', changed)
    with pytest.raises((ValueError, c.CustodyError)) as failure:
        n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    assert snapshots, f'fault injection did not finish: {failure.value}'
    assert files(n) == snapshots[0]
    assert not (n.metadata / 'source-initial-generation.json').exists()
