"""Filesystem-only source exchange; no installer, sudo, services or sockets."""
import importlib.util
import fcntl
import os
import json
import re
import socket
import subprocess
import tempfile
from types import SimpleNamespace
import threading
from pathlib import Path

import pytest


MODULE = Path(__file__).resolve().parents[1] / "bin/pixel_source_upgrade.py"
spec = importlib.util.spec_from_file_location("source_upgrade", MODULE)
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


@pytest.fixture
def trees(tmp_path):
    old, new, state = (tmp_path / name for name in ("installed", "candidate", "state"))
    for root in (old, new, state):
        root.mkdir(mode=0o700)
    for root in (old, new):
        for name in upgrade.ROOTS:
            (root / name).mkdir(mode=0o755)
    (old / "bin/a.py").write_text("old")
    (old / "bin/removed.py").write_text("removed")
    (new / "bin/a.py").write_text("new")
    (new / "bin/added.py").write_text("added")
    uid = os.getuid()
    manager = upgrade.SourceUpgrade(state, old, uid, state_uid=uid)
    identity = dict(beforeRef="a" * 40, afterRef="b" * 40, markerSha256="c" * 64,
                    configSha256='e' * 64, receiptSha256=None)
    return manager, old, new, identity


def held(manager, new, identity):
    events = []
    def verify(token):
        assert token == "d" * 64
        events.append("verify")
    manager.stage(new, os.getuid(), identity)
    manager.bind("d" * 64, verify)
    return verify, events


def test_unheld_remount_allocates_new_buffer_and_preserves_old_receipt(trees):
    manager, old, new, identity = trees
    manager.stage(new, os.getuid(), identity)
    path = manager.state / 'source-scratch.json'
    record = json.loads(path.read_text())
    old_buffer = old / record['name']
    record['parent'][0] += 100
    record['identity'][0] += 100
    raw = upgrade.encoded(record)
    manager._write('source-scratch.json', raw)
    manager.stage(new, os.getuid(), identity)
    current = json.loads(path.read_text())
    assert current['name'] != record['name']
    assert current['parent'] == [old.stat().st_dev, old.stat().st_ino]
    assert old_buffer.is_dir() and list(old_buffer.iterdir()) == []
    assert (manager.state / upgrade.sha(raw)).read_bytes() == raw
    manager.bind('d' * 64, lambda _: None)
    manager.publish(lambda _: None)
    assert (old / 'bin/a.py').read_text() == 'new'


@pytest.mark.parametrize('condition', ['held', 'content', 'changed-source', 'transition', 'inode'])
def test_remount_never_rebinds_uncertain_or_changed_scratch(trees, condition):
    manager, old, new, identity = trees
    if condition == 'held':
        held(manager, new, identity)
    else:
        manager.stage(new, os.getuid(), identity)
    record = json.loads((manager.state / 'source-scratch.json').read_text())
    record['parent'][0] += 100
    record['identity'][0] += 100
    if condition == 'content':
        (old / record['name'] / 'payload').write_text('do not adopt or remove')
    if condition == 'changed-source':
        (old / 'bin/a.py').write_text('owner edit')
    if condition == 'transition':
        (manager.state.parent / 'transition.json').write_text('{}')
    if condition == 'inode':
        record['identity'][1] += 1
    raw = upgrade.encoded(record)
    manager._write('source-scratch.json', raw)
    with pytest.raises(upgrade.UpgradeError):
        manager._renew_unheld_scratch_after_remount()
        manager._prepare_scratch()
    assert (manager.state / 'source-scratch.json').read_bytes() == raw


def test_stage_is_no_mutation_and_publish_needs_authenticated_hold(trees):
    manager, old, new, identity = trees
    before = upgrade.inventory(old, os.getuid())
    manager.stage(new, os.getuid(), identity)
    assert upgrade.inventory(old, os.getuid()) == before
    with pytest.raises(upgrade.UpgradeError, match="source-not-held"):
        manager.publish(lambda _: pytest.fail("no hold must not call backend"))
    assert upgrade.inventory(old, os.getuid()) == before


def test_exact_apply_fresh_proof_and_rollback(trees):
    manager, old, new, identity = trees
    before = upgrade.inventory(old, os.getuid())
    verify, events = held(manager, new, identity)
    manager.publish(verify)
    assert upgrade.inventory(old, os.getuid()) == manager.journal()["after"]
    def failed_proof(*_):
        raise RuntimeError("fresh proof failed")
    with pytest.raises(RuntimeError, match="fresh proof"):
        manager.finish(failed_proof)
    assert manager.journal()["phase"] == "applied"
    manager.publish(verify, rollback=True)
    assert upgrade.inventory(old, os.getuid()) == before
    proof = []
    manager.finish(lambda *args: proof.append(args))
    assert proof == [("d" * 64, "rolled-back")]
    assert len(events) >= 7


@pytest.mark.parametrize("boundary", ["intent", "bin/a.py", "bin/added.py", "bin/removed.py", "applied"])
def test_interruption_resumes_identical_candidate(trees, boundary):
    manager, old, new, identity = trees
    verify, _ = held(manager, new, identity)
    def interrupt(phase):
        if phase == boundary:
            raise RuntimeError("power loss")
    with pytest.raises(RuntimeError, match="power loss"):
        manager.publish(verify, checkpoint=interrupt)
    restarted = upgrade.SourceUpgrade(manager.state, old, os.getuid(), state_uid=os.getuid())
    restarted.stage(new, os.getuid(), identity)
    restarted.publish(verify)
    assert upgrade.inventory(old, os.getuid()) == manager.journal()["after"]


@pytest.mark.parametrize("boundary", ["intent", "bin/a.py", "bin/added.py", "bin/removed.py", "restored"])
def test_rollback_interruption_is_replayable(trees, boundary):
    manager, old, new, identity = trees
    before = upgrade.inventory(old, os.getuid())
    verify, _ = held(manager, new, identity)
    manager.publish(verify)
    def interrupt(phase):
        if phase == boundary:
            raise RuntimeError("power loss")
    with pytest.raises(RuntimeError, match="power loss"):
        manager.publish(verify, rollback=True, checkpoint=interrupt)
    with pytest.raises(upgrade.UpgradeError, match="rollback-in-progress"):
        manager.publish(verify)
    manager.publish(verify, rollback=True)
    assert upgrade.inventory(old, os.getuid()) == before


