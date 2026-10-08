"""Filesystem-only source exchange; no installer, sudo, services or sockets."""
import importlib.util
import fcntl
import os
import json
import pwd
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


def test_published_paths_take_the_owner_primary_group(trees):
    """Root publishes the update. Setting only the owner left new files and
    directories in root's group; a fresh install's owner-run copy leaves them
    in the owner's primary group, and the host agent keeps a file's group when
    it rewrites one (a non-root chown to root's group fails)."""
    manager, old, new, identity = trees
    uid = os.getuid()
    assert upgrade.SourceUpgrade(manager.state, old, uid, state_uid=uid).gid == pwd.getpwuid(uid).pw_gid
    # Unprivileged stand-in for root: this process creates the payloads in
    # its own group, and the owner's group is another one it may assign.
    creator = os.getegid()
    owner_group = next((group for group in os.getgroups() if group != creator), None)
    if owner_group is None:
        pytest.skip("needs a supplementary group other than the creating group")
    manager = upgrade.SourceUpgrade(manager.state, old, uid, state_uid=uid, owner_gid=owner_group)
    (new / "extensions/services/hermes").mkdir(parents=True)
    (new / "extensions/services/hermes/compose.yaml.disabled").write_text("services: {}\n")
    verify, _ = held(manager, new, identity)
    manager.publish(verify)
    for path in ("bin/a.py", "bin/added.py", "extensions/services", "extensions/services/hermes",
                 "extensions/services/hermes/compose.yaml.disabled"):
        assert (old / path).stat().st_gid == owner_group, path
    # Paths the update does not change are not rewritten.
    assert (old / "bin/removed.py").stat().st_gid == creator


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


def test_new_download_refreshes_unheld_plan_without_copying_or_losing_mirror(mirrored):
    manager, old, new, identity, mirror = mirrored
    prior = manager.journal()
    original_source = upgrade.inventory(old, os.getuid())
    mirror_name = manager._mirror_name()
    mirror_bytes = (manager.state / mirror_name).read_bytes()
    protected_bytes = {path.name: path.read_bytes() for path in mirror.iterdir()}
    (new / 'bin/a.py').write_text('new main download')
    refreshed = manager.stage(new, os.getuid(), identity, refresh_unheld=True)
    assert refreshed['phase'] == 'staged' and refreshed['hold'] is None
    assert refreshed['before'] == prior['before'] == original_source
    assert refreshed['candidate'] == upgrade.inventory(new, os.getuid(), candidate=True)
    assert (manager.state / upgrade.sha(upgrade.encoded(prior))).read_bytes() == upgrade.encoded(prior)
    assert (manager.state / mirror_name).read_bytes() == mirror_bytes
    assert {path.name: path.read_bytes() for path in mirror.iterdir()} == protected_bytes
    assert upgrade.inventory(old, os.getuid()) == original_source
    # The normal protected installer writer can now journal the refreshed
    # coordinator, then acquire its own hold and apply the selected candidate.
    for path in mirror.iterdir():
        manager.record_mirror_write(path, path.read_bytes(), 0o644, os.getuid(), os.getgid())
    manager.bind('d' * 64, lambda _: None)
    manager.publish(lambda _: None)
    assert (old / 'bin/a.py').read_text() == 'new main download'
    manager.finish(lambda *_: None)
    manager.verify_mirror()


def test_stage_cli_selects_guarded_refresh_for_a_new_download(mirrored, monkeypatch):
    manager, old, new, identity, mirror = mirrored
    for service in ('pixel-edge', 'litellm'):
        path = new / f'extensions/services/{service}/compose.yaml'
        path.parent.mkdir(parents=True)
        path.write_text('services: {}')
    marker = dict(schema_version=2, manager='ods', install_dir=str(old),
                  initial_active_state='absent', state='ready', pixel_source_ref=identity['beforeRef'])
    monkeypatch.setattr(upgrade, '_protected_json', lambda *a, **kw: (marker, identity['markerSha256']))
    monkeypatch.setattr(upgrade, 'owner_baseline', lambda *a: {
        'configSha256': identity['configSha256'], 'receiptSha256': identity['receiptSha256']})
    account = SimpleNamespace(pw_dir=str(old.parent / 'owner'), pw_uid=os.getuid())
    upgrade._stage(manager, account, new, identity['afterRef'])
    assert manager.journal()['candidate'] == upgrade.inventory(new, os.getuid(), candidate=True)
    assert manager.journal()['hold'] is None
    assert (old / 'bin/a.py').read_text() == 'old'


