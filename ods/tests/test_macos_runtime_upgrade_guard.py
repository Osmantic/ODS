from contextlib import contextmanager
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import pixel_access_bridge as bridge


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    value = object.__new__(bridge.LaunchdAccessBridge)
    value.state = tmp_path
    held = []
    @contextmanager
    def locked(self):
        held.append(True)
        try: yield
        finally: held.pop()
    monkeypatch.setattr(bridge.SystemdAccessBridge, 'locked', locked)
    return value, held


@pytest.mark.parametrize('marker', ['{}', '{"phase":"active"}', '{"phase":"restored"}', 'broken', 'link'])
def test_any_pending_upgrade_blocks_mutation_and_reports_recovery(adapter, marker):
    value, held = adapter
    path = value.state / 'runtime-upgrade.json'
    if marker == 'link': path.symlink_to(value.state / 'missing')
    else: path.write_text(marker)
    with pytest.raises(bridge.AccessError, match='runtime-upgrade-recovery-required'):
        with value.locked():
            pytest.fail('mutation admitted')
    assert held == []
    status = value.status()
    assert status['pending'] and not status['available'] and not status['runtime_verified']
    assert status['reason'] == 'runtime-upgrade-recovery-required'


def test_no_upgrade_preserves_existing_status_and_lock(adapter, monkeypatch):
    value, held = adapter
    monkeypatch.setattr(bridge.SystemdAccessBridge, 'status', lambda self: {'original': True})
    assert value.status() == {'original': True}
    with value.locked():
        assert held == [True]
    assert held == []


def test_recovery_lock_requires_pending_upgrade(adapter):
    value, held = adapter
    with pytest.raises(bridge.AccessError, match='runtime-upgrade-journal-required'):
        with value.recovery_locked():
            pytest.fail('missing recovery admitted')
    assert held == []


@pytest.mark.parametrize('fault', [None, 'missing', 'pending', 'digest', 'transition'])
def test_completed_recovery_lock_requires_selected_archive(adapter, fault):
    value, held = adapter
    digest = 'b' * 64
    if fault != 'missing':
        (value.state / ('runtime-upgrade-' + digest + '.completed.json')).write_text('{}')
    if fault == 'pending': (value.state / 'runtime-upgrade.json').write_text('{}')
    if fault == 'transition': (value.state / 'transition.json').write_text('{}')
    def run():
        with value.recovery_locked(completed_digest='../bad' if fault == 'digest' else digest):
            assert held == [True]
    if fault:
        with pytest.raises(bridge.AccessError): run()
    else: run()
    assert held == []


@pytest.mark.parametrize('pending', [None, 'transition.json', 'policy-activation.json'])
def test_recovery_uses_same_lock_without_admitting_normal_mutations(adapter, pending):
    value, held = adapter
    (value.state / 'runtime-upgrade.json').write_text('{}')
    if pending:
        (value.state / pending).write_text('{}')
        with pytest.raises(bridge.AccessError, match='runtime-upgrade-pending-recovery'):
            with value.recovery_locked():
                pytest.fail('conflicting transition admitted')
    else:
        with value.recovery_locked():
            assert held == [True]
            assert value.status()['reason'] == 'runtime-upgrade-recovery-required'
        with pytest.raises(bridge.AccessError, match='runtime-upgrade-recovery-required'):
            with value.locked():
                pytest.fail('normal mutation admitted')
    assert held == []