def test_changed_candidate_or_original_never_adopted(trees):
    manager, old, new, identity = trees
    manager.stage(new, os.getuid(), identity)
    (new / "bin/a.py").write_text("third")
    with pytest.raises(upgrade.UpgradeError, match="candidate-changed"):
        manager.stage(new, os.getuid(), identity)
    (old / "bin/a.py").write_text("owner edit")
    with pytest.raises(upgrade.UpgradeError, match="before-changed"):
        manager.bind("d" * 64, lambda _: None)
    assert (old / "bin/a.py").read_text() == "owner edit"


def test_revoked_hold_prevents_overwrite(trees):
    manager, old, new, identity = trees
    held(manager, new, identity)
    before = upgrade.inventory(old, os.getuid())
    def revoked(_):
        raise RuntimeError("revoked")
    with pytest.raises(RuntimeError, match="revoked"):
        manager.publish(revoked)
    assert upgrade.inventory(old, os.getuid()) == before


def test_live_owner_edit_is_not_rolled_back(trees):
    manager, old, new, identity = trees
    verify, _ = held(manager, new, identity)
    manager.publish(verify)
    (old / "bin/a.py").write_text("revoked owner edits")
    with pytest.raises(upgrade.UpgradeError, match="live-drift"):
        manager.publish(verify, rollback=True)
    assert (old / "bin/a.py").read_text() == "revoked owner edits"


@pytest.mark.parametrize("link", ["file", "directory", "hardlink"])
def test_untrusted_filesystem_objects_rejected(trees, tmp_path, link):
    manager, old, new, identity = trees
    outside = tmp_path / "outside"
    outside.write_text("not source")
    if link == "file":
        (new / "bin/b.py").symlink_to(outside)
    elif link == "directory":
        (new / "bin/dir").symlink_to(tmp_path, target_is_directory=True)
    else:
        os.link(outside, new / "bin/b.py")
    before = upgrade.inventory(old, os.getuid())
    with pytest.raises((upgrade.UpgradeError, OSError)):
        manager.stage(new, os.getuid(), identity)
    assert upgrade.inventory(old, os.getuid()) == before
    assert outside.read_text() == "not source"


def test_corrupted_private_snapshot_refuses_publication(trees):
    manager, old, new, identity = trees
    verify, _ = held(manager, new, identity)
    digest = manager.journal()["after"]["bin/a.py"]["sha256"]
    (manager.state / digest).write_text("corruption")
    with pytest.raises(upgrade.UpgradeError, match="snapshot-changed"):
        manager.publish(verify)
    assert (old / "bin/a.py").read_text() == "old"


def test_missing_manifest_does_not_adopt_snapshots(trees):
    manager, old, new, identity = trees
    held(manager, new, identity)
    (manager.state / "source-upgrade.json").unlink()
    with pytest.raises(upgrade.UpgradeError, match="not-held"):
        manager.publish(lambda _: None)


def test_preserves_unrelated_installed_extension(trees):
    manager, old, new, identity = trees
    custom = old / "extensions/owner-extension.py"
    custom.write_text("owner data")
    verify, _ = held(manager, new, identity)
    manager.publish(verify)
    assert custom.read_text() == "owner data"
    assert (old / "bin/removed.py").read_text() == "removed"


def test_unknown_new_file_refuses_before_any_publication(trees):
    manager, old, new, identity = trees
    verify, _ = held(manager, new, identity)
    (old / "extensions/new-owner-file").write_text("concurrent edit")
    with pytest.raises(upgrade.UpgradeError, match="live-drift"):
        manager.publish(verify)
    assert (old / "bin/a.py").read_text() == "old"


def test_symlink_after_stage_never_changes_external_file(trees, tmp_path):
    manager, old, new, identity = trees
    verify, _ = held(manager, new, identity)
    target = tmp_path / "outside"
    target.write_text("outside")
    (old / "bin/a.py").unlink()
    (old / "bin/a.py").symlink_to(target)
    with pytest.raises(OSError):
        manager.publish(verify)
    assert target.read_text() == "outside"


def test_same_transaction_lock_refuses_concurrent_installer(trees):
    manager, old, new, identity = trees
    verify, _ = held(manager, new, identity)
    with (manager.state / "lock").open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with pytest.raises(upgrade.UpgradeError, match="upgrade-busy"):
            manager.publish(verify)
    assert (old / "bin/a.py").read_text() == "old"


def test_rollback_completion_replay_reproves_exact_old_source(trees):
    manager, old, new, identity = trees
    verify, _ = held(manager, new, identity)
    manager.publish(verify)
    manager.publish(verify, rollback=True)
    calls = []
    manager.finish(lambda *args: calls.append(args))
    manager.finish(lambda *args: calls.append(args))
    assert calls == [("d" * 64, "rolled-back")] * 2


def test_mode_and_new_nested_directory_preserved_on_rollback(trees):
    manager, old, new, identity = trees
    (old / "bin/a.py").chmod(0o500)
    (new / "bin/nested").mkdir()
    (new / "bin/nested/new.py").write_text("nested")
    verify, _ = held(manager, new, identity)
    manager.publish(verify)
    assert (old / "bin/nested/new.py").read_text() == "nested"
    manager.publish(verify, rollback=True)
    assert not (old / "bin/nested/new.py").exists()
    assert (old / "bin/a.py").stat().st_mode & 0o777 == 0o500


def test_root_release_gate_requires_exact_completed_source(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade.SourceUpgrade, 'verify_mirror', lambda *a, **kw: None)
    old, new = tmp_path / "installed", tmp_path / "candidate"
    state = tmp_path / "access-state"
    source = state / "source-upgrade"
    for path in (old, new, state, source):
        path.mkdir(mode=0o700)
    for path in (old, new):
        for name in upgrade.ROOTS:
            (path / name).mkdir()
        (path / "bin/run.py").write_text(path.name)
    uid = os.getuid()
    manager = upgrade.SourceUpgrade(source, old, uid, state_uid=uid)
    identity = dict(beforeRef="a" * 40, afterRef="b" * 40, markerSha256="c" * 64,
                    configSha256='e' * 64, receiptSha256=None)
    def gate(token="d" * 64, outcome="applied"):
        upgrade.release_guard(state, old, uid, token, outcome, state_uid=uid)
    manager.stage(new, uid, identity)
    with pytest.raises(upgrade.UpgradeError, match="completion-required"):
        gate()
    manager.bind("d" * 64, lambda _: None)
    manager.publish(lambda _: None)
    with pytest.raises(upgrade.UpgradeError, match="completion-required"):
        gate()
    manager.finish(lambda *_: None)
    gate()
    with pytest.raises(upgrade.UpgradeError, match="completion-required"):
        gate(outcome="rolled-back")
    (old / "bin/run.py").write_text("changed after proof")
    with pytest.raises(upgrade.UpgradeError, match="live-drift"):
        gate()
    gate(token="e" * 64)  # Never reapply an old update to a new model operation.


