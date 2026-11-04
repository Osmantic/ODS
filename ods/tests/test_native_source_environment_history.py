"""Real owned Linux files; the Darwin custody/process boundary is simulated.

Sealed old history is retained as data, never read as current invocation or
actor-absence evidence. A large history must not block a normal generation.
"""
import json
import os
from pathlib import Path
import sys

import pytest

if sys.platform != 'linux':
    pytest.skip('Linux owned-file harness, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_environment_generation import (  # noqa: E402,F401
    c, deployment, executor, generation, helper, material, native, package,
    publication, qualification_operation, source, built, stopped, target,
)


def test_large_sealed_history_is_retained_without_traversal(executor, monkeypatch):
    n = executor
    stopped(n)
    active = json.loads((n.metadata / 'source-jobs-active.json').read_bytes())
    assert active['history']['directory'] is not None
    history = n.metadata / 'native-source-jobs/history'
    inode = [history.stat().st_dev, history.stat().st_ino]
    # More descendants than the old 4096-entry whole-tree limit. A deep opaque
    # branch also exceeds the former depth bound; neither is live authority.
    deep = history
    for index in range(10):
        deep = deep / ('sealed-' + str(index))
        deep.mkdir(mode=0o700)
    for index in range(4100):
        fd = os.open(deep / (str(index) + '.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    original_listdir = os.listdir
    history_fd_inodes = set()
    def listdir(path):
        info = os.fstat(path) if isinstance(path, int) else os.stat(path, follow_symlinks=False)
        if [info.st_dev, info.st_ino] == inode:
            history_fd_inodes.add(tuple(inode))
            pytest.fail('sealed history descendants are not consulted')
        return original_listdir(path)
    monkeypatch.setattr(os, 'listdir', listdir)
    result = helper.publish_environment_generation(n.selected, target(n))
    record = json.loads((n.metadata / 'source-environment-generation.json').read_bytes())
    assert record['schemaVersion'] == 2 and 'treeSha256' not in record['old']
    assert record['old']['jobs']['history'] == {
        'directory': inode, 'count': active['history']['count']}
    retained = n.metadata / helper._environment_archive_name(record) / 'native-source-jobs/history'
    assert [retained.stat().st_dev, retained.stat().st_ino] == inode
    assert (retained / deep.relative_to(history) / '4099.json').is_file()
    assert not history_fd_inodes and result['status'] == 'inactive'
    assert helper.publish_environment_generation(n.selected, target(n)) == result
