"""Actual temporary files, simulated root/admission; not installed acceptance."""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_access_bridge import AccessError, atomic_json
from pixel_provider import service_environment as env
from pixel_provider.managed_deployment import deployment, required_policy

BINDING = {'schemaVersion': 1, 'activationId': '123e4567-e89b-12d3-a456-426614174000',
           'revision': 1, 'allowCloud': False}


class Bridge:
    def __init__(self, root):
        self.state, self.home = root / 'state', root / 'home'
        self.state.mkdir(mode=0o700)
        (self.home / '.openclaw').mkdir(parents=True, mode=0o700)
        self.owner = SimpleNamespace(pw_uid=os.getuid())
        self.phase, self.active, self.streams = 'held', 0, 0
        self.token = 'b' * 64
        self.record = {'kind': 'provider', 'phase': 'invoking', 'token': self.token,
                       'transactionId': 'a' * 64, 'edge_revision': 'c' * 64}
        atomic_json(self.state / 'transition.json', self.record)
        self.config = self.home / '.openclaw/openclaw.json'
        self.before = b'{"route":"before"}\n'
        self.after = b'{"route":"after"}\n'
        self.config.write_bytes(self.before)
        self.config.chmod(0o600)

    def pending(self):
        return json.loads((self.state / 'transition.json').read_text())

    def native(self, operation=None, token=None, **kwargs):
        if operation and token != self.token:
            raise AccessError('native-lease-unconfirmed')
        return {'phase': self.phase, 'active': self.active}

    def edge(self, operation=None, token=None, revision=None):
        if operation and token != self.token:
            raise AccessError('edge-lease-unconfirmed')
        return {'phase': self.phase, 'streams': self.streams}


@pytest.fixture
def participant(tmp_path, monkeypatch):
    # Explicit custody simulation: tests run as a non-root CI owner in /tmp.
    # All file type, mode, nlink, inode and durability code remains real.
    monkeypatch.setattr(env, 'ROOT_UID', os.getuid())
    original = env._parents
    def parents(path):
        if path.resolve() != path:
            raise AccessError('unsafe-provider-service-path')
        for entry in path.parents:
            if entry == tmp_path.parent:
                break
            info = entry.lstat()
            if not entry.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise AccessError('unsafe-provider-service-directory')
    monkeypatch.setattr(env, '_parents', parents)
    bridge = Bridge(tmp_path)
    directory = tmp_path / 'service'
    directory.mkdir(mode=0o700)
    value = env.ServiceEnvironment(bridge, environment=directory / 'provider.env', dropin=directory / '95-provider.conf')
    value.original_parents = original
    return value


def arm(value, **overrides):
    b = value.bridge
    options = {'before_sha': hashlib.sha256(b.before).hexdigest(), 'after_sha': hashlib.sha256(b.after).hexdigest(),
               'before_binding': None, 'after_binding': copy.deepcopy(BINDING),
               'deployment_document': deployment(BINDING, '/opt/ods/source', '/usr/bin/python3', '/home/ods/providers', True),
               'policy': required_policy()}
    options.update(overrides)
    return value.prepare(b.record, **options)


def test_forward_then_same_callback_rollback_restores_absence(participant):
    p, b = participant, participant.bridge
    record = arm(p)
    assert bytes.fromhex(record['after']['dropin']['hex']) == ('[Service]\nEnvironmentFile=' + str(p.environment)
        + '\nBindPaths=/home/ods/providers\n').encode()
    assert p.snapshot() == {'environment': None, 'dropin': None}
    b.config.write_bytes(b.after)
    selected = p.apply(b.record)
    assert selected['binding'] == BINDING and selected['side'] == 'after'
    assert p.snapshot() == record['after']
    assert p.verify(b.record, selected) == record['after']
    b.config.write_bytes(b.before)
    restored = p.apply(b.record)
    assert restored['binding'] is None and restored['side'] == 'before'
    assert p.verify(b.record, restored) == record['before']
    assert not p.environment.exists() and not p.dropin.exists()
    assert b.pending() == b.record and b.phase == 'held'


@pytest.mark.parametrize('directory,error', [
    ('/home/ods/with space', AccessError),
    ('/home/ods/%h', AccessError),
    ('/home/ods/a:b', AccessError),
    ('/home/ods/../elsewhere', ValueError),
])
def test_provider_mount_cannot_expand_unit_syntax_or_escape_selected_path(participant, directory, error):
    doc = deployment(BINDING, '/opt/ods/source', '/usr/bin/python3', '/home/ods/providers', True)
    doc['providerDirectory'] = directory
    with pytest.raises(error): arm(participant, deployment_document=doc)
    assert participant.snapshot() == {'environment': None, 'dropin': None}


def test_second_prepare_cannot_overwrite_recovery_images(participant):
    arm(participant)
    original = (participant.bridge.state / env.RECORD).read_bytes()
    with pytest.raises(AccessError, match='already-prepared'):
        arm(participant)
    assert (participant.bridge.state / env.RECORD).read_bytes() == original