@pytest.fixture
def mirrored(trees, tmp_path, monkeypatch):
    manager, old, new, identity = trees
    system = tmp_path / 'system'
    mirror = system / 'program'
    mirror.mkdir(parents=True, mode=0o755)
    monkeypatch.setattr(upgrade, 'SYSTEM_ROOT', system)
    monkeypatch.setattr(upgrade, 'SYSTEM_UID', os.getuid())
    monkeypatch.setattr(upgrade, 'MIRROR', mirror)
    monkeypatch.setattr(upgrade, 'MIRROR_LEAVES', frozenset())
    locations = {'pixel_access_bridge.py': 'bin/pixel_access_bridge.py',
                 'pixel_source_upgrade.py': 'bin/pixel_source_upgrade.py',
                 'access_mode_server.py': 'extensions/services/pixel-agent/host/access_mode_server.py'}
    for name, rel in locations.items():
        (new / rel).parent.mkdir(parents=True, exist_ok=True)
        (new / rel).write_bytes(('candidate-' + name).encode())
        (mirror / name).write_bytes(('old-' + name).encode())
    manager.stage(new, os.getuid(), identity)
    for name, rel in locations.items():
        raw = (new / rel).read_bytes()
        manager.record_mirror_write(mirror / name, raw, 0o644, os.getuid(), os.getgid())
        (mirror / name).write_bytes(raw)
    return manager, old, new, identity, mirror


def test_partial_source_rollback_keeps_new_guard_and_cannot_finish(mirrored):
    manager, old, new, identity, mirror = mirrored
    manager.bind('d' * 64, lambda _: None)
    manager.publish(lambda _: None)
    def fail_after_one(phase):
        if phase == 'bin/a.py':
            raise RuntimeError('power loss')
    with pytest.raises(RuntimeError):
        manager.publish(lambda _: None, rollback=True, checkpoint=fail_after_one)
    with pytest.raises(upgrade.UpgradeError, match='outcome-unconfirmed'):
        manager.finish(lambda *_: pytest.fail('must not prove partial rollback'))
    assert (mirror / 'pixel_access_bridge.py').read_bytes().startswith(b'candidate-')
    manager.publish(lambda _: None, rollback=True)
    manager.verify_mirror()  # Guard is intentionally never downgraded.
    manager.finish(lambda *_: None)
    manager.uninstall_inventory()


def test_next_update_requires_completed_released_custody(mirrored):
    manager, old, new, identity, mirror = mirrored
    manager.bind('d' * 64, lambda _: None)
    manager.publish(lambda _: None)
    next_identity = {**identity, 'beforeRef': identity['afterRef'], 'afterRef': 'f' * 40}
    (new / 'bin/a.py').write_text('next update')
    with pytest.raises(upgrade.UpgradeError, match='candidate-changed'):
        manager.stage(new, os.getuid(), next_identity, retire_complete=lambda _: None)
    manager.finish(lambda *_: None)
    def still_pending(_):
        raise RuntimeError('old hold not released')
    with pytest.raises(RuntimeError, match='not released'):
        manager.stage(new, os.getuid(), next_identity, retire_complete=still_pending)
    manager.stage(new, os.getuid(), next_identity, retire_complete=lambda _: None)
    assert manager.journal()['hold'] is None
    assert manager.journal()['phase'] == 'staged'
    previous = json.loads((manager.state / 'previous-completion.json').read_text())
    assert previous['hold'] == 'd' * 64
    # A new generation cannot adopt its predecessor's protected-file receipt.
    with pytest.raises(upgrade.UpgradeError, match='mirror-journal-missing'):
        manager.verify_mirror()


def test_completed_upgrade_allows_next_upgrade_to_capture_new_owner_extension(mirrored):
    manager, old, new, identity, mirror = mirrored
    manager.bind('d' * 64, lambda _: None)
    manager.publish(lambda _: None)
    manager.finish(lambda *_: None)
    extension = old / 'extensions/user-installed/manifest.json'
    extension.parent.mkdir()
    extension.write_text('{"name":"owner-added"}')
    manager.uninstall_inventory()
    next_identity = {**identity, 'beforeRef': identity['afterRef'], 'afterRef': 'f' * 40}
    manager.stage(new, os.getuid(), next_identity, retire_complete=lambda _: None)
    assert 'extensions/user-installed/manifest.json' in manager.journal()['before']
    manager.bind('a' * 64, lambda _: None)
    manager.publish(lambda _: None)
    assert extension.read_text() == '{"name":"owner-added"}'


def installer_source_handoff(manager, old, code_source):
    import textwrap
    script = (MODULE.parents[1] / 'installers/lib/pixel-host-install.sh').read_text()
    start = script.index('    plan = source_upgrade.journal()')
    end = script.index('\nelif (state /', start)
    fragment = textwrap.dedent(script[start:end])
    # Only the literal validation branch: no bootstrap, privileged files,
    # service commands, or complete installer executes in this regression.
    scope = dict(source_upgrade=manager, completed_source_upgrade=None,
                 source=old, code_source=code_source, module=upgrade,
                 owner=SimpleNamespace(pw_uid=os.getuid()), state=manager.state.parent,
                 os=os)
    exec(compile(fragment, '<installer source validation>', 'exec'), scope)
    return scope


def test_installer_completed_handoff_accepts_owner_extension_and_preserves_custody(mirrored):
    manager, old, new, identity, mirror = mirrored
    manager.bind('d' * 64, lambda _: None)
    manager.publish(lambda _: None)
    manager.finish(lambda *_: None)
    extension = old / 'extensions/owner-added.yaml'
    extension.write_text('services: {}')
    scope = installer_source_handoff(manager, old, old)
    assert scope['source_upgrade'] is None
    assert scope['completed_source_upgrade'] is manager
    manager.uninstall_inventory()
    assert extension.read_text() == 'services: {}'


