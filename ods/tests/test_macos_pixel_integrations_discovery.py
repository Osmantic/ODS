"""Real Coffee hint producer and read-only native FD validation, without launchd."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

if sys.platform == 'win32':
    pytest.skip('Unix owner-file descriptors', allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'extensions/services/pixel-agent/host'))
sys.path.insert(0, str(ROOT / 'bin'))
import integrations_service as producer  # noqa: E402
import pixel_macos_custody as custody  # noqa: E402

SPEC = importlib.util.spec_from_file_location('discovery_native_integrations',
    ROOT / 'installers/macos/lib/pixel-native-integrations-service.py')
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


@pytest.fixture
def hint(monkeypatch):
    # The real publisher refuses shared writable ancestors such as /tmp.
    with tempfile.TemporaryDirectory(prefix='.discovery-qa-', dir=ROOT.parent) as name:
        root = Path(name).resolve()
        runtime = root / 'runtime'
        runtime.mkdir(mode=0o700)
        os.chown(runtime, os.getuid(), os.getgid())
        app = producer.Integrations(producer.Store(root / 'state'))
        app.discovery_path = runtime / 'discovery.json'
        entry = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        monkeypatch.setattr(native, 'RUNTIME', runtime)
        # Darwin uses the actual ACL checker. Linux has no equivalent API;
        # only that observation is adapted, never FD reads or metadata.
        acl = custody if sys.platform == 'darwin' else SimpleNamespace(_require_no_acl=lambda _fd: None)
        monkeypatch.setattr(producer, 'McpSession', lambda *_a, **_kw: pytest.fail('provider I/O'))
        try:
            yield SimpleNamespace(app=app, path=app.discovery_path, runtime=runtime, entry=entry, custody=acl)
        finally:
            app.local.close()


def publish(hint, *, catalog=False, expired=False, revision=0):
    config = hint.app.store.config()
    config['revision'] = revision
    if catalog:
        server = dict(id='files', name='Arquivos QA', kind='http', enabled=True,
            url='http://127.0.0.1:1/invented', workingFolder='', envNames=[],
            tools=[dict(name='read_file', policy='allow'), dict(name='write_file', policy='ask'),
                   dict(name='disabled', policy='off')])
        server['checkedBinding'] = producer.digest(producer.server_context(server, hint.app.store.secrets()))
        config['mcp']['servers'] = [server]
    hint.app.store.save_config(config)
    with patch.object(producer, 'now', (lambda: 1) if expired else producer.now):
        assert hint.app.publish_discovery() is True
    return json.loads(hint.path.read_bytes())


def image(path):
    info = path.lstat()
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns, path.read_bytes())


@pytest.mark.parametrize('catalog,expired,revision', [(False, False, 0), (True, False, 1),
    (True, True, 17), (False, True, 2 ** 53 + 1)])
def test_actual_producer_snapshot_is_preserved_without_requiring_live_freshness(hint, catalog, expired, revision):
    value = publish(hint, catalog=catalog, expired=expired, revision=revision)
    if catalog:
        assert value['servers'] == [dict(id='files', name='Arquivos QA',
            tools=[dict(name='read_file', ask=False), dict(name='write_file', ask=True)])]
    else:
        assert value['servers'] == []
    before = image(hint.path)
    native._validate_runtime(hint.entry, hint.custody)
    assert image(hint.path) == before
    assert set(hint.runtime.iterdir()) == {hint.path}


@pytest.mark.parametrize('servers,tools', [(20, 1), (1, 200)])
def test_actual_producer_catalog_at_supported_limits_remains_accepted(hint, servers, tools):
    publish(hint, catalog=True)
    config = hint.app.store.config()
    template = config['mcp']['servers'][0]
    config['mcp']['servers'] = []
    for index in range(servers):
        server = dict(template, id='server-' + str(index),
            tools=[dict(name='t' + str(i), policy='ask') for i in range(tools)])
        server['checkedBinding'] = producer.digest(producer.server_context(server, hint.app.store.secrets()))
        config['mcp']['servers'].append(server)
    hint.app.store.save_config(config)
    assert hint.app.publish_discovery() is True
    value = json.loads(hint.path.read_bytes())
    assert len(value['servers']) == servers and all(len(server['tools']) == tools for server in value['servers'])
    before = image(hint.path)
    native._validate_runtime(hint.entry, hint.custody)
    assert image(hint.path) == before


@pytest.mark.parametrize('fault', ['symlink', 'hardlink', 'fifo', 'directory', 'mode', 'uid', 'gid',
    'acl', 'oversize', 'empty', 'unknown-child', 'temporary-child'])
def test_unknown_or_unsafe_hint_is_refused_and_preserved(hint, monkeypatch, fault):
    publish(hint)
    if fault in ('symlink', 'fifo', 'directory'):
        hint.path.rename(hint.runtime.parent / 'retained.json')
        if fault == 'symlink': hint.path.symlink_to(hint.runtime.parent / 'retained.json')
        elif fault == 'fifo': os.mkfifo(hint.path, 0o600)
        else: hint.path.mkdir(mode=0o700)
    elif fault == 'hardlink': os.link(hint.path, hint.runtime.parent / 'alias')
    elif fault == 'mode': hint.path.chmod(0o640)
    elif fault in ('uid', 'gid'):
        # An unprivileged fixture cannot assign another user's ownership.
        # Adapt only this child's observed identity, keeping actual FD reads.
        original, selected = os.fstat, hint.path.lstat().st_ino
        def foreign(fd):
            info = original(fd)
            if info.st_ino != selected: return info
            fields = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
            fields['st_' + fault] += 1
            return SimpleNamespace(**fields)
        monkeypatch.setattr(native.os, 'fstat', foreign)
    elif fault == 'acl':
        original = hint.custody._require_no_acl
        def reject(fd):
            original(fd)
            if os.fstat(fd).st_ino == hint.path.lstat().st_ino: raise ValueError('invented ACL observation')
        monkeypatch.setattr(hint.custody, '_require_no_acl', reject)
    elif fault == 'oversize': hint.path.write_bytes(b' ' * 8193)
    elif fault == 'empty': hint.path.write_bytes(b'')
    else: (hint.runtime / ('.discovery-interrupted' if fault == 'temporary-child' else 'foreign')).write_bytes(b'preserve')
    before = {p.name: (p.lstat().st_ino, p.lstat().st_mode) for p in hint.runtime.iterdir()}
    with pytest.raises((ValueError, OSError)):
        native._validate_runtime(hint.entry, hint.custody)
    assert before == {p.name: (p.lstat().st_ino, p.lstat().st_mode) for p in hint.runtime.iterdir()}


@pytest.mark.parametrize('fault', ['json', 'utf8', 'duplicate-key', 'duplicate-nested-key', 'extra', 'version-bool', 'revision-bool',
    'revision-negative', 'valid-float', 'truncated-int', 'servers-object', 'server-extra', 'server-id',
    'server-name', 'server-duplicate', 'server-count', 'tool-extra', 'tool-name', 'tool-ask', 'tool-duplicate', 'tool-count'])
def test_closed_producer_format_and_limits_are_enforced(hint, fault):
    value = publish(hint, catalog=True)
    server = value['servers'][0]
    if fault == 'json': raw = b'not JSON'
    elif fault == 'utf8': raw = b'\xff'
    elif fault == 'duplicate-key': raw = json.dumps(value).replace('"version": 1', '"version": 1, "version": 1').encode()
    elif fault == 'duplicate-nested-key': raw = json.dumps(value).replace('"ask": false', '"ask": false, "ask": true').encode()
    else:
        if fault == 'extra': value['endpoint'] = 'invented'
        elif fault == 'version-bool': value['version'] = True
        elif fault == 'revision-bool': value['revision'] = False
        elif fault == 'revision-negative': value['revision'] = -1
        elif fault == 'valid-float': value['validUntil'] = 2.0
        elif fault == 'truncated-int': value['truncated'] = 0
        elif fault == 'servers-object': value['servers'] = {}
        elif fault == 'server-extra': server['url'] = 'invented'
        elif fault == 'server-id': server['id'] = '../foreign'
        elif fault == 'server-name': server['name'] = '/private/invented'
        elif fault == 'server-duplicate': value['servers'].append(copy.deepcopy(server))
        elif fault == 'server-count': value['servers'] = [dict(server, id='server-' + str(i)) for i in range(21)]
        elif fault == 'tool-extra': server['tools'][0]['arguments'] = {}
        elif fault == 'tool-name': server['tools'][0]['name'] = 'bad/name'
        elif fault == 'tool-ask': server['tools'][0]['ask'] = 1
        elif fault == 'tool-duplicate': server['tools'].append(copy.deepcopy(server['tools'][0]))
        else: server['tools'] = [dict(name='t' + str(i), ask=True) for i in range(201)]
        raw = json.dumps(value, separators=(',', ':')).encode()
        assert len(raw) <= 8192  # Exercise the shape limit, not just the byte bound.
    hint.path.write_bytes(raw)
    before = image(hint.path)
    with pytest.raises(ValueError, match='unsafe-native-integrations-discovery'):
        native._validate_runtime(hint.entry, hint.custody)
    assert image(hint.path) == before


@pytest.mark.parametrize('replacement', [False, True])
def test_fd_or_named_inode_change_during_read_is_refused(hint, monkeypatch, replacement):
    publish(hint)
    original, calls = os.fstat, []
    selected = hint.path.lstat().st_ino
    def changed(fd):
        info = original(fd)
        if info.st_ino == selected:
            calls.append(True)
            if len(calls) == 2:
                if replacement:
                    other = hint.runtime.parent / 'replacement'
                    other.write_bytes(hint.path.read_bytes())
                    other.chmod(0o600)
                    os.replace(other, hint.path)
                else:
                    hint.path.write_bytes(hint.path.read_bytes() + b' ')
                info = original(fd)
        return info
    monkeypatch.setattr(native.os, 'fstat', changed)
    with pytest.raises(ValueError, match='unsafe-native-integrations-discovery'):
        native._validate_runtime(hint.entry, hint.custody)
    assert len(calls) >= 2 and hint.path.exists()
