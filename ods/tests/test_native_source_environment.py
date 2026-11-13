"""Linux/procfs FD harness; Darwin metadata/ACL/DS are explicit simulations.

The imported fixture retains actual Root deployment/runtime/bundle checks. No
account, real ACL, installed state, launchd job, provider or model is touched.
"""
# ruff: noqa: E402 -- explicit harness platform gate precedes POSIX fixture imports.
import copy
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

if not sys.platform.startswith('linux') or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs private FD harness, not physical Darwin qualification', allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_custody import native  # noqa: F401
from test_connection_deployment_paths import deployment  # noqa: F401
from pixel_connections import custody as source


@pytest.fixture
def prepared(native, monkeypatch):  # noqa: F811
    n = native
    template = {'PIXEL_LIMB_EMAIL_ENABLED': '0', 'PIXEL_LIMB_CALENDAR_ENABLED': '0',
        'PIXEL_LIMB_CALENDAR_WRITE_ENABLED': '0', 'PIXEL_LIMB_EMAIL_DRAFTS_ENABLED': '0',
        'PIXEL_LIMB_SOCIAL_ENABLED': '0', 'PIXEL_CALENDAR_DIRECT_ENABLED': '0',
        **{name: '/var/lib/pixel-source-broker/original' for name in source._native_environment_paths()},
        'PIXEL_SOURCE_CALENDAR_ID': 'primary', 'PIXEL_SOURCE_TIME_ZONE': 'UTC',
        'PIXEL_SOURCE_GMAIL_QUERY': 'in:inbox', 'PIXEL_SOURCE_GMAIL_SENT_QUERY': 'in:sent',
        'PIXEL_SOURCE_GMAIL_PAGE_SIZE': '100', 'PYTHONDONTWRITEBYTECODE': '1'}
    body = source.env_text(list(template.items()), {}).encode()
    (n.kit/'source-broker.env').write_bytes(body)
    manifest_path = n.kit/'source-kit.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['source-broker.env'] = hashlib.sha256(body).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    original = n.acl.acl_init
    monkeypatch.setattr(n.acl, 'acl_init', lambda count: n.acl.put({'entries': [], 'flags': 0}) if count == 1 else original(count))
    # The simulated ACL belongs to a live inode, not to every future reuse of
    # its integer. Actual Darwin discards an unlinked inode's ACL after its last
    # FD closes. Mirror ONLY that metadata lifetime for these disposable files.
    real_replace, real_unlink, real_close = os.replace, os.unlink, os.close
    def forget_unlinked(info):
        if info is None or info.st_nlink != 1:
            return
        key = info.st_dev, info.st_ino
        for number in os.listdir('/proc/self/fd'):
            try:
                active = n.real_fstat(int(number))
                if (active.st_dev, active.st_ino) == key:
                    return
            except (OSError, ValueError):
                pass
        n.acl.acls.pop(key, None)
    def old_entry(name, directory=None):
        try:
            return os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return None
    def replace(first, second, **kwargs):
        info = old_entry(second, kwargs.get('dst_dir_fd'))
        result = real_replace(first, second, **kwargs)
        forget_unlinked(info)
        return result
    def unlink(name, **kwargs):
        info = old_entry(name, kwargs.get('dir_fd'))
        result = real_unlink(name, **kwargs)
        forget_unlinked(info)
        return result
    def close(fd):
        info = n.real_fstat(fd)
        result = real_close(fd)
        if info.st_nlink == 0:
            n.acl.acls.pop((info.st_dev, info.st_ino), None)
        return result
    monkeypatch.setattr(os, 'replace', replace)
    monkeypatch.setattr(os, 'unlink', unlink)
    monkeypatch.setattr(os, 'close', close)
    source.native_prepare_state(n.selected, n.identity)
    return n


def content(n, **overrides):
    return source.native_environment_text(n.selected, overrides)


def journal(n):
    return json.loads((n.metadata/'source-environment.json').read_bytes())


def test_render_fixed_paths_and_preauth_preserves_options_without_shell(prepared):
    n = prepared
    text = source.native_environment_text(n.selected,
        {'PIXEL_LIMB_EMAIL_ENABLED': '1', 'PIXEL_SOURCE_CALENDAR_ID': "calendar'quoted"}, preauth=True)
    parsed = dict(source.parse_env(text))
    assert all(parsed[key] == value for key, value in source._native_environment_paths().items())
    assert parsed['PIXEL_LIMB_EMAIL_ENABLED'] == '0'
    assert parsed['PIXEL_SOURCE_CALENDAR_ID'] == "calendar'quoted"
    assert parsed['PIXEL_SOURCE_GMAIL_PAGE_SIZE'] == '100'
    assert not (n.metadata/'source-environment.json').exists()


