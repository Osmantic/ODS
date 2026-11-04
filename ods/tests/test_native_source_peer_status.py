"""Native read-only client peer boundary; no platform activation or Mac claim."""
import io
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ODS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ODS / 'bin'))
sys.path.insert(0, str(ODS / 'extensions/services/pixel-agent/host'))
import pixel_access_client as client  # noqa: E402
import unix_peer  # noqa: E402


class Connection:
    def __init__(self):
        self.sent = []
        self.timeout = None
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        pass
    def connect(self, address):
        self.address = address
    def settimeout(self, seconds):
        self.timeout = seconds
    def sendall(self, raw):
        self.sent.append(raw)
    def makefile(self, *_args):
        return io.BytesIO(b'{"status":200,"body":{}}\n')


def test_native_read_uses_kernel_reader_before_sending_fixed_request():
    connection = Connection()
    def peer(selected):
        assert selected is connection and connection.sent == []
        return 0, 0
    with patch.object(client.sys, 'platform', 'darwin'), \
            patch.object(client.socket, 'socket', return_value=connection), \
            patch.object(unix_peer, 'peer_ids', side_effect=peer) as reader:
        assert client.request_access('connection-agent-status') == (200, {})
    reader.assert_called_once_with(connection)
    assert connection.timeout == 20
    assert connection.sent == [b'{"operation": "connection-agent-status"}\n']


@pytest.mark.parametrize('failure', [1000, OSError('unavailable'), ValueError('not connected')])
def test_native_unknown_or_nonroot_peer_receives_no_request(failure):
    connection = Connection()
    def peer(_selected):
        if isinstance(failure, Exception):
            raise failure
        return failure, 0
    with patch.object(client.sys, 'platform', 'darwin'), \
            patch.object(client.socket, 'socket', return_value=connection), \
            patch.object(unix_peer, 'peer_ids', side_effect=peer), pytest.raises(ValueError):
        client.request_access('connection-agent-status')
    assert connection.sent == []


def test_native_read_support_does_not_enable_installer_protocol():
    connection = Connection()
    with patch.object(client.sys, 'platform', 'darwin'), \
            patch.object(client.socket, 'socket', return_value=connection), \
            patch.object(unix_peer, 'peer_ids', side_effect=AssertionError('installer must not use this path')), \
            pytest.raises(ValueError, match='Linux peer credentials'):
        client.request_access('installer-source-begin')
    assert connection.sent == []
