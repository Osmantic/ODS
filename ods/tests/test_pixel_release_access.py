"""Release adapter preserves client custody and rejects unbound proof replies."""
import importlib.util
import json
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

PATH = Path(__file__).resolve().parents[1] / 'vendor/pixel/scripts/lib/ods-release-access.py'
spec = importlib.util.spec_from_file_location('release_access', PATH)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
A, B = 'a' * 64, 'b' * 64


@pytest.fixture
def transport(monkeypatch):
    def info(path):
        mode = stat.S_IFREG if path == adapter.CLIENT else stat.S_IFDIR
        return SimpleNamespace(st_mode=mode | 0o755, st_uid=0, st_nlink=1)
    monkeypatch.setattr(Path, 'lstat', info)
    call = Mock(return_value=json.dumps({'configSha256': B}).encode())
    monkeypatch.setattr(adapter.subprocess, 'check_output', call)
    return call


def test_preparation_uses_isolated_installed_client(transport, capsys):
    transport.return_value = json.dumps({'afterSha': B, 'beforeSha': A}).encode()
    adapter.main(['prepare', A, '/reviewed/openclaw.json', B])
    assert capsys.readouterr().out == A + ' ' + B + '\n'
    argv = transport.call_args.args[0]
    assert argv[1:] == ['-I', str(adapter.CLIENT), 'release-prepare', '--transaction', A,
                        '--candidate', '/reviewed/openclaw.json', '--sha256', B]
    assert transport.call_args.kwargs['timeout'] == 340


@pytest.mark.parametrize('mode,uid,links', [(stat.S_IFLNK | 0o777, 0, 1),
    (stat.S_IFREG | 0o775, 0, 1), (stat.S_IFREG | 0o755, 1000, 1),
    (stat.S_IFREG | 0o755, 0, 2)])
def test_unsafe_client_is_not_executed(transport, monkeypatch, mode, uid, links):
    monkeypatch.setattr(Path, 'lstat', lambda _: SimpleNamespace(st_mode=mode, st_uid=uid, st_nlink=links))
    with pytest.raises(ValueError, match='custody'):
        adapter.invoke('publish', A, 'apply')
    transport.assert_not_called()


@pytest.mark.parametrize('raw', [b'null', b'{}', b'x' * 4097,
    json.dumps({'configSha256': B, 'extra': True}).encode(),
    json.dumps({'configSha256': A}).encode()])
def test_completion_requires_exact_proof(transport, raw):
    transport.return_value = raw
    with pytest.raises(ValueError):
        adapter.invoke('finish', A, B, 'apply')


@pytest.mark.parametrize('outcome', ['apply', 'rollback'])
def test_publication_and_completion_preserve_outcome(transport, outcome):
    assert adapter.invoke('publish', A, outcome) == {'configSha256': B}
    assert transport.call_args.args[0][-1] == outcome
    assert adapter.invoke('finish', A, B, outcome) == {'configSha256': B}
    assert transport.call_args.args[0][-3:] == ['--sha256', B, outcome]