@pytest.mark.parametrize('boundary', ['before-journal', 'after-journal'])
def test_unheld_refresh_replays_interrupted_journal_publication(mirrored, monkeypatch, boundary):
    manager, old, new, identity, mirror = mirrored
    prior = manager.journal()
    (new / 'bin/a.py').write_text('new main download')
    save = manager._save
    def interrupted(value):
        if boundary == 'after-journal':
            save(value)
        raise RuntimeError('interrupted refresh')
    monkeypatch.setattr(manager, '_save', interrupted)
    with pytest.raises(RuntimeError, match='interrupted refresh'):
        manager.stage(new, os.getuid(), identity, refresh_unheld=True)
    monkeypatch.setattr(manager, '_save', save)
    if boundary == 'before-journal':
        assert manager.journal() == prior
    result = manager.stage(new, os.getuid(), identity, refresh_unheld=True)
    assert result['candidate'] == upgrade.inventory(new, os.getuid(), candidate=True)
    assert result['phase'] == 'staged' and result['hold'] is None
    assert upgrade.inventory(old, os.getuid()) == prior['before']


def test_unheld_refresh_rejournals_a_partially_installed_changed_coordinator(mirrored):
    manager, old, new, identity, mirror = mirrored
    prior = manager.journal()
    # A bootstrap interruption left this file at the recorded before bytes.
    (mirror / 'pixel_source_upgrade.py').write_bytes(b'old-pixel_source_upgrade.py')
    (new / 'bin/pixel_access_bridge.py').write_bytes(b'corrected controller')
    manager.stage(new, os.getuid(), identity, refresh_unheld=True)
    for path in mirror.iterdir():
        rel = ('extensions/services/pixel-agent/host/' if path.name == 'access_mode_server.py' else 'bin/') + path.name
        raw = (new / rel).read_bytes()
        manager.record_mirror_write(path, raw, 0o644, os.getuid(), os.getgid())
        path.write_bytes(raw)
    manager.verify_mirror()
    assert upgrade.inventory(old, os.getuid()) == prior['before']
    assert manager.journal()['hold'] is None


def test_unheld_refresh_rechecks_live_source_before_publishing(mirrored, monkeypatch):
    manager, old, new, identity, mirror = mirrored
    prior = manager.journal()
    (new / 'bin/a.py').write_text('new main download')
    inventory = upgrade.inventory
    calls = 0
    def racing_inventory(path, uid, **kwargs):
        nonlocal calls
        if path == old:
            calls += 1
            if calls == 2:
                (old / 'bin/a.py').write_text('concurrent owner edit')
        return inventory(path, uid, **kwargs)
    monkeypatch.setattr(upgrade, 'inventory', racing_inventory)
    with pytest.raises(upgrade.UpgradeError, match='source-live-drift'):
        manager.stage(new, os.getuid(), identity, refresh_unheld=True)
    assert manager.journal() == prior
    assert (old / 'bin/a.py').read_text() == 'concurrent owner edit'


@pytest.mark.parametrize('condition', [
    'hold', 'applying', 'applied', 'restoring', 'restored', 'complete',
    'pending', 'downstream', 'live-bytes', 'live-mode', 'mirror', 'scratch',
    'marker', 'before-ref', 'after-ref',
])
def test_unheld_refresh_refuses_ambiguous_state_without_replacing_plan(mirrored, condition):
    manager, old, new, identity, mirror = mirrored
    if condition == 'hold':
        manager.bind('d' * 64, lambda _: None)
    elif condition in ('applying', 'applied', 'restoring', 'restored', 'complete'):
        value = manager.journal()
        value['phase'] = condition
        manager._save(value)
    elif condition == 'pending':
        (manager.state.parent / 'transition.json').write_text('{}')
    elif condition == 'downstream':
        manager._write(manager.downstream_name(), b'{}')
    elif condition == 'live-bytes':
        (old / 'bin/a.py').write_text('owner edit')
    elif condition == 'live-mode':
        (old / 'bin/a.py').chmod(0o600)
    elif condition == 'mirror':
        (mirror / 'pixel_access_bridge.py').write_text('unrecognized controller')
    elif condition == 'scratch':
        record = json.loads((manager.state / 'source-scratch.json').read_text())
        (old / record['name'] / 'payload').write_text('interrupted write')
    else:
        key = {'marker': 'markerSha256', 'before-ref': 'beforeRef', 'after-ref': 'afterRef'}[condition]
        identity = {**identity, key: 'f' * len(identity[key])}
    (new / 'bin/a.py').write_text('new main download')
    prior = manager.journal()
    source = upgrade.inventory(old, os.getuid())
    records = {p.name: p.read_bytes() for p in manager.state.iterdir() if p.is_file()}
    protected = {p.name: p.read_bytes() for p in mirror.iterdir()}
    with pytest.raises(upgrade.UpgradeError):
        manager.stage(new, os.getuid(), identity, refresh_unheld=True)
    assert manager.journal() == prior
    assert upgrade.inventory(old, os.getuid()) == source
    assert {p.name: p.read_bytes() for p in manager.state.iterdir() if p.is_file()} == records
    assert {p.name: p.read_bytes() for p in mirror.iterdir()} == protected


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


