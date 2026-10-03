"""Untrusted bridge peers cannot wait in the trusted connection admission queue."""
import importlib.util
import socket
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "bin/ods-macos-llm-bridge.py"
SPEC = importlib.util.spec_from_file_location("bridge_denied_admission", SOURCE)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


@pytest.mark.parametrize("peer,allowlist", [
    ("192.0.2.2", ()),
    ("192.0.2.3", ("192.0.2.2",)),
    ("198.51.100.2", ("192.0.2.0/24",)),
])
def test_denied_peer_never_enters_connection_admission(peer, allowlist):
    class PeerFixture(bridge.LlmBridgeServer):
        def get_request(self):
            request, address = super().get_request()
            # Use a documentation-only peer identity with a real local socket.
            return request, (peer, address[1])

    proxy = PeerFixture(("127.0.0.1", 0), ("127.0.0.1", 1), allowlist)
    acquire = Mock(side_effect=AssertionError("Denied client reached admission"))
    proxy._connection_slots.acquire = acquire
    thread = threading.Thread(target=proxy.handle_request)
    thread.start()
    try:
        with socket.create_connection(proxy.server_address, timeout=2) as client:
            client.settimeout(2)
            assert client.recv(1) == b""
        thread.join(timeout=2)
        assert not thread.is_alive()
        acquire.assert_not_called()
    finally:
        proxy.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("peer,allowlist", [
    ("127.0.0.1", ()),
    ("::1", ()),
    ("192.0.2.2", ("192.0.2.2",)),
    ("192.0.2.3", ("192.0.2.0/24",)),
])
def test_authorized_peer_passes_dispatch_check(peer, allowlist):
    with bridge.LlmBridgeServer(("127.0.0.1", 0), ("127.0.0.1", 1), allowlist) as proxy:
        assert proxy.verify_request(None, (peer, 4321)) is True