@pytest.mark.parametrize('mutation', ['pending', 'mirror', 'candidate'])
def test_installer_completed_handoff_still_rejects_custody_changes(mirrored, mutation):
    manager, old, new, identity, mirror = mirrored
    manager.bind('d' * 64, lambda _: None)
    manager.publish(lambda _: None)
    manager.finish(lambda *_: None)
    if mutation == 'pending':
        (manager.state.parent / 'transition.json').write_text('{}')
    elif mutation == 'mirror':
        (mirror / 'pixel_access_bridge.py').write_text('changed')
    with pytest.raises((SystemExit, upgrade.UpgradeError)):
        installer_source_handoff(manager, old, new if mutation == 'candidate' else old)


def test_installer_active_handoff_rejects_unplanned_extension(mirrored):
    manager, old, new, identity, mirror = mirrored
    manager.bind('d' * 64, lambda _: None)
    manager.publish(lambda _: None)
    (old / 'extensions/owner-added.yaml').write_text('services: {}')
    with pytest.raises(SystemExit, match='candidate changed'):
        installer_source_handoff(manager, old, old)


@pytest.mark.parametrize('boundary', ['created', 'partial-write', 'fsynced', 'before-rename', 'after-rename'])
@pytest.mark.parametrize('rollback', [False, True])
def test_sigkill_during_source_write_recovers_exact_private_payload(trees, boundary, rollback):
    import signal
    manager, old, new, identity = trees
    verify, _ = held(manager, new, identity)
    if rollback:
        manager.publish(verify)
    child = os.fork()
    if child == 0:
        real_open, real_fdopen = os.open, os.fdopen
        real_fsync, real_replace = os.fsync, os.replace
        payload_fd = None
        def die():
            os.kill(os.getpid(), signal.SIGKILL)
        def open_file(path, flags, *args, **kwargs):
            nonlocal payload_fd
            fd = real_open(path, flags, *args, **kwargs)
            if flags & os.O_CREAT and (str(path) == 'payload' or str(path).startswith('.ods-source-')):
                payload_fd = fd
                if boundary == 'created':
                    die()
            return fd
        class PartialWriter:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.handle.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.handle, name)
            def write(self, raw):
                self.handle.write(raw[:1])
                self.handle.flush()
                die()
        def fdopen(fd, *args, **kwargs):
            handle = real_fdopen(fd, *args, **kwargs)
            return PartialWriter(handle) if fd == payload_fd and boundary == 'partial-write' else handle
        def sync(fd):
            real_fsync(fd)
            if fd == payload_fd and boundary == 'fsynced':
                die()
        def replace(src, dst, *args, **kwargs):
            is_payload = str(src) == 'payload' or str(src).startswith('.ods-source-')
            if is_payload and boundary == 'before-rename':
                die()
            real_replace(src, dst, *args, **kwargs)
            if is_payload and boundary == 'after-rename':
                die()
        os.open, os.fdopen, os.fsync, os.replace = open_file, fdopen, sync, replace
        try:
            manager.publish(verify, rollback=rollback)
        finally:
            os._exit(91)
    _, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    manager.publish(verify, rollback=rollback)
    assert upgrade.inventory(old, os.getuid()) == manager.journal()['before' if rollback else 'after']
    with manager._scratch() as (scratch, validate):
        validate()
        assert os.listdir(scratch) == []


@pytest.mark.parametrize('mutation', ['extra', 'symlink', 'hardlink', 'directory-swap', 'parent-swap'])
def test_unknown_or_swapped_private_scratch_is_preserved(trees, mutation):
    manager, old, new, identity = trees
    verify, _ = held(manager, new, identity)
    record = json.loads((manager.state / 'source-scratch.json').read_text())
    scratch = old / record['name']
    sentinel = old / 'owner-data'
    sentinel.write_bytes(b'never delete me')
    if mutation == 'extra':
        (scratch / 'unrecognized').write_bytes(b'unknown')
    elif mutation == 'symlink':
        (scratch / 'payload').symlink_to(sentinel)
    elif mutation == 'hardlink':
        os.link(sentinel, scratch / 'payload')
    elif mutation == 'directory-swap':
        scratch.rename(old / 'moved-private-scratch')
        scratch.mkdir(mode=0o700)
        (scratch / 'payload').write_bytes(b'unknown')
    else:
        old.rename(old.parent / 'moved-install')
        old.mkdir(mode=0o700)
    with pytest.raises((upgrade.UpgradeError, OSError)):
        manager.publish(verify)
    found = (old.parent / 'moved-install' / 'owner-data') if mutation == 'parent-swap' else sentinel
    assert found.read_bytes() == b'never delete me'
    if mutation in ('extra', 'directory-swap'):
        assert (scratch / ('unrecognized' if mutation == 'extra' else 'payload')).exists()


@pytest.mark.parametrize('populated', [False, True])
def test_scratch_creation_receipt_replay_accepts_only_empty_private_directory(trees, populated):
    manager, old, new, identity = trees
    manager.stage(new, os.getuid(), identity)
    receipt = manager.state / 'source-scratch.json'
    record = json.loads(receipt.read_text())
    record['identity'] = None  # Simulates crash after mkdir but before inode receipt.
    manager._write(receipt.name, upgrade.encoded(record))
    scratch = old / record['name']
    if populated:
        (scratch / 'unknown').write_bytes(b'owner data must survive')
        with pytest.raises(upgrade.UpgradeError, match='unbound-content'):
            manager.stage(new, os.getuid(), identity)
        assert (scratch / 'unknown').read_bytes() == b'owner data must survive'
    else:
        manager.stage(new, os.getuid(), identity)
        assert json.loads(receipt.read_text())['identity'] is not None


@pytest.mark.parametrize('mutation', ['scratch', 'destination'])
def test_directory_replacement_during_write_cannot_publish(trees, monkeypatch, mutation):
    manager, old, new, identity = trees
    verify, _ = held(manager, new, identity)
    record = json.loads((manager.state / 'source-scratch.json').read_text())
    scratch = old / record['name']
    original_fsync = os.fsync
    replaced = False
    def exchange_after_write(fd):
        nonlocal replaced
        original_fsync(fd)
        if not replaced and os.readlink(f'/proc/self/fd/{fd}') == str(scratch / 'payload'):
            replaced = True
            target = scratch if mutation == 'scratch' else old / 'bin'
            target.rename(old / 'displaced-directory')
            target.mkdir(mode=0o700 if mutation == 'scratch' else 0o755)
            if mutation == 'destination':
                (target / 'a.py').write_bytes(b'old')
    monkeypatch.setattr(os, 'fsync', exchange_after_write)
    with pytest.raises(upgrade.UpgradeError, match='changed'):
        manager.publish(verify)
    assert replaced
    assert (old / 'bin/a.py').read_bytes() == b'old'
    assert (old / 'displaced-directory').is_dir()


