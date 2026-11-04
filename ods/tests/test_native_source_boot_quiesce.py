"""Fresh current-boot process control; Linux driver, no physical reboot claim."""
# ruff: noqa: E402,F401,F811
import json
from pathlib import Path
import sys

import pytest

if sys.platform != 'linux':
    pytest.skip('Linux owned-child/metadata driver, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_executor import (executor, qualification_operation, generation, built,
    source, package, native, deployment, material, publication, helper)
from pixel_connections import custody as c, native_executor as ex

NEW_BOOT = 'ABCDEF01-2345-4ABC-8DEF-0123456789AB'


def test_old_boot_live_label_uses_fresh_owned_child_identity_not_old_journal_pids(executor, monkeypatch):
    n = executor
    original = n.executor.start('sync')
    old_pid = original['identity'][0]
    # Old execution data is deliberately invalid as a current process proof.
    value, raw, _doc, _public = n.executor._read()
    value['jobs'][c.NATIVE_LABEL + 'sync']['tree'] = [[1, 123, 0]]
    n.executor._save(value, raw)
    monkeypatch.setattr(c, '_native_boot_uuid', lambda: NEW_BOOT)
    commands = n.driver
    def command(arguments, timeout=20):
        if arguments == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']:
            return NEW_BOOT + '\n'
        return commands(arguments, timeout)
    monkeypatch.setattr(n.f.bridge, 'command', command)
    births, actual_birth = [], ex.process_birth
    def birth(pid):
        births.append(pid)
        assert pid != 1, 'An old execution-journal PID became authority after reboot'
        return actual_birth(pid)
    monkeypatch.setattr(ex, 'process_birth', birth)
    with pytest.raises(c.CustodyError, match='execution-binding-changed'):
        n.executor.observe('sync')
    n.driver.fail = 'after-stop'
    result = ex.quiesce_previous_boot(n.selected)
    assert result['status'] == 'stopped' and result['boot'] == NEW_BOOT
    assert not n.driver.jobs and old_pid in births
    record = json.loads((c.NATIVE_META / 'source-boot-quiesce.json').read_bytes())
    assert record['phase'] == 'stopped' and record['jobs'][c.NATIVE_LABEL + 'sync']['identity'][0] == old_pid
    before = sum(args[:2] == ['/bin/launchctl', 'bootout'] for args in n.driver.sent)
    ex.quiesce_previous_boot(n.selected)
    assert sum(args[:2] == ['/bin/launchctl', 'bootout'] for args in n.driver.sent) == before
    assert not any('-k' in args for args in n.driver.sent)
    # Quiescence does not renew old invocations or make their Gate usable.
    with pytest.raises(c.CustodyError, match='execution-binding-changed'):
        n.executor.start('sync')


def test_current_boot_or_drifted_definition_cannot_enter_reboot_control(executor, monkeypatch):
    n = executor
    with pytest.raises(c.CustodyError, match='boot-quiesce-not-required'):
        ex.quiesce_previous_boot(n.selected)
    monkeypatch.setattr(c, '_native_boot_uuid', lambda: NEW_BOOT)
    path = c.NATIVE_DEFINITIONS / (c.NATIVE_LABEL + 'sync.plist')
    body = path.read_bytes()
    path.write_bytes(body + b'\n')
    with pytest.raises((c.CustodyError, ValueError), match='(definition|generation).*(changed|incomplete)'):
        ex.quiesce_previous_boot(n.selected)
    assert not any(args[:2] == ['/bin/launchctl', 'bootout'] for args in n.driver.sent)
    assert not (c.NATIVE_META / 'source-boot-quiesce.json').exists()
