"""Retirement history after the ordinary first prepare and pending-boot repair."""
# ruff: noqa: E402,F401,F811
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs Apple metadata harness, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_initial_boot import (initial, material, native, deployment, built, source,
    package, publication, helper, qualification_operation, pending)
from pixel_connections import custody as c, native_retirement as retirement
import pixel_macos_custody as mac


@pytest.mark.parametrize('rebooted', [False, True])
def test_normal_first_generation_history_is_explicit_and_preserved(initial, monkeypatch, rebooted):
    n = initial
    parent = n.world / 'public-records'
    parent.mkdir(mode=0o755)
    monkeypatch.setattr(c, 'NATIVE_CONNECTION_RECORD', parent / 'portal-connections.json')
    original = mac.protected_directory
    @contextmanager
    def directory(path, **kwargs):
        if Path(path) == parent:
            assert not kwargs
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: yield fd
            finally: os.close(fd)
        else:
            with original(path, **kwargs) as fd: yield fd
    monkeypatch.setattr(mac, 'protected_directory', directory)
    if rebooted: pending(n, monkeypatch, 'complete-child')
    n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    initial_record = json.loads((n.metadata / 'source-initial-generation.json').read_bytes())
    assert initial_record['phase'] == 'committed'
    inventory = c.native_retirement_inventory(n.selected)
    before = {path: path.stat().st_ino for path in n.metadata.iterdir()}
    result = retirement.add_history(n.selected, inventory)
    assert '.source-initial-publication-' + initial_record['nonce'] + '.json' in result['history']['metadata']
    if rebooted:
        ledger = json.loads((n.metadata / 'source-initial-boot.json').read_bytes())
        assert ledger['transactions'][-1]['phase'] == 'committed'
        for item in ledger['transactions']:
            names = helper._initial_boot_names(item)
            assert names['parent_before'] in result['history']['metadata']
            if item['child'] is not None: assert names['child_before'] in result['history']['metadata']
    assert {path: path.stat().st_ino for path in before} == before
    assert not (c.NATIVE_STATE / 'private/google-token.json').exists()
    assert retirement.verify_history(n.selected, result) == result['history']