def test_other_destination_filesystem_is_refused_before_hold(trees, monkeypatch):
    manager, old, new, identity = trees
    real_lstat = Path.lstat
    def different_device(path):
        info = real_lstat(path)
        if path == old / 'bin':
            values = list(info)
            values[2] += 1
            return os.stat_result(values)
        return info
    monkeypatch.setattr(Path, 'lstat', different_device)
    with pytest.raises(upgrade.UpgradeError, match='cross-filesystem'):
        manager.stage(new, os.getuid(), identity)
    assert manager.journal()['phase'] == 'staged'
    assert manager.journal()['hold'] is None
    assert (old / 'bin/a.py').read_bytes() == b'old'


def test_same_device_bind_mount_is_refused_before_hold(trees, monkeypatch):
    manager, old, new, identity = trees
    actual_mount = upgrade.mount_id
    def substituted_mount(fd):
        return actual_mount(fd) + (1 if os.readlink(f'/proc/self/fd/{fd}') == str(old / 'bin') else 0)
    monkeypatch.setattr(upgrade, 'mount_id', substituted_mount)
    with pytest.raises(upgrade.UpgradeError, match='cross-filesystem'):
        manager.stage(new, os.getuid(), identity)
    assert manager.journal()['hold'] is None
    assert (old / 'bin/a.py').read_bytes() == b'old'


@pytest.mark.parametrize('mutation', ['', 'bytes', 'mode', 'uid', 'unknown'])
def test_completed_installer_writer_requires_same_protected_mirror(mirrored, mutation):
    import hashlib
    import textwrap
    manager, old, new, identity, mirror = mirrored
    manager.bind('d' * 64, lambda _: None)
    manager.publish(lambda _: None)
    manager.finish(lambda *_: None)
    script = (MODULE.parents[1] / 'installers/lib/pixel-host-install.sh').read_text()
    start = script.index('    if completed_source_upgrade is not None:', script.index('def write(path,'))
    end = script.index('    if path.exists()', start)
    fragment = textwrap.dedent(script[start:end])
    path = mirror / 'pixel_access_bridge.py'
    content = path.read_bytes()
    mode, uid = 0o644, os.getuid()
    if mutation == 'bytes':
        content += b'changed'
    elif mutation == 'mode':
        mode = 0o600
    elif mutation == 'uid':
        uid += 1
    elif mutation == 'unknown':
        path = mirror / 'unrecorded.py'
    scope = dict(completed_source_upgrade=manager, path=path, content=content,
                 mode=mode, uid=uid, gid=os.getgid(), hashlib=hashlib)
    if mutation:
        with pytest.raises(SystemExit, match='protected coordinator'):
            exec(compile(fragment, '<installer write validation>', 'exec'), scope)
    else:
        exec(compile(fragment, '<installer write validation>', 'exec'), scope)


def test_permission_revocation_can_restage_only_before_any_hold(mirrored):
    manager, old, new, identity, mirror = mirrored
    original_bytes = upgrade.inventory(old, os.getuid())
    enabled = {**identity, 'configSha256': 'a' * 64, 'receiptSha256': 'b' * 64}
    manager.stage(new, os.getuid(), enabled, rebase_unheld=True)
    current = {**identity, 'configSha256': 'f' * 64, 'receiptSha256': None}
    manager.stage(new, os.getuid(), current, rebase_unheld=True)
    assert manager.journal()['identity'] == current
    assert manager.journal()['hold'] is None
    assert upgrade.inventory(old, os.getuid()) == original_bytes
    manager.verify_mirror()  # Partial bootstrap custody survives restaging.
    manager.bind('d' * 64, lambda _: None)
    with pytest.raises(upgrade.UpgradeError, match='unheld-rebase-refused'):
        manager.stage(new, os.getuid(), identity, rebase_unheld=True)
    assert manager.journal()['identity'] == current
    assert manager.journal()['hold'] == 'd' * 64


def test_unheld_permission_restage_cannot_change_candidate_or_marker(mirrored):
    manager, old, new, identity, mirror = mirrored
    for changed in ({**identity, 'markerSha256': 'f' * 64}, {**identity, 'afterRef': 'f' * 40}):
        with pytest.raises(upgrade.UpgradeError, match='unheld-rebase-refused'):
            manager.stage(new, os.getuid(), changed, rebase_unheld=True)
    (new / 'bin/a.py').write_text('other candidate')
    with pytest.raises(upgrade.UpgradeError, match='unheld-rebase-refused'):
        manager.stage(new, os.getuid(), {**identity, 'configSha256': 'f' * 64}, rebase_unheld=True)
    assert manager.journal()['identity'] == identity


@pytest.mark.parametrize('mutation', ['extra', 'symlink', 'blob', 'pending', 'mirror'])
def test_completed_uninstall_inventory_rejects_unknown_or_changed_state(mirrored, mutation):
    manager, old, new, identity, mirror = mirrored
    manager.bind('d' * 64, lambda _: None)
    manager.publish(lambda _: None)
    manager.finish(lambda *_: None)
    manager.uninstall_inventory()
    if mutation == 'extra':
        (manager.state / 'unexpected').write_bytes(b'x')
    elif mutation == 'symlink':
        (manager.state / ('f' * 64)).symlink_to(old / 'bin/a.py')
    elif mutation == 'blob':
        (manager.state / manager.journal()['before']['bin/a.py']['sha256']).write_bytes(b'changed')
    elif mutation == 'pending':
        (manager.state.parent / 'transition.json').write_text('{}')
    else:
        (mirror / 'pixel_access_bridge.py').write_bytes(b'not the reviewed guard')
    with pytest.raises((upgrade.UpgradeError, OSError)):
        manager.uninstall_inventory()


