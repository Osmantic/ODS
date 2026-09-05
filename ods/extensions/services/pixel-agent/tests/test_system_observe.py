import importlib.util
import socket
import pathlib
import stat
import tempfile
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).parents[1] / "host" / "system_observe.py"
SPEC = importlib.util.spec_from_file_location("system_observe", MODULE_PATH)
assert SPEC and SPEC.loader
system_observe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(system_observe)


class SystemObserveTests(unittest.TestCase):
    def test_gpu_output_is_bounded_and_omits_device_identifiers(self):
        result = mock.Mock(
            returncode=0,
            stdout="NVIDIA GeForce RTX 5070 Laptop GPU, 8151, 573.22\n",
            stderr="",
        )
        with mock.patch.object(system_observe, "_trusted_executable", return_value="/usr/bin/nvidia-smi") as trusted, \
             mock.patch.object(system_observe, "_run", return_value=result):
            value = system_observe.observe_gpu()
        trusted.assert_called_once_with([
            "/usr/lib/wsl/lib/nvidia-smi",
            "/usr/bin/nvidia-smi",
            "/usr/local/bin/nvidia-smi",
        ])
        self.assertEqual(
            value,
            {
                "schemaVersion": 1,
                "kind": "ods-host-gpu",
                "available": True,
                "backend": "nvidia",
                "devices": [{
                    "name": "NVIDIA GeForce RTX 5070 Laptop GPU",
                    "memoryMiB": 8151,
                    "driver": "573.22",
                }],
            },
        )
        self.assertNotIn("uuid", str(value).lower())
        self.assertNotIn("serial", str(value).lower())

    def test_gpu_parser_fails_closed_on_unexpected_output(self):
        result = mock.Mock(returncode=0, stdout="GPU, 123, driver, extra\n", stderr="")
        with mock.patch.object(system_observe, "_trusted_executable", return_value="/usr/bin/nvidia-smi"), \
             mock.patch.object(system_observe, "_run", return_value=result):
            value = system_observe.observe_gpu()
        self.assertFalse(value["available"])
        self.assertEqual(value["devices"], [])

    def test_tailscale_projection_contains_no_peer_or_address_data(self):
        with mock.patch.object(
            system_observe,
            "_native_tailscale_state",
            return_value=(True, "running", True),
        ):
            value = system_observe.observe_tailscale()
        self.assertEqual(
            value,
            {
                "schemaVersion": 1,
                "kind": "ods-host-tailscale",
                "available": True,
                "state": "running",
                "serviceRunning": True,
            },
        )
        rendered = str(value).lower()
        self.assertNotIn("peer", rendered)
        self.assertNotIn("address", rendered)
        self.assertNotIn("account", rendered)

    def test_windows_tailscale_uses_only_a_sanitized_service_state(self):
        result = mock.Mock(returncode=0, stdout="Running\n", stderr="#< CLIXML\n")
        with mock.patch.object(system_observe, "_native_tailscale_state", return_value=None), \
             mock.patch.object(system_observe, "_windows_powershell_result", return_value=result) as run:
            value = system_observe.observe_tailscale()
        command = run.call_args.args[0]
        self.assertIn("Get-Service -Name Tailscale", command)
        self.assertNotIn("status --json", command)
        self.assertEqual(value["state"], "service-running")
        self.assertTrue(value["serviceRunning"])

    def test_windows_interop_retries_only_root_owned_socket_candidates(self):
        failed = mock.Mock(returncode=1, stdout="", stderr="failed")
        succeeded = mock.Mock(returncode=0, stdout="Running\n", stderr="")
        candidates = [pathlib.Path("/run/WSL/101_interop"), pathlib.Path("/run/WSL/99_interop")]
        with tempfile.TemporaryDirectory() as directory:
            executable = pathlib.Path(directory) / "powershell.exe"
            executable.write_text("fixture", encoding="utf-8")
            executable.chmod(0o555)
            with mock.patch.object(system_observe, "WINDOWS_POWERSHELL", executable), \
                 mock.patch.object(system_observe, "_trusted_interop_sockets", return_value=candidates), \
                 mock.patch.object(system_observe, "_run", side_effect=[failed, succeeded]) as run:
                result = system_observe._windows_powershell_result("'safe'")
        self.assertIs(result, succeeded)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].kwargs["extra_env"], {"WSL_INTEROP": str(candidates[0])})
        self.assertEqual(run.call_args_list[1].kwargs["extra_env"], {"WSL_INTEROP": str(candidates[1])})

    def test_interop_directory_must_be_root_owned_and_not_writable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            with mock.patch.object(pathlib.Path, "lstat", return_value=mock.Mock(st_mode=stat.S_IFDIR | 0o777, st_uid=0)):
                self.assertEqual(system_observe._trusted_interop_sockets(root), [])

    def test_private_network_peer_reports_only_bounded_exact_peer_evidence(self):
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.166", 0)),
        ]
        tailscale = {
            "available": True,
            "found": False,
            "online": None,
            "addresses": [],
        }
        with mock.patch.object(system_observe.socket, "getaddrinfo", return_value=records), \
             mock.patch.object(system_observe, "_tailscale_peer_status", return_value=tailscale), \
             mock.patch.object(system_observe, "_probe_icmp", return_value=False), \
             mock.patch.object(
                 system_observe,
                 "_probe_tcp",
                 side_effect=lambda _family, _address, port: port == 22,
             ):
            value = system_observe.observe_network_peer("Strixy", "22,3389")
        self.assertEqual(value["target"], "Strixy")
        self.assertEqual(value["ports"], [22, 3389])
        self.assertTrue(value["resolved"])
        self.assertTrue(value["reachable"])
        self.assertEqual(value["tailscale"], tailscale)
        self.assertEqual(value["addresses"], [{
            "address": "192.168.0.166",
            "family": "ipv4",
            "scope": "lan",
            "icmpReachable": False,
            "tcp": [{"port": 22, "open": True}, {"port": 3389, "open": False}],
        }])

    def test_network_peer_rejects_public_resolution_and_unbounded_ports(self):
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", 0)),
        ]
        with mock.patch.object(system_observe.socket, "getaddrinfo", return_value=records), \
             self.assertRaisesRegex(ValueError, "private network boundary"):
            system_observe.observe_network_peer("public.example", "443")
        for ports in ("0", "65536", "22,22", "1,2,3,4,5,6,7,8,9"):
            with self.subTest(ports=ports), self.assertRaises(ValueError):
                system_observe._normalized_peer_ports(ports)

    def test_network_peer_adds_only_the_exact_tailscale_peer(self):
        status = {
            "Peer": {
                "one": {
                    "HostName": "Strixy",
                    "DNSName": "strixy.example.ts.net.",
                    "Online": True,
                    "TailscaleIPs": ["100.100.20.30", "fd7a:115c:a1e0::1234"],
                },
                "other": {
                    "HostName": "unrelated",
                    "Online": True,
                    "TailscaleIPs": ["100.90.1.2"],
                },
            },
        }
        with mock.patch.object(system_observe, "_tailscale_status_json", return_value=status):
            value = system_observe._tailscale_peer_status("Strixy")
        self.assertEqual(value, {
            "available": True,
            "found": True,
            "online": True,
            "addresses": ["100.100.20.30", "fd7a:115c:a1e0::1234"],
        })
        self.assertNotIn("unrelated", str(value))


if __name__ == "__main__":
    unittest.main()