def test_root_publisher_fd_acl_hash_journal_is_idempotent_and_starts_nothing(prepared, monkeypatch):
    n = prepared
    monkeypatch.setattr(source.subprocess, 'run', lambda *a, **kw: pytest.fail('No actor/launchctl/process is allowed'))
    before = n.acl.writes
    text = content(n)
    result = source.native_publish_environment(n.selected, text)
    assert result['sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert source.NATIVE_ENV.read_text() == text
    assert n.acl.writes == before+1
    saved = journal(n)
    assert saved['pending'] is None and saved['current'] == result
    assert text not in json.dumps(saved) and 'calendar' not in json.dumps(saved)
    with source.NATIVE_ENV.open('rb') as stream:
        source.native_verify_env_fd(stream.fileno(), n.identity['id'])
        assert n.acl.acls[n.acl.key(stream.fileno())]['entries'] == [n.acl.entry(n.identity['id'], mask=2)]
    assert source.native_publish_environment(n.selected, text) == result
    assert n.acl.writes == before+1


@pytest.mark.parametrize('change', ['path', 'loader', 'fixed-option', 'flag', 'duplicate', 'newline', 'oversize'])
def test_invalid_env_refuses_before_journal_or_acl(prepared, change):
    n = prepared
    text = content(n)
    if change == 'path': text = text.replace(str(source.NATIVE_STATE/'private/google-token.json'), '/owner/token.json')
    elif change == 'loader': text += "PYTHONPATH='/owner/code'\n"
    elif change == 'fixed-option': text = text.replace("PIXEL_SOURCE_GMAIL_PAGE_SIZE='100'", "PIXEL_SOURCE_GMAIL_PAGE_SIZE='999'")
    elif change == 'flag': text = text.replace("PIXEL_CALENDAR_DIRECT_ENABLED='0'", "PIXEL_CALENDAR_DIRECT_ENABLED='yes'")
    elif change == 'duplicate': text += "PIXEL_CALENDAR_DIRECT_ENABLED='0'\n"
    elif change == 'newline': text = text.replace("'primary'", "'line\nother'")
    else: text += 'X'*65537
    writes = n.acl.writes
    with pytest.raises(source.CustodyError): source.native_publish_environment(n.selected, text)
    assert not (n.metadata/'source-environment.json').exists()
    assert not source.NATIVE_ENV.exists() and n.acl.writes == writes


def test_existing_unjournaled_env_is_preserved_never_repaired(prepared):
    n = prepared
    source.NATIVE_ENV.write_bytes(b'unknown')
    before = source.NATIVE_ENV.read_bytes()
    writes = n.acl.writes
    with pytest.raises(source.CustodyError): source.native_publish_environment(n.selected, content(n))
    assert source.NATIVE_ENV.read_bytes() == before and n.acl.writes == writes
    assert not (n.metadata/'source-environment.json').exists()


def test_partial_body_retry_appends_only_same_bound_prefix(prepared, monkeypatch):
    n = prepared
    original = source.os.write
    def partial(fd, data):
        original(fd, data[:9])
        raise OSError('invented interrupted body write')
    monkeypatch.setattr(source.os, 'write', partial)
    text = content(n)
    with pytest.raises(OSError): source.native_publish_environment(n.selected, text)
    pending = copy.deepcopy(journal(n)['pending'])
    assert pending['inode'] and not pending['ready'] and not source.NATIVE_ENV.exists()
    candidate = n.metadata/pending['name']
    assert candidate.read_bytes() == text.encode()[:9]
    with pytest.raises(source.CustodyError, match='pending'):
        source.native_publish_environment(n.selected, content(n, PIXEL_LIMB_EMAIL_ENABLED='1'))
    monkeypatch.setattr(source.os, 'write', original)
    result = source.native_publish_environment(n.selected, text)
    assert result['inode'] == pending['inode'] and source.NATIVE_ENV.read_text() == text


@pytest.mark.parametrize('when', ['before', 'after'])
def test_lost_rename_reply_resumes_same_candidate_without_republication(prepared, monkeypatch, when):
    n = prepared
    original = source.os.replace
    env_renames = []
    def interrupted(first, second, **kwargs):
        if second == source.NATIVE_ENV.name:
            env_renames.append(second)
            if when == 'after': original(first, second, **kwargs)
            raise OSError('invented lost rename acknowledgement')
        return original(first, second, **kwargs)
    monkeypatch.setattr(source.os, 'replace', interrupted)
    text = content(n)
    with pytest.raises(OSError): source.native_publish_environment(n.selected, text)
    pending = copy.deepcopy(journal(n)['pending'])
    assert pending['ready'] and pending['inode']
    monkeypatch.setattr(source.os, 'replace', original)
    result = source.native_publish_environment(n.selected, text)
    assert result['inode'] == pending['inode'] and journal(n)['pending'] is None
    assert len(env_renames) == 1


def test_runtime_drift_or_unmanaged_gate_entries_refuse_before_env_writes(prepared):
    n = prepared
    text = content(n)
    (source.NATIVE_STATE/'unknown').write_text('preserve')
    writes = n.acl.writes
    with pytest.raises(source.CustodyError, match='unmanaged'): source.native_publish_environment(n.selected, text)
    assert not source.NATIVE_ENV.exists() and n.acl.writes == writes
    (source.NATIVE_STATE/'unknown').unlink()
    (n.f.root/'runtime/openclaw.mjs').write_bytes(b'changed runtime')
    with pytest.raises(Exception): source.native_publish_environment(n.selected, text)
    assert not (n.metadata/'source-environment.json').exists() and n.acl.writes == writes


def test_unrecorded_acl_writer_has_no_authority(prepared):
    n = prepared
    path = n.metadata/'unrelated'
    path.write_text('preserve')
    before = n.acl.writes
    with path.open('rb') as stream, pytest.raises(source.CustodyError):
        source._native_environment_acl(n.selected, stream.fileno(), n.identity['id'])
    assert n.acl.writes == before and path.read_text() == 'preserve'


def test_legacy_linux_env_writer_is_not_native_custody(prepared, monkeypatch):
    n = prepared
    monkeypatch.setattr(source, 'broker_ids', lambda: (n.identity['id'], n.identity['id']))
    paths = source.NativeSourcePaths(install=source.NATIVE_KIT, state=source.NATIVE_STATE,
                                    env=source.NATIVE_ENV, kit=source.NATIVE_KIT)
    source.install_env(paths, content(n))
    with source.NATIVE_ENV.open('rb') as stream:
        assert (os.fstat(stream.fileno()).st_mode & 0o777) == 0o640
        with pytest.raises(source.CustodyError, match='env-unsafe'):
            source.native_verify_env_fd(stream.fileno(), n.identity['id'])
    assert not (n.metadata/'source-environment.json').exists()


@pytest.mark.parametrize('drift', ['inode', 'bytes', 'acl'])
def test_pending_candidate_drift_is_preserved_not_adopted_or_acl_repaired(prepared, monkeypatch, drift):
    n = prepared
    text = content(n)
    n.acl.fail_write = True
    with pytest.raises(source.CustodyError, match='acl-write-failed'):
        source.native_publish_environment(n.selected, text)
    n.acl.fail_write = False
    candidate = n.metadata/journal(n)['pending']['name']
    if drift == 'inode':
        candidate.rename(candidate.with_name('preserved-original'))
        candidate.write_text(text)
        candidate.chmod(0o600)
    elif drift == 'bytes':
        candidate.write_text('unrelated preserved body')
    else:
        with candidate.open('rb') as stream:
            n.acl.acls[n.acl.key(stream.fileno())] = {'entries':[n.acl.entry(os.getuid(), mask=2)]}
    before, writes = candidate.read_bytes(), n.acl.writes
    with pytest.raises(source.CustodyError): source.native_publish_environment(n.selected, text)
    assert candidate.read_bytes() == before and n.acl.writes == writes
    assert not source.NATIVE_ENV.exists()


def test_update_failure_keeps_previous_env_and_same_pending_is_resumable(prepared, monkeypatch):
    n = prepared
    old_text = content(n)
    old = source.native_publish_environment(n.selected, old_text)
    new_text = content(n, PIXEL_LIMB_EMAIL_ENABLED='1')
    original = source.os.replace
    def refuse_env(first, second, **kwargs):
        if second == source.NATIVE_ENV.name: raise OSError('invented before replacement')
        return original(first, second, **kwargs)
    monkeypatch.setattr(source.os, 'replace', refuse_env)
    with pytest.raises(OSError): source.native_publish_environment(n.selected, new_text)
    assert source.NATIVE_ENV.read_text() == old_text and journal(n)['current'] == old
    with pytest.raises(source.CustodyError, match='pending'): source.native_publish_environment(n.selected, old_text)
    monkeypatch.setattr(source.os, 'replace', original)
    new = source.native_publish_environment(n.selected, new_text)
    assert new['inode'] != old['inode'] and source.NATIVE_ENV.read_text() == new_text


def test_valid_empty_acl_is_only_a_private_recorded_creation_prefix(prepared, monkeypatch):
    n = prepared
    original = source._native_environment_acl
    def empty_prefix(selection, fd, uid):
        n.acl.acls[n.acl.key(fd)] = {'entries': [], 'flags': 0}
        return original(selection, fd, uid)
    monkeypatch.setattr(source, '_native_environment_acl', empty_prefix)
    result = source.native_publish_environment(n.selected, content(n))
    assert journal(n)['current'] == result
    with source.NATIVE_ENV.open('rb') as stream:
        assert n.acl.acls[n.acl.key(stream.fileno())]['entries'] == [n.acl.entry(n.identity['id'], mask=2)]