def test_source_begin_protocol_has_no_caller_supplied_authority():
    import sys
    sys.path.insert(0, str(MODULE.parent))
    import pixel_access_protocol as protocol
    import pixel_model_transition as client
    def request(operation, payload):
        assert protocol.control_request(dict(operation=operation, request=payload)) == {
            'operation': 'installer-source-begin', 'request': {}}
        return 200, {'status': 'held', 'transaction_id': 'd' * 64}
    assert client.execute('source-begin', request=request) == 'd' * 64
    for field in ('transaction_id', 'owner', 'install', 'confirmed'):
        with pytest.raises(protocol.ProtocolError):
            protocol.control_request(dict(operation='installer-source-begin', request={field: 'attacker'}))


@pytest.mark.parametrize('receipt_present', [False, True])
def test_begin_plan_checks_actual_original_config_and_receipt_bytes(monkeypatch, receipt_present):
    # Use a private temporary directory below HOME so the real production
    # ancestor-custody validator runs unchanged (/tmp is intentionally unsafe).
    with tempfile.TemporaryDirectory(prefix='.ods-source-fixture-', dir=Path.home()) as directory:
        base = Path(directory)
        home, install, incoming, state = (base / name for name in ('owner', 'installed', 'incoming', 'root-state'))
        for path in (home, install, incoming, state, state / 'source-upgrade'):
            path.mkdir(mode=0o700)
        for path in (install, incoming):
            for name in upgrade.ROOTS:
                (path / name).mkdir()
            (path / 'bin/code.py').write_text(path.name)
        config = home / '.openclaw/openclaw.json'
        marker = home / '.config/ods/pixel-managed.json'
        receipt = home / '.local/state/ods-pixel-access-mode/pixel-access-mode.json'
        for path in (config, marker, receipt):
            path.parent.mkdir(parents=True, mode=0o700)
        for path in (config, marker):
            path.write_text('{}')
            path.chmod(0o600)
        if receipt_present:
            receipt.write_text('{"status":"enabled"}')
            receipt.chmod(0o600)
        uid = os.getuid()
        original_class = upgrade.SourceUpgrade
        manager = original_class(state / 'source-upgrade', install, uid, state_uid=uid)
        identity = dict(beforeRef='a' * 40, afterRef='b' * 40,
                        markerSha256=upgrade.sha(marker.read_bytes()),
                        **upgrade.owner_baseline(home, uid))
        manager.stage(incoming, uid, identity)
        # Only the fixture's root UID is substituted. Actual no-follow reads,
        # private modes, ancestor checks, JSON parsing and hashes are exercised.
        monkeypatch.setattr(upgrade, 'SourceUpgrade',
            lambda *a, **kw: original_class(*a, **kw, state_uid=uid))
        owner = SimpleNamespace(pw_uid=uid, pw_dir=str(home))
        assert upgrade.begin_plan(state, install, owner, installer=True).journal() == manager.journal()
        config.write_text('{"changed":true}')
        with pytest.raises(upgrade.UpgradeError, match='owner-state-changed'):
            upgrade.begin_plan(state, install, owner, installer=True)
        config.write_text('{}')
        if receipt_present:
            receipt.unlink()
        else:
            receipt.write_text('{"status":"enabled"}')
            receipt.chmod(0o600)
        with pytest.raises(upgrade.UpgradeError, match='owner-state-changed'):
            upgrade.begin_plan(state, install, owner, installer=True)
        assert manager.journal()['hold'] is None


def test_reviewed_compose_and_exec_mode_transforms_are_staged_before_copy(trees):
    manager, old, new, identity = trees
    inactive = 'extensions/services/pixel-edge/compose.yaml.disabled'
    active = inactive.removesuffix('.disabled')
    for root, rel, text in ((old, inactive, 'old disabled'), (new, active, 'new active')):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    for rel in upgrade.EXECUTION_CONTROLS:
        (new / rel).parent.mkdir(parents=True, exist_ok=True)
        (new / rel).write_text('#!/bin/sh\n')
        (new / rel).chmod(0o644)
    verify, _ = held(manager, new, identity)
    planned = manager.journal()
    assert inactive in planned['before'] and inactive not in planned['after']
    assert all(planned['after'][rel]['mode'] == 0o755 for rel in upgrade.EXECUTION_CONTROLS)
    manager.publish(verify)
    assert not (old / inactive).exists()
    assert all((old / rel).stat().st_mode & 0o777 == 0o755 for rel in upgrade.EXECUTION_CONTROLS)
    manager.publish(verify, rollback=True)
    assert (old / inactive).read_text() == 'old disabled'
    assert not (old / active).exists()


def test_source_begin_real_socket_rejects_nonroot_peer_before_sending(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip('this fixture specifically exercises a non-root server')
    import sys
    sys.path.insert(0, str(MODULE.parent))
    import pixel_access_client as transport
    address = str(tmp_path / 'control.sock')
    captured = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(address)
        server.listen(1)
        server.settimeout(5)
        def serve():
            with server.accept()[0] as connection:
                connection.settimeout(5)
                captured.append(connection.recv(2048))
        worker = threading.Thread(target=serve)
        worker.start()
        monkeypatch.setattr(transport, 'ACCESS_SOCKET_PATH', address)
        try:
            with pytest.raises(ValueError, match='root coordinator'):
                transport.request_access('installer-source-begin', {})
        finally:
            worker.join(timeout=6)
    assert not worker.is_alive()
    assert captured == [b'']


def test_coordinator_reserves_source_token_before_gate_and_replays_lost_write(trees, tmp_path, monkeypatch):
    import sys
    import types
    sys.path.insert(0, str(MODULE.parent))
    sys.path.insert(0, str(Path(__file__).parent))
    import pixel_access_bridge as bridge_module
    from test_pixel_model_transition import FakeBridge
    manager, old, new, identity = trees
    identity = {**identity, 'configSha256': 'a' * 64}
    manager.stage(new, os.getuid(), identity)
    bridge = FakeBridge(tmp_path / 'coordinator')
    bridge.install = old
    bridge.owner = types.SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(tmp_path / 'home'))
    (bridge.state / 'source-upgrade').mkdir()
    monkeypatch.setitem(sys.modules, 'pixel_source_upgrade', upgrade)
    def plan(_state, _install, _owner, *, installer):
        if not installer:
            raise upgrade.UpgradeError('source-installer-hold-required')
        return manager
    monkeypatch.setattr(upgrade, 'begin_plan', plan)
    monkeypatch.setattr(bridge_module.os, 'geteuid', lambda: 0)
    def interrupted(path, value):
        assert manager.journal()['hold'] == value['transaction_id']
        raise RuntimeError('crash before admission journal write')
    monkeypatch.setattr(bridge_module, 'atomic_json', interrupted)
    with pytest.raises(RuntimeError, match='crash before'):
        bridge.model_begin(installer_source=True)
    reserved = manager.journal()['hold']
    assert reserved and not (bridge.state / 'transition.json').exists()
    assert not any(call.startswith(('native:', 'edge:')) for call in bridge.calls)
    with pytest.raises(bridge_module.AccessError, match='source-installer-hold-required'):
        bridge.model_begin()
    def atomic(path, value):
        path.write_text(json.dumps(value))
    monkeypatch.setattr(bridge_module, 'atomic_json', atomic)
    assert bridge.model_begin(installer_source=True) == {'status': 'held', 'transaction_id': reserved}
    assert bridge.pending()['transaction_id'] == reserved
    assert bridge.native_state['phase'] == bridge.edge_state['phase'] == 'held'


