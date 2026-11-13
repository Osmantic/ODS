"""Pending first-sign-in boot recovery on real Root files and typed OS driver."""
# ruff: noqa: E402,F401,F811
import json
import os
from pathlib import Path
import plistlib
import sys

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux metadata driver, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_initial_backend import (initial, material, native, deployment, built, source,
    package, publication, helper, qualification_operation, Launchd)
from test_native_source_installer_withdrawal import files
from pixel_connections import custody as c, native_executor as ex


def pending(n, monkeypatch, prefix):
    original = c._native_save_at
    hit = []
    def save(selection, directory, name, value, **kwargs):
        if not hit and prefix == 'complete-child' and name == 'source-initial-generation.json' and value['phase'] == 'committed':
            hit.append(True)
            raise OSError('invented pending initial parent')
        result = original(selection, directory, name, value, **kwargs)
        chosen = (prefix == 'before-publication' and name == 'source-initial-generation.json' and value['phase'] == 'staging'
            or prefix == 'before-child' and name == 'source-initial-generation.json' and value['phase'] == 'generation'
            or prefix == 'partial-child' and name == 'source-jobs-prepare.json' and value['phase'] == 'staging')
        if not hit and chosen:
            hit.append(True)
            raise OSError('invented pending initial parent')
        return result
    monkeypatch.setattr(c, '_native_save_at', save)
    with pytest.raises(OSError, match='pending initial parent'):
        n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    monkeypatch.setattr(c, '_native_save_at', original)
    assert hit
    parent = json.loads((n.metadata / 'source-initial-generation.json').read_bytes())
    publication = c._native_encoded(dict(parent['before']['document'], witness=parent['witness']))
    old_boot = n.boot
    n.boot = '11234567-89AB-4DEF-8123-0123456789AB'
    n.identity = json.loads((n.metadata / 'source-identity.json').read_bytes())
    driver = Launchd(n)
    for path in n.units.iterdir():
        driver.jobs[path.stem] = {'path': path, 'document': plistlib.loads(path.read_bytes()),
            'runs': 0, 'exit': None, 'process': None, 'release': None}
    def command(argv, timeout=20):
        if argv == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']:
            return n.boot + '\n'
        return driver(argv, timeout=timeout)
    monkeypatch.setattr(n.f.bridge, 'command', command)
    return parent, publication, old_boot, driver


@pytest.mark.parametrize('prefix', ['before-publication', 'before-child', 'partial-child', 'complete-child'])
def test_pending_initial_reboot_keeps_publication_fixed_child_and_original_images(initial, monkeypatch, prefix):
    n = initial
    parent, publication_body, old_boot, driver = pending(n, monkeypatch, prefix)
    old_parent = (n.metadata / 'source-initial-generation.json').stat().st_ino
    child_path = n.metadata / 'source-jobs-prepare.json'
    old_child_body = child_path.read_bytes() if child_path.exists() else None
    old_child_inode = child_path.stat().st_ino if child_path.exists() else None
    n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    current = json.loads((n.metadata / 'source-initial-generation.json').read_bytes())
    child = json.loads(child_path.read_bytes())
    ledger = json.loads((n.metadata / 'source-initial-boot.json').read_bytes())
    entry = ledger['transactions'][0]
    assert current['generation'] == child['generation'] == parent['generation']
    assert current['nonce'] == parent['nonce'] and current['phase'] == 'committed'
    assert child['phase'] == 'committed-inactive' and child['witness']['boot'] == n.boot
    assert (n.metadata / 'source-publication.json').read_bytes() == publication_body
    assert entry['beforeBoot'] == old_boot and entry['boot'] == n.boot and entry['phase'] == 'committed'
    names = helper._initial_boot_names(entry)
    assert (n.metadata / names['parent_before']).stat().st_ino == old_parent
    if old_child_body is not None:
        assert (n.metadata / names['child_before']).read_bytes() == old_child_body
        assert (n.metadata / names['child_before']).stat().st_ino == old_child_inode
    assert sorted(row['role'] for row in child['definitions'].values()) == ['direct', 'sync']
    snapshot = files(n)
    n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    assert files(n) == snapshot
    assert not driver.jobs
    assert not any(argv[:2] in (['/bin/launchctl', 'bootstrap'], ['/bin/launchctl', 'kickstart']) for argv in driver.sent)
    assert helper.initial_boot_images(n.selected) is None  # Historical parent cannot control later jobs.


