"""Disposable Linux/procfs FDs; explicit Darwin metadata/ACL/DS simulation."""
# ruff: noqa: E402
import copy
import json
import os
from pathlib import Path
import sys

import pytest

if not sys.platform.startswith('linux') or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs private FD harness, not a physical Darwin gate', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_connection_deployment_paths import deployment  # noqa: F401
from test_native_source_custody import native  # noqa: F401
from test_native_source_environment import prepared  # noqa: F401
from pixel_connections import custody as source


@pytest.fixture
def logs(prepared, monkeypatch):  # noqa: F811
    monkeypatch.setattr(source, 'NATIVE_LOGS', prepared.world/'logs')
    return prepared


def read_intent(n):
    return json.loads((n.metadata/'source-logs-prepare.json').read_bytes())


def test_exact_logs_have_only_broker_traverse_and_no_owner_or_group_acl(logs, monkeypatch):
    n = logs
    monkeypatch.setattr(source.subprocess, 'run', lambda *a, **kw: pytest.fail('No job/process/account is allowed'))
    before = n.acl.writes
    result = source.native_prepare_logs(n.selected)
    assert set(os.listdir(source.NATIVE_LOGS)) == set(source._native_log_names())
    assert len(result['files']) == 12
    fd = os.open(source.NATIVE_LOGS, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (0, 0, 0o700)
        assert n.acl.acls[n.acl.key(fd)]['entries'] == [n.acl.entry(n.identity['id'])]
        assert source.native_verify_log_directory(fd, n.identity['id']) == result['directory']
    finally:
        os.close(fd)
    for name in source._native_log_names():
        with (source.NATIVE_LOGS/name).open('rb') as stream:
            info = os.fstat(stream.fileno())
            assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (60001, 60001, 0o600)
            source._native_no_acl(stream.fileno())
    assert n.acl.writes == before+1
    assert source.native_prepare_logs(n.selected) == result
    assert n.acl.writes == before+1


def test_log_root_intent_is_durable_before_first_directory_creation(logs, monkeypatch):
    n = logs
    original = source.os.mkdir
    observed = []
    def create(name, **kwargs):
        if name == source.NATIVE_LOGS.name:
            intent = read_intent(n)
            assert intent['directory'] is None and intent['files'] == {}
            observed.append(True)
        return original(name, **kwargs)
    monkeypatch.setattr(source.os, 'mkdir', create)
    source.native_prepare_logs(n.selected)
    assert observed == [True]


def test_existing_unreceipted_log_root_is_never_adopted(logs):
    n = logs
    source.NATIVE_LOGS.mkdir(mode=0o700)
    marker = source.NATIVE_LOGS/'private-owner-data'
    marker.write_text('preserve')
    before = n.acl.writes
    with pytest.raises(source.CustodyError, match='unmanaged'): source.native_prepare_logs(n.selected)
    assert marker.read_text() == 'preserve' and n.acl.writes == before
    assert not (n.metadata/'source-logs-prepare.json').exists()


def test_failed_acl_publication_keeps_all_files_private_and_reuses_inodes(logs):
    n = logs
    n.acl.fail_write = True
    with pytest.raises(source.CustodyError, match='acl-write-failed'): source.native_prepare_logs(n.selected)
    before = copy.deepcopy(read_intent(n))
    assert len(before['files']) == 12 and all(item['ready'] for item in before['files'].values())
    fd = os.open(source.NATIVE_LOGS, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        assert source._native_acl_entries(fd, missing=True) == ()
    finally:
        os.close(fd)
    n.acl.fail_write = False
    result = source.native_prepare_logs(n.selected)
    assert result['directory'] == before['directory']
    assert result['files'] == {key:item['inode'] for key,item in before['files'].items()}


@pytest.mark.parametrize('drift', ['extra-file', 'file-mode', 'file-link', 'owner-acl', 'write-acl'])
def test_unknown_or_unsafe_log_prefix_does_not_open_the_directory(logs, drift):
    n = logs
    n.acl.fail_write = True
    with pytest.raises(source.CustodyError): source.native_prepare_logs(n.selected)
    n.acl.fail_write = False
    first = source.NATIVE_LOGS/source._native_log_names()[0]
    if drift == 'extra-file': (source.NATIVE_LOGS/'unrecognized').write_text('preserve')
    elif drift == 'file-mode': first.chmod(0o640)
    elif drift == 'file-link': os.link(first, source.NATIVE_LOGS/'second-link')
    else:
        fd = os.open(source.NATIVE_LOGS, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            n.acl.acls[n.acl.key(fd)] = {'entries':[n.acl.entry(os.getuid() if drift == 'owner-acl' else 60001,
                                                                            mask=10 if drift == 'owner-acl' else 12)]}
        finally:
            os.close(fd)
    writes = n.acl.writes
    with pytest.raises(source.CustodyError): source.native_prepare_logs(n.selected)
    assert n.acl.writes == writes