@pytest.mark.parametrize('phase,edge_phase,native_phase', [
    ('acquiring', 'idle', 'idle'), ('draining', 'held', 'idle'),
    ('draining', 'held', 'held'), ('error', 'interrupted', 'interrupted'),
])
@pytest.mark.parametrize('mode', ['sandboxed', 'full-access'])
def test_source_acquisition_crash_resumes_same_owner_mode_and_token(
        trees, tmp_path, monkeypatch, phase, edge_phase, native_phase, mode):
    import sys
    import types
    sys.path.insert(0, str(MODULE.parent))
    sys.path.insert(0, str(Path(__file__).parent))
    import pixel_access_bridge as bridge_module
    from test_pixel_model_transition import FakeBridge
    manager, old, new, identity = trees
    identity = {**identity, 'configSha256': 'a' * 64}
    manager.stage(new, os.getuid(), identity)
    manager.bind('d' * 64, lambda _: None)
    bridge = FakeBridge(tmp_path / 'coordinator')
    bridge.install = old
    bridge.owner = types.SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(tmp_path / 'home'))
    bridge.edge_state['phase'] = edge_phase
    bridge.native_state['phase'] = native_phase
    bridge.config['configured_status'] = mode
    pending = dict(kind='model', transaction_id='d' * 64, token='e' * 64,
                   phase=phase, edge_revision='b' * 64, configured_mode=mode,
                   start_config_sha256='a' * 64)
    (bridge.state / 'transition.json').write_text(json.dumps(pending))
    monkeypatch.setitem(sys.modules, 'pixel_source_upgrade', upgrade)
    monkeypatch.setattr(upgrade, 'begin_plan', lambda *a, **kw: manager)
    monkeypatch.setattr(bridge_module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(bridge_module, 'atomic_json', lambda p, v: p.write_text(json.dumps(v)))
    proofs = []
    def prove(token, observed_mode):
        assert token == pending['token'] and observed_mode == mode
        assert bridge.edge_state['phase'] == bridge.native_state['phase'] == 'held'
        proofs.append(observed_mode)
    monkeypatch.setattr(bridge, 'verify_held_mode', prove)
    assert bridge.model_begin(installer_source=True) == {'status': 'held', 'transaction_id': 'd' * 64}
    assert proofs == [mode]
    assert bridge.pending()['token'] == pending['token']
    assert bridge.pending()['phase'] == 'held'
    assert 'release' not in ' '.join(bridge.calls)
    # Foreign handles and a changed owner preference never acquire fresh gates.
    bad = {**pending, 'transaction_id': 'f' * 64}
    (bridge.state / 'transition.json').write_text(json.dumps(bad))
    bridge.calls.clear()
    with pytest.raises(bridge_module.AccessError, match='model-transaction-mismatch'):
        bridge.model_begin(installer_source=True)
    assert not any(call.startswith(('native:', 'edge:')) for call in bridge.calls)
    (bridge.state / 'transition.json').write_text(json.dumps(pending))
    bridge.config['configured_status'] = 'full-access' if mode == 'sandboxed' else 'sandboxed'
    with pytest.raises(bridge_module.AccessError, match='source-owner-state-changed'):
        bridge.model_begin(installer_source=True)
    assert proofs == [mode]
    assert bridge.pending()['phase'] == 'error'
    assert 'release' not in ' '.join(bridge.calls)


@pytest.mark.parametrize('phase,failure,expected', [
    ('staged', '', ['restore', 'stage', 'status', 'bootstrap', 'hold', 'copy', 'downstream']),
    ('held', '', ['restore', 'stage', 'status', 'hold', 'copy', 'downstream']),
    ('applied', '', ['restore', 'stage', 'status', 'hold', 'copy', 'downstream']),
    ('staged', 'stage', ['restore', 'stage']),
    ('staged', 'hold', ['restore', 'stage', 'status', 'bootstrap', 'hold']),
])
def test_phase06_actual_handoff_slice_never_uninstalls_or_rebootstraps_held_code(phase, failure, expected):
    source = (MODULE.parents[1] / 'installers/phases/06-directories.sh').read_text()
    start = source.index('                _phase06_step "rebind-pixel-source"')
    end = source.index('                unset _phase06_pixel_binary', start)
    fragment = source[start:end] + 'unset _phase06_pixel_binary\n'
    # This is the literal handoff branch, not a whole installer/uninstaller.
    # Every callable is replaced with a shell function; PATH has no programs,
    # and no source/eval/privileged operation is allowed in the extracted slice.
    assert not re.search(r'(?m)^\s*(?:sudo|systemctl|source|eval|rm|cp)\s', fragment)
    assert 'uninstall' not in fragment
    harness = r'''
PATH=/no-external-programs
_phase06_pixel_owner=fixture
_phase06_pixel_home=/fixture/home
_phase06_requested_pixel_ref=fixture
SCRIPT_DIR=/fixture/candidate
PHASE="$1"
FAILURE="$2"
_phase06_step() { :; }
ai() { :; }
error() { :; }
_ods_pixel_restore_transition_source() { printf 'restore\n' >&2; }
_ods_pixel_openclaw_bin() { printf /fixture/openclaw; }
_ods_pixel_install_access_service() { printf 'bootstrap\n' >&2; }
_ods_pixel_source_upgrade() {
  printf '%s\n' "$1" >&2
  [[ "$FAILURE" != "$1" ]] || return 1
  case "$1" in
    stage|copy|downstream) : ;;
    status) printf '{}';;
    hold) printf '%064d' 0;;
    *) return 90;;
  esac
}
jq() { [[ "$PHASE" == staged ]]; }
exercise() {
'''
    result = subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c',
        harness + fragment + '\n}\nexercise\n', 'fixture', phase, failure],
        text=True, capture_output=True, timeout=5)
    assert result.stderr.splitlines() == expected
    assert result.returncode == (1 if failure else 0)