def _completed(mirrored):
    manager, old, _new, _identity, _mirror = mirrored
    manager.bind('d' * 64, lambda _: None)
    manager.publish(lambda _: None)
    manager.finish(lambda *_: None)
    return manager, old


def _renumbered_receipt(manager):
    """The receipt as a WSL VM restart leaves it: same inodes, a new device."""
    record = json.loads((manager.state / 'source-scratch.json').read_text())
    record['parent'][0] += 100
    record['identity'][0] += 100
    return record


def test_completed_uninstall_inventory_accepts_a_renumbered_device(mirrored):
    manager, old = _completed(mirrored)
    expected = manager.uninstall_inventory()
    record = _renumbered_receipt(manager)
    raw = upgrade.encoded(record)
    manager._write('source-scratch.json', raw)
    with pytest.raises(upgrade.UpgradeError, match='source-scratch-invalid'):
        with manager._scratch():
            pass

    assert manager.uninstall_inventory() == expected
    # Validation only reads: the receipt and the buffer stay as they were.
    assert (manager.state / 'source-scratch.json').read_bytes() == raw
    assert (old / record['name']).is_dir() and list((old / record['name']).iterdir()) == []


@pytest.mark.parametrize('condition', ['buffer-inode', 'content', 'mode', 'parent-inode',
                                       'split-device', 'pending'])
def test_completed_uninstall_inventory_refuses_anything_but_a_renumbered_device(mirrored, condition):
    manager, old = _completed(mirrored)
    record = _renumbered_receipt(manager)
    buffer = old / record['name']
    if condition == 'buffer-inode':
        record['identity'][1] += 1
    elif condition == 'content':
        (buffer / 'payload').write_text('never adopt')
    elif condition == 'mode':
        buffer.chmod(0o755)
    elif condition == 'parent-inode':
        record['parent'][1] += 1
    elif condition == 'split-device':
        record['identity'][0] += 1
    else:
        (manager.state.parent / 'transition.json').write_text('{}')
    raw = upgrade.encoded(record)
    manager._write('source-scratch.json', raw)

    with pytest.raises(upgrade.UpgradeError):
        manager.uninstall_inventory()
    assert (manager.state / 'source-scratch.json').read_bytes() == raw


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
    # Past the downstream boundary (a taken-over or resumed update), the
    # applied tree's coordinator is installed before anything else runs.
    ('downstream', '', ['restore', 'stage', 'status', 'hold', 'copy', 'downstream', 'bootstrap']),
    ('downstream', 'copy', ['restore', 'stage', 'status', 'hold', 'copy']),
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
_phase06_source_failed() { return 1; }
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
jq() {
  case "$2" in
    '.downstream == true') [[ "$PHASE" == downstream ]] ;;
    *) [[ "$PHASE" == staged ]] ;;
  esac
}
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