@pytest.mark.parametrize('failure', ['config', 'busy', 'streams', 'idle', 'journal'])
def test_refuse_before_any_service_write(participant, failure):
    p, b = participant, participant.bridge
    arm(p)
    b.config.write_bytes(b.after)
    if failure == 'config': b.config.write_bytes(b'{"other":1}\n')
    if failure == 'busy': b.active = 1
    if failure == 'streams': b.streams = 1
    if failure == 'idle': b.phase = 'idle'
    if failure == 'journal': atomic_json(b.state / 'transition.json', dict(b.record, transactionId='d' * 64))
    with pytest.raises(AccessError): p.apply(b.record)
    assert not p.environment.exists() and not p.dropin.exists()


def test_partial_write_crash_is_recoverable_without_adopting_state(participant, monkeypatch):
    p, b = participant, participant.bridge
    record = arm(p)
    b.config.write_bytes(b.after)
    replace = env._replace
    def fail_dropin(path, expected, image):
        if path == p.dropin: raise OSError('injected disk failure')
        replace(path, expected, image)
    monkeypatch.setattr(env, '_replace', fail_dropin)
    with pytest.raises(OSError): p.apply(b.record)
    assert p.snapshot() == {'environment': record['after']['environment'], 'dropin': None}
    monkeypatch.setattr(env, '_replace', replace)
    b.config.write_bytes(b.before)
    assert p.apply(b.record)['side'] == 'before'
    assert p.snapshot() == record['before']


def test_foreign_file_is_not_overwritten_during_recovery(participant):
    p, b = participant, participant.bridge
    arm(p)
    b.config.write_bytes(b.after)
    p.apply(b.record)
    p.environment.write_bytes(b'FOREIGN=not-ours\n')
    b.config.write_bytes(b.before)
    with pytest.raises(AccessError, match='recovery-conflict'): p.apply(b.record)
    assert p.environment.read_bytes() == b'FOREIGN=not-ours\n' and p.dropin.exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'writable', 'directory', 'parent'])
def test_unsafe_service_files_refused(participant, tmp_path, kind):
    p, b = participant, participant.bridge
    arm(p)
    b.config.write_bytes(b.after)
    target = tmp_path / 'unrelated'
    target.write_bytes(b'preserve')
    target.chmod(0o600)
    if kind == 'symlink': p.environment.symlink_to(target)
    if kind == 'hardlink': os.link(target, p.environment)
    if kind == 'writable':
        p.environment.write_bytes(b'x')
        p.environment.chmod(0o666)
    if kind == 'directory': p.environment.mkdir()
    if kind == 'parent': p.environment.parent.chmod(0o777)
    with pytest.raises(AccessError): p.apply(b.record)
    assert target.read_bytes() == b'preserve' and not p.dropin.exists()


def test_config_change_during_admission_prevents_file_write(participant, monkeypatch):
    p, b = participant, participant.bridge
    arm(p)
    b.config.write_bytes(b.after)
    edge = b.edge
    def raced(*args):
        b.config.write_bytes(b.before)
        return edge(*args)
    monkeypatch.setattr(b, 'edge', raced)
    with pytest.raises(AccessError, match='config-changed'): p.apply(b.record)
    assert not p.environment.exists() and not p.dropin.exists()


def test_update_requires_exact_root_previous_image_and_deactivation_restores_it(participant):
    p, b = participant, participant.bridge
    original = arm(p)
    b.config.write_bytes(b.after)
    p.apply(b.record)
    baseline = original['before']
    b.record = dict(b.record, transactionId='d' * 64)
    b.record.pop('serviceEnvironment')
    atomic_json(b.state / 'transition.json', b.record)
    b.before, b.after = b.after, b.before
    arm(p, before_binding=BINDING, after_binding=None, deployment_document=None, policy=None,
        restore=baseline, expected=original['after'])
    b.config.write_bytes(b.after)
    assert p.apply(b.record)['binding'] is None
    assert p.snapshot() == baseline


def test_changed_root_record_fails_digest_binding(participant):
    p, b = participant, participant.bridge
    record = arm(p)
    record['afterSha'] = 'e' * 64
    atomic_json(b.state / env.RECORD, record)
    with pytest.raises(AccessError, match='invalid-provider-service-record'): p.load(b.record)


def test_verify_refuses_post_apply_file_drift(participant):
    p, b = participant, participant.bridge
    arm(p)
    b.config.write_bytes(b.after)
    selection = p.apply(b.record)
    p.dropin.unlink()
    with pytest.raises(AccessError, match='verification-changed'): p.verify(b.record, selection)


def test_initial_adoption_refuses_existing_environment(participant):
    participant.environment.write_bytes(b'EXISTING=yes\n')
    participant.environment.chmod(0o600)
    with pytest.raises(AccessError, match='baseline-conflict'): arm(participant)
    assert participant.environment.read_bytes() == b'EXISTING=yes\n'


def test_equal_config_hashes_are_ambiguous(participant):
    with pytest.raises(AccessError, match='ambiguous-provider-config-hash'):
        arm(participant, after_sha=hashlib.sha256(participant.bridge.before).hexdigest())