@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.parametrize('boundary', ['intent', 'extensions/services/whisper/compose.yaml', 'extensions/services/whisper/compose.yaml.disabled', 'applied'])
def test_feature_only_counterpart_removal_resumes_exactly(trees, enabled, boundary):
    manager, old, new, identity = trees
    identity['afterRef'] = identity['beforeRef']
    for root in (old, new):
        (root / 'extensions/services/whisper').mkdir(parents=True)
    active = 'extensions/services/whisper/compose.yaml'
    disabled = active + '.disabled'
    (old / active).write_text('old enabled')
    (old / disabled).write_text('old disabled')
    selected = active if enabled else disabled
    opposite = disabled if enabled else active
    (new / selected).write_text('selected bytes')
    custom = old / 'extensions/services/owner-custom'
    custom.mkdir()
    (custom / 'compose.yaml').write_text('owner enabled')
    (custom / 'compose.yaml.disabled').write_text('owner disabled')
    before = upgrade.inventory(old, os.getuid())
    verify, _ = held(manager, new, identity)
    def crash(point):
        if point == boundary:
            raise RuntimeError('interrupted feature exchange')
    with pytest.raises(RuntimeError, match='interrupted feature exchange'):
        manager.publish(verify, checkpoint=crash)
    restarted = upgrade.SourceUpgrade(manager.state, old, os.getuid(), state_uid=os.getuid())
    (new / selected).write_text('different candidate')
    with pytest.raises(upgrade.UpgradeError, match='source-candidate-changed'):
        restarted.stage(new, os.getuid(), identity)
    (new / selected).write_text('selected bytes')
    restarted.stage(new, os.getuid(), identity)
    restarted.publish(verify)
    assert (old / selected).read_text() == 'selected bytes'
    assert not (old / opposite).exists()
    assert (custom / 'compose.yaml').read_text() == 'owner enabled'
    assert (custom / 'compose.yaml.disabled').read_text() == 'owner disabled'
    def missing_fresh_proof(*_):
        raise RuntimeError('fresh runtime proof required')
    with pytest.raises(RuntimeError, match='fresh runtime proof required'):
        restarted.finish(missing_fresh_proof)
    assert restarted.journal()['phase'] == 'applied'
    restarted.publish(verify, rollback=True)
    assert upgrade.inventory(old, os.getuid()) == before


def test_feature_projection_is_closed_to_actual_phase03_services():
    phase = (MODULE.parents[1] / 'installers/phases/03-features.sh').read_text()
    names = set(re.findall(r'^    _sync_extension_compose "[^"\n]*"\s+([a-z0-9-]+)', phase, re.M))
    assert names == upgrade.FEATURE_COMPOSE_SERVICES
    before = {'extensions/services/owner-custom/compose.yaml': {'owner': True}}
    candidate = {'extensions/services/owner-custom/compose.yaml.disabled': {'owner': True}}
    assert upgrade.installed_projection(before, candidate) == {**before, **candidate}
    pair = 'extensions/services/whisper/compose.yaml'
    with pytest.raises(upgrade.UpgradeError, match='source-feature-selection-ambiguous'):
        upgrade.installed_projection({}, {pair: {}, pair+'.disabled': {}})


@pytest.mark.parametrize("retained_provider", [False, True])
def test_held_source_copy_keeps_drvfs_root_safe(trees, retained_provider):
    """The copy must be safe before Phase 06's later normalization runs."""
    manager, old, new, identity = trees
    copy_helper = MODULE.parents[1] / "installers/lib/source-copy.sh"
    provider = old / "config/litellm/cloud.yaml"
    candidate_provider = new / "config/litellm/cloud.yaml"
    candidate_provider.parent.mkdir(parents=True)
    candidate_provider.write_text("bundled template")
    if retained_provider:
        provider.parent.mkdir(parents=True)
        provider.write_text("owner provider")
        provider.chmod(0o600)
        before = provider.stat()
    # Include the source root itself, which rsync -a also copies. Every
    # candidate file appearing executable matches the WSL DrvFS failure.
    for path in [new, *new.rglob("*")]:
        path.chmod(0o777)
    verify, _ = held(manager, new, identity)
    manager.publish(verify)
    expected = manager.journal()
    protected_before = {
        p.name: p.read_bytes() for p in manager.state.iterdir() if p.is_file()
    }
    # Open the provider as a long-lived consumer would; preserving its
    # pathname alone would miss replacing the inode underneath that reader.
    with provider.open() if retained_provider else open(os.devnull) as consumer:
        subprocess.run(
            ["bash", "-c", 'source "$1"; ods_copy_install_source "$2" "$3" "$4"',
             "source-copy", str(copy_helper), str(new), str(old),
             str(old.parent / "copy.log")],
            env={**os.environ, "ODS_PIXEL_SOURCE_TRANSACTION": "d" * 64},
            check=True, capture_output=True, text=True,
        )
        # Reconstruct the actual transaction reader immediately, without
        # invoking Phase 06 normalization or changing any permissions here.
        current = upgrade.SourceUpgrade(manager.state, old, os.getuid(), state_uid=os.getuid())
        assert current.journal() == expected
        assert upgrade.inventory(old, os.getuid()) == expected["after"]
        assert old.stat().st_mode & 0o022 == 0
        assert provider.parent.stat().st_mode & 0o022 == 0
        assert (old / "config").stat().st_mode & 0o022 == 0
        assert protected_before == {
            p.name: p.read_bytes() for p in manager.state.iterdir() if p.is_file()
        }
        if retained_provider:
            after = provider.stat()
            assert (after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid) == (
                before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid)
            assert provider.read_text() == consumer.read() == "owner provider"
        else:
            assert provider.read_text() == "bundled template"


def test_source_transaction_rejects_writable_install_root(trees):
    manager, old, _, _ = trees
    old.chmod(0o777)
    with pytest.raises(upgrade.UpgradeError, match="source-directory-unsafe"):
        upgrade.SourceUpgrade(manager.state, old, os.getuid(), state_uid=os.getuid())