@pytest.mark.parametrize('change', ['token', 'grant', 'execution', 'definition'])
def test_pending_initial_reboot_unqualified_state_preserved(initial, monkeypatch, change):
    n = initial
    pending(n, monkeypatch, 'complete-child')
    if change == 'token':
        path = c.NATIVE_STATE / 'private/google-token.json'
        path.write_bytes(b'opaque retained token')
        path.chmod(0o600)
    elif change == 'grant':
        text = c.env_text(c.parse_env(c.NATIVE_ENV.read_text()), {'PIXEL_LIMB_EMAIL_ENABLED': '1'})
        c.NATIVE_ENV.write_text(text)
    elif change == 'execution':
        (n.metadata / 'source-execution.json').write_bytes(b'unknown prior execution, retained')
    else:
        path = next(n.units.iterdir())
        path.write_bytes(path.read_bytes() + b'\n<!-- replacement -->\n')
    snapshot = files(n)
    with pytest.raises((ValueError, c.CustodyError)):
        n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    assert files(n) == snapshot
    assert not (n.metadata / 'source-initial-boot.json').exists()


@pytest.mark.parametrize('where', ['parent-before', 'parent-after', 'child-before', 'child-after', 'terminal'])
def test_initial_boot_lost_reply_resumes_original_bound_images(initial, monkeypatch, where):
    n = initial
    parent, publication_body, _old_boot, driver = pending(n, monkeypatch, 'complete-child')
    old_parent = (n.metadata / 'source-initial-generation.json').stat().st_ino
    old_child = (n.metadata / 'source-jobs-prepare.json').stat().st_ino
    rename, save = os.rename, c._native_save_at
    hit = []
    def renamed(first, second, **kwargs):
        result = rename(first, second, **kwargs)
        chosen = (where == 'parent-before' and str(second).startswith('.source-initial-parent-before-')
            or where == 'parent-after' and str(first).startswith('.source-initial-parent-next-')
            or where == 'child-before' and str(second).startswith('.source-initial-child-before-')
            or where == 'child-after' and str(first).startswith('.source-initial-child-next-'))
        if chosen and not hit:
            hit.append(True)
            raise OSError('invented initial boot lost reply')
        return result
    def saved(selection, directory, name, value, **kwargs):
        result = save(selection, directory, name, value, **kwargs)
        if (where == 'terminal' and not hit and name == 'source-initial-boot.json'
                and value['transactions'][-1]['phase'] == 'committed'):
            hit.append(True)
            raise OSError('invented initial boot lost reply')
        return result
    monkeypatch.setattr(os, 'rename', renamed)
    monkeypatch.setattr(c, '_native_save_at', saved)
    with pytest.raises(OSError, match='initial boot lost reply'):
        n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    first = json.loads((n.metadata / 'source-initial-boot.json').read_bytes())
    monkeypatch.setattr(os, 'rename', rename)
    monkeypatch.setattr(c, '_native_save_at', save)
    n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    final = json.loads((n.metadata / 'source-initial-boot.json').read_bytes())
    assert hit and len(final['transactions']) == 1 and final['transactions'][0]['phase'] == 'committed'
    assert final['transactions'][0]['nonce'] == first['transactions'][0]['nonce']
    names = helper._initial_boot_names(final['transactions'][0])
    assert (n.metadata / names['parent_before']).stat().st_ino == old_parent
    assert (n.metadata / names['child_before']).stat().st_ino == old_child
    child = json.loads((n.metadata / 'source-jobs-prepare.json').read_bytes())
    assert child['generation'] == parent['generation'] and child['witness']['boot'] == n.boot
    assert (n.metadata / 'source-publication.json').read_bytes() == publication_body
    assert not driver.jobs


def test_second_reboot_during_pending_handoff_preserves_original_transaction(initial, monkeypatch):
    n = initial
    pending(n, monkeypatch, 'before-child')
    save = c._native_save_at
    hit = []
    def saved(selection, directory, name, value, **kwargs):
        result = save(selection, directory, name, value, **kwargs)
        if name == 'source-initial-boot.json' and not hit:
            hit.append(True)
            raise OSError('invented boot intent reply loss')
        return result
    monkeypatch.setattr(c, '_native_save_at', saved)
    with pytest.raises(OSError, match='boot intent reply loss'):
        n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    monkeypatch.setattr(c, '_native_save_at', save)
    n.boot = '21234567-89AB-4DEF-8123-0123456789AB'
    snapshot = files(n)
    with pytest.raises(ValueError, match='next-reboot-preserved'):
        helper.resume_initial_generation_boot(n.selected)
    assert files(n) == snapshot
