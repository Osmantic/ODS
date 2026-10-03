import importlib.util
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("unix_peer", Path(__file__).resolve().parents[1] / "host/unix_peer.py")
peer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(peer)


class PeerIdentityTests(unittest.TestCase):
    def test_connected_client_and_server_credentials(self):
        with tempfile.TemporaryDirectory(prefix="peer-", dir="/tmp") as directory:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(Path(directory) / "socket"))
                server.listen(1)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(server.getsockname())
                    accepted, _ = server.accept()
                    with accepted:
                        self.assertEqual(peer.peer_ids(accepted), (os.geteuid(), os.getegid()))
                        self.assertEqual(peer.peer_ids(client), (os.geteuid(), os.getegid()))
                        with patch.object(peer.sys, "platform", "unsupported"):
                            with self.assertRaises(OSError):
                                peer.peer_ids(accepted)

    def test_rejects_non_local_streams(self):
        for family, kind in ((socket.AF_INET, socket.SOCK_STREAM), (socket.AF_UNIX, socket.SOCK_DGRAM)):
            with socket.socket(family, kind) as connection:
                with self.assertRaises(OSError):
                    peer.peer_ids(connection)

    def test_rejects_unconnected_and_closed_sockets(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            with self.assertRaises(OSError):
                peer.peer_ids(connection)
        with self.assertRaises(OSError):
            peer.peer_ids(connection)


if __name__ == "__main__":
    unittest.main()
