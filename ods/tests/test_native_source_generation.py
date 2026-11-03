"""Actual publisher FDs/renames; Linux/procfs with explicit Darwin metadata simulation.

No account, launchctl mutation, SDK/provider/owner execution, or active generation.
The shared fixture retains real protected bundle/RuntimeCustody/bridge admission.
"""
# ruff: noqa: E402
from contextlib import contextmanager
import json
import os
from pathlib import Path
import plistlib
import sys

import pytest

if not sys.platform.startswith('linux') or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs metadata harness, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_connection_deployment_paths import deployment  # noqa: F401
from test_native_source_custody import native  # noqa: F401
from test_native_source_runtime_publication import built, source, package  # noqa: F401
from test_native_source_publication import material, publication, helper  # noqa: F401
from pixel_connections import custody
import pixel_macos_custody

SPECS = [{'role': 'sync', 'identifier': None}, {'role': 'direct', 'identifier': None},
    {'role': 'action', 'identifier': 'calendar-1893456789012-12abcdef'},
    {'role': 'reconcile', 'identifier': 'gmail-draft-1893456789012-34abcdef'},
    {'role': 'exchange', 'identifier': 'abcdef0123456789abcdef0123456789'},
    {'role': 'revoke', 'identifier': None}]


@pytest.fixture
def generation(publication, monkeypatch):  # noqa: F811
    n = publication
    helper.publish_kit(n.selected)
    n.before_gate = (custody.NATIVE_STATE/'custody.json').read_bytes()
    n.before_gate_inode = (custody.NATIVE_STATE/'custody.json').stat().st_ino
    n.units = n.world/'definitions'
    n.units.mkdir(mode=0o755)
    monkeypatch.setattr(custody, 'NATIVE_DEFINITIONS', n.units)
    original = pixel_macos_custody.protected_directory
    @contextmanager
    def directory(path, **kwargs):
        if Path(path) == n.units:
            assert not kwargs
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: yield fd
            finally: os.close(fd)
        else:
            with original(path, **kwargs) as fd: yield fd
    monkeypatch.setattr(pixel_macos_custody, 'protected_directory', directory)
    return n


def saved(n):
    return json.loads((n.metadata/'source-jobs-prepare.json').read_bytes())


def test_actual_publisher_commits_only_inactive_v2_after_all_disabled_inputs(generation, monkeypatch):
    n = generation
    original = os.rename
    order = []
    def rename(first, second, **kwargs):
        if second == 'custody.json':
            record = saved(n)
            assert record['phase'] == 'moving-gate'
            assert len(list(n.units.glob('*.plist'))) == 6
            assert (n.metadata/'native-source-jobs/manifest.json').is_file()
            assert all(plistlib.loads(path.read_bytes())['Disabled'] is True for path in n.units.iterdir())
        order.append((first, second))
        return original(first, second, **kwargs)
    monkeypatch.setattr(os, 'rename', rename)
    result = helper.publish_disabled_generation(n.selected, SPECS)
    assert result['status'] == 'inactive' and result['definitionCount'] == 6
    record = saved(n)
    projection = json.loads((custody.NATIVE_STATE/'custody.json').read_bytes())
    assert projection['schemaVersion'] == 2 and projection['phase'] == projection['launch']['phase'] == 'inactive'
    assert record['phase'] == 'committed-inactive' and projection['launch']['generation'] == record['generation']
    assert order[-1] == ('.gate-next', 'custody.json')
    assert custody.NATIVE_ENV.stat().st_ino == n.env_inode and custody.NATIVE_ENV.read_bytes() == n.env_before
    assert helper.publish_disabled_generation(n.selected, SPECS) == result
    assert all(argv[:2] not in (['/bin/launchctl','bootstrap'], ['/bin/launchctl','kickstart']) for argv in n.commands)
    with pytest.raises(custody.CustodyError, match='generation-requires-lifecycle'):
        custody.native_publish_environment(n.selected, n.env_before.decode())
    with pytest.raises(custody.CustodyError, match='generation-not-admitted'):
        custody._native_child_main(['--native-broker-child', 'sync'])