@pytest.mark.parametrize('before_mode', [0o600, 0o755])
def test_drvfs_plugin_projection_survives_real_normalizer_and_rollback(trees, before_mode):
    manager, old, new, identity = trees
    plugin = 'extensions/services/pixel-agent/plugin'
    paths = [plugin + '/index.mjs', plugin + '/nested/helper.py']
    for rel in paths:
        for root, mode, content in ((old, before_mode, 'before'), (new, 0o777, 'candidate')):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            path.chmod(mode)
    siblings = ['extensions/services/pixel-agent/plugin-other/run.sh',
                'extensions/services/pixel-agent/host/run.sh']
    for rel in siblings:
        path = new / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('executable')
        path.chmod(0o777)
    verify, _ = held(manager, new, identity)
    plan = manager.journal()
    assert all(plan['before'][rel]['mode'] == before_mode for rel in paths)
    assert all(plan['candidate'][rel]['mode'] == 0o644 for rel in paths)
    assert all(plan['candidate'][rel]['mode'] == 0o755 for rel in siblings)
    manager.publish(verify)
    # Exercise the production normalizer, which previously invalidated the
    # protected plan between source publication and coordinator handoff.
    helper = MODULE.parent.parent / 'installers/lib/pixel-host-install.sh'
    result = subprocess.run(['bash', '-c',
        'source "$1"; INSTALL_DIR="$2"; '
        '_ods_pixel_secure_plugin_tree "$(id -un)" "$HOME" "$2/extensions/services/pixel-agent/plugin"',
        'normalize-fixture', str(helper), str(old)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert upgrade.inventory(old, os.getuid()) == plan['after']
    assert all((old / rel).stat().st_mode & 0o777 == 0o755 for rel in siblings)
    manager.publish(verify, rollback=True)
    assert upgrade.inventory(old, os.getuid()) == plan['before']
    assert all((old / rel).read_text() == 'before' for rel in paths)


@pytest.mark.parametrize('mutation', ['bytes', 'mode'])
def test_plugin_projection_keeps_installed_drift_detection(trees, mutation):
    manager, old, new, identity = trees
    rel = 'extensions/services/pixel-agent/plugin/index.mjs'
    for root in (old, new):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('original')
        path.chmod(0o755)
    verify, _ = held(manager, new, identity)
    manager.publish(verify)
    if mutation == 'bytes':
        (old / rel).write_text('changed')
    else:
        (old / rel).chmod(0o600)
    with pytest.raises(upgrade.UpgradeError, match='source-live-drift'):
        manager.publish(verify, rollback=True)


def test_plugin_candidate_bytes_remain_bound_to_held_plan(trees):
    manager, old, new, identity = trees
    path = new / 'extensions/services/pixel-agent/plugin/index.mjs'
    path.parent.mkdir(parents=True)
    path.write_text('original')
    path.chmod(0o777)
    held(manager, new, identity)
    path.write_text('changed')
    with pytest.raises(upgrade.UpgradeError, match='source-candidate-changed'):
        manager.stage(new, os.getuid(), identity)


def applied(manager, new, identity, token='d' * 64):
    """A held plan whose tree is fully applied and past the downstream boundary."""
    manager.stage(new, os.getuid(), identity)
    manager.bind(token, lambda _: None)
    manager.publish(lambda _: None)
    manager._write(manager.downstream_name(), upgrade.encoded({'transaction': token}))
    return manager.journal()


def test_corrected_candidate_takes_over_a_failed_update_under_its_hold(mirrored):
    manager, old, new, identity, mirror = mirrored
    stuck = applied(manager, new, identity)
    stuck_downstream = manager.downstream_name()
    (new / 'bin/a.py').write_text('corrected')
    # Without an explicit takeover a different candidate is still refused.
    with pytest.raises(upgrade.UpgradeError, match='source-candidate-changed'):
        manager.stage(new, os.getuid(), identity)
    plan = manager.stage(new, os.getuid(), identity, supersede=True)
    assert plan['hold'] == stuck['hold'] and plan['phase'] == 'held'
    assert plan['identity'] == identity
    assert plan['before'] == stuck['after'] == upgrade.inventory(old, os.getuid())
    raw = upgrade.encoded(stuck)
    assert (manager.state / upgrade.sha(raw)).read_bytes() == raw
    assert (manager.state / stuck_downstream).exists()
    assert json.loads((manager.state / manager.downstream_name()).read_text()) == {'transaction': stuck['hold']}
    # Rerunning the same corrected installer resumes; it is not a new takeover.
    assert manager.stage(new, os.getuid(), identity, supersede=True) == plan
    manager.bind(stuck['hold'], lambda _: None)
    manager.publish(lambda _: None)
    assert (old / 'bin/a.py').read_text() == 'corrected'
    # The applied tree's coordinator is recorded for the new plan before finish.
    for name in ('pixel_access_bridge.py', 'pixel_source_upgrade.py', 'access_mode_server.py'):
        manager.record_mirror_write(mirror / name, (mirror / name).read_bytes(), 0o644,
                                    os.getuid(), os.getgid())
    manager.finish(lambda *_: None)
    assert manager.journal()['phase'] == 'complete'
    manager.uninstall_inventory()


@pytest.mark.parametrize('state', ['staged', 'held', 'restored', 'identity', 'drift', 'mirror'])
def test_takeover_refuses_anything_but_an_exactly_applied_plan(mirrored, state):
    manager, old, new, identity, mirror = mirrored
    if state == 'held':
        manager.bind('d' * 64, lambda _: None)
    elif state != 'staged':
        applied(manager, new, identity)
    if state == 'restored':
        manager.publish(lambda _: None, rollback=True)
    before = manager.journal()
    requested = dict(identity, afterRef='f' * 40) if state == 'identity' else identity
    if state == 'drift':
        (old / 'bin/a.py').write_text('owner edit')
    if state == 'mirror':
        (mirror / 'pixel_access_bridge.py').write_bytes(b'replaced coordinator')
    (new / 'bin/a.py').write_text('corrected')
    with pytest.raises(upgrade.UpgradeError, match='supersede-refused|live-drift|mirror-changed'):
        manager.stage(new, os.getuid(), requested, supersede=True)
    assert manager.journal() == before


@pytest.mark.parametrize('plan_phase,status_phase,downstream,expected', [
    ('staged', None, False, True),
    ('held', 'acquiring', False, True),
    ('held', 'error', False, True),
    ('held', 'error', True, False),
    ('applied', 'error', True, False),
    ('held', 'held', True, False),
])
def test_hold_reacquires_admission_only_before_the_downstream_boundary(
        plan_phase, status_phase, downstream, expected):
    plan = dict(phase=plan_phase, hold=None if plan_phase == 'staged' else 'd' * 64)
    status = dict(pending=True, phase=status_phase) if status_phase else {'pending': False}
    assert upgrade.needs_source_begin(plan, status, downstream) is expected


@pytest.mark.parametrize('before_ref,version,active,accepted', [
    ('a' * 40, b'4.3.29\n', '4.3.28', True),
    ('a' * 40, b'4.10.0\n', '4.9.9', True),  # numeric, not lexical, ordering
    ('a' * 40, b'4.3.28\n', '4.3.28', False),  # different source with the active version
    ('a' * 40, b'4.3.27\n', '4.3.28', False),
    ('a' * 40, b'4.3.29-rc1\n', '4.3.28', False),
    ('a' * 40, None, '4.3.28', False),  # the incoming source has no VERSION
    ('a' * 40, b'4.3.29\n', '', False),  # no verified active release to compare against
    ('b' * 40, b'4.3.28\n', '4.3.28', True),  # the same source needs no new version
])
def test_changed_pixel_source_requires_a_strictly_newer_release(tmp_path, monkeypatch, before_ref, version,
                                                                 active, accepted):
    install = tmp_path / 'install'
    marker = dict(schema_version=2, manager='ods', install_dir=str(install), initial_active_state='absent',
                  state='ready', pixel_source_ref=before_ref, active_release_version=active)
    files = {'extensions/services/pixel-edge/compose.yaml': b'services: {}\n',
             'extensions/services/litellm/compose.yaml': b'services: {}\n',
             'vendor/pixel/VERSION': version}

    def read_file(root, name, uid, *, candidate=False):
        raw = files.get(name)
        return (None, None) if raw is None else (dict(sha256=upgrade.sha(raw), mode=0o644), raw)

    monkeypatch.setattr(upgrade, 'read_file', read_file)
    monkeypatch.setattr(upgrade, '_protected_json', lambda path, uid=0: (marker, 'c' * 64))
    monkeypatch.setattr(upgrade, 'owner_baseline', lambda home, uid: dict(configSha256='e' * 64, receiptSha256=None))
    staged = []
    manager = SimpleNamespace(install=install, state=tmp_path / 'state/source-upgrade', journal=lambda: None,
                              stage=lambda source, uid, identity, **options: staged.append(identity))
    account = SimpleNamespace(pw_dir=str(tmp_path / 'home'), pw_uid=1000)
    if accepted:
        upgrade._stage(manager, account, str(tmp_path / 'candidate'), 'b' * 40)
        assert [identity['beforeRef'] for identity in staged] == [before_ref]
    else:
        with pytest.raises(upgrade.UpgradeError, match='source-new-version-required'):
            upgrade._stage(manager, account, str(tmp_path / 'candidate'), 'b' * 40)
        assert staged == []