def test_rollback_restores_exact_v1_inode_and_all_definition_absences(generation):
    n = generation
    result = helper.publish_disabled_generation(n.selected, SPECS)
    record = saved(n)
    restored = helper.publish_disabled_generation(n.selected, SPECS, rollback=True)
    assert restored == {'status': 'restored', 'generation': result['generation']}
    assert (custody.NATIVE_STATE/'custody.json').read_bytes() == n.before_gate
    assert (custody.NATIVE_STATE/'custody.json').stat().st_ino == n.before_gate_inode
    assert list(n.units.iterdir()) == [] and not (n.metadata/'native-source-jobs').exists()
    retained = n.metadata/('.native-source-jobs-'+result['generation'])
    assert retained.stat().st_ino == record['directory'][1] and retained.stat().st_mode & 0o777 == 0o700
    assert helper.publish_disabled_generation(n.selected, SPECS, rollback=True) == restored
    with pytest.raises(ValueError, match='explicit-recovery'):
        helper.publish_disabled_generation(n.selected, SPECS)


@pytest.mark.parametrize('where', ['before-definition', 'after-definition', 'after-old-gate', 'after-new-gate'])
def test_lost_rename_reply_resumes_exact_recorded_generation(generation, monkeypatch, where):
    n = generation
    original = os.rename
    hit = []
    def rename(first, second, **kwargs):
        chosen = (where in ('before-definition','after-definition') and str(first).endswith('.plist')
            or where == 'after-old-gate' and second == '.gate-before'
            or where == 'after-new-gate' and first == '.gate-next')
        if chosen and not hit:
            hit.append(True)
            if where != 'before-definition': original(first, second, **kwargs)
            raise OSError('invented lost rename acknowledgement')
        return original(first, second, **kwargs)
    monkeypatch.setattr(os, 'rename', rename)
    with pytest.raises(OSError, match='lost rename'):
        helper.publish_disabled_generation(n.selected, SPECS)
    record = saved(n)
    assert hit
    monkeypatch.setattr(os, 'rename', original)
    result = helper.publish_disabled_generation(n.selected, SPECS)
    assert result['status'] == 'inactive' and result['generation'] == record['generation']
    assert saved(n)['definitions'] == record['definitions']


@pytest.mark.parametrize('what', ['unknown-definition', 'unknown-directory', 'duplicate', 'bad-id', 'too-many', 'duck-selection'])
def test_unknown_namespace_and_invalid_inputs_never_create_intent(generation, what):
    n = generation
    specs, selection = SPECS, n.selected
    if what == 'unknown-definition': (n.units/(custody.NATIVE_LABEL+'sync.plist')).write_bytes(b'preserve')
    if what == 'unknown-directory': (n.metadata/'native-source-jobs').mkdir(mode=0o700)
    if what == 'duplicate': specs = SPECS+SPECS[:1]
    if what == 'bad-id': specs = [{'role':'action','identifier':'../../not-an-action'}]
    if what == 'too-many': specs = SPECS*11
    if what == 'duck-selection': selection = type('Pretend', (), {'guard':lambda _self:n.f.value.document})()
    before = {path.name:path.read_bytes() for path in n.units.iterdir()}
    with pytest.raises((ValueError,custody.CustodyError)):
        helper.publish_disabled_generation(selection, specs)
    assert not (n.metadata/'source-jobs-prepare.json').exists()
    assert {path.name:path.read_bytes() for path in n.units.iterdir()} == before


@pytest.mark.parametrize('change', ['valid-prefix', 'same-bytes-new-inode', 'foreign-bytes', 'unknown-entry'])
def test_recorded_prefix_resumes_but_unknown_images_cannot_publish_or_rollback(generation, monkeypatch, change):
    n = generation
    original = custody._native_save_at
    stopped = []
    def save(selection, directory, name, value, **kwargs):
        result = original(selection, directory, name, value, **kwargs)
        if name == 'source-jobs-prepare.json' and not stopped and any(
                item['inode'] is not None and not item['ready'] for item in value['definitions'].values()):
            stopped.append(True)
            raise OSError('invented stop after durable empty-file inode')
        return result
    monkeypatch.setattr(custody, '_native_save_at', save)
    with pytest.raises(OSError, match='durable empty-file'):
        helper.publish_disabled_generation(n.selected, SPECS[:1])
    record = saved(n)
    label, item = next(iter(record['definitions'].items()))
    path = n.metadata/'native-source-jobs'/(label+'.plist')
    body = helper.disabled_definition('sync', identity=n.identity, python=n.f.value.document['hostPythonPath'])[1]
    assert path.stat().st_ino == item['inode'][1] and path.read_bytes() == b''
    if change == 'valid-prefix': path.write_bytes(body[:91])
    if change == 'same-bytes-new-inode':
        replacement = path.with_name('replacement')
        replacement.write_bytes(body[:91])
        replacement.chmod(0o600)
        replacement.replace(path)
    if change == 'foreign-bytes': path.write_bytes(b'invented private unknown bytes')
    if change == 'unknown-entry': (path.parent/'foreign').write_bytes(b'preserve unknown')
    monkeypatch.setattr(custody, '_native_save_at', original)
    if change == 'valid-prefix':
        result = helper.publish_disabled_generation(n.selected, SPECS[:1])
        target = n.units/(label+'.plist')
        assert result['status'] == 'inactive' and target.stat().st_ino == item['inode'][1]
        assert target.read_bytes() == body
    else:
        before = {p.name:p.read_bytes() for p in path.parent.iterdir()}
        for rollback in (False, True):
            with pytest.raises((ValueError,custody.CustodyError)):
                helper.publish_disabled_generation(n.selected, SPECS[:1], rollback=rollback)
        assert {p.name:p.read_bytes() for p in path.parent.iterdir()} == before
        assert list(n.units.iterdir()) == [] and (custody.NATIVE_STATE/'custody.json').read_bytes() == n.before_gate


def test_replaced_directory_after_open_never_receives_commit_or_adoption(generation, monkeypatch):
    n = generation
    original = os.fchmod
    moved = []
    def chmod(fd, mode):
        original(fd, mode)
        location = Path(os.readlink('/proc/self/fd/'+str(fd)))
        if not moved and mode == 0o644 and location.name.endswith('.plist'):
            moved.append(True)
            (n.metadata/'native-source-jobs').rename(n.metadata/'held-original')
            (n.metadata/'native-source-jobs').mkdir(mode=0o700)
            (n.metadata/'native-source-jobs/unknown').write_text('preserve')
    monkeypatch.setattr(os, 'fchmod', chmod)
    with pytest.raises(ValueError, match='directory-changed'):
        helper.publish_disabled_generation(n.selected, SPECS[:1])
    assert moved and (n.metadata/'native-source-jobs/unknown').read_text() == 'preserve'
    assert list(n.units.iterdir()) == [] and (custody.NATIVE_STATE/'custody.json').read_bytes() == n.before_gate


@pytest.mark.parametrize('phase', ['unknown', 'retired'])
def test_terminal_generation_never_automatically_reactivates(generation, phase):
    n = generation
    helper.publish_disabled_generation(n.selected, SPECS[:1])
    record = saved(n)
    record['phase'] = phase
    path = n.metadata/'source-jobs-prepare.json'
    path.write_bytes(helper.encoded(record))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='explicit-recovery'):
        helper.publish_disabled_generation(n.selected, SPECS[:1])
    assert path.read_bytes() == before
    assert all(plistlib.loads(p.read_bytes())['Disabled'] is True for p in n.units.iterdir())


@pytest.mark.parametrize('where', ['gate-restored', 'definition-restored', 'directory-retained'])
def test_rollback_lost_acknowledgement_resumes_without_deleting_retained_data(generation, monkeypatch, where):
    n = generation
    helper.publish_disabled_generation(n.selected, SPECS[:1])
    original = os.rename
    hit = []
    def rename(first, second, **kwargs):
        chosen = (where == 'gate-restored' and first == '.gate-before'
            or where == 'definition-restored' and str(first).endswith('.plist')
            or where == 'directory-retained' and first == 'native-source-jobs')
        if chosen and not hit:
            hit.append(True)
            original(first, second, **kwargs)
            raise OSError('invented rollback acknowledgement loss')
        return original(first, second, **kwargs)
    monkeypatch.setattr(os, 'rename', rename)
    with pytest.raises(OSError, match='rollback acknowledgement'):
        helper.publish_disabled_generation(n.selected, SPECS[:1], rollback=True)
    assert hit
    monkeypatch.setattr(os, 'rename', original)
    assert helper.publish_disabled_generation(n.selected, SPECS[:1], rollback=True)['status'] == 'restored'
    assert (custody.NATIVE_STATE/'custody.json').stat().st_ino == n.before_gate_inode
    assert list(n.units.iterdir()) == []
