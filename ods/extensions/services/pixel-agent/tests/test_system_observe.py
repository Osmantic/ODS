import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

import importlib.util
import contextlib
import io
import json
import socket
import pathlib
import stat
import tempfile
import threading
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).parents[1] / "host" / "system_observe.py"
SPEC = importlib.util.spec_from_file_location("system_observe", MODULE_PATH)
assert SPEC and SPEC.loader
system_observe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(system_observe)


class SystemObserveTests(unittest.TestCase):
    def setUp(self):
        platform = mock.patch.object(system_observe.sys, 'platform', 'linux')
        platform.start()
        self.addCleanup(platform.stop)

    def test_native_observations_use_only_fixed_read_commands(self):
        for action, commands in system_observe.MACOS_OBSERVATIONS.items():
            with self.subTest(action=action), \
                    mock.patch.object(system_observe.sys, 'platform', 'darwin'), \
                    mock.patch.object(system_observe, '_trusted_executable', side_effect=lambda paths: paths[0]), \
                    mock.patch.object(system_observe, '_run', return_value=mock.Mock(returncode=0, stdout='fixture', stderr='')) as run:
                value = system_observe.observe_macos(action)
                self.assertTrue(value['available'])
                self.assertEqual([call.args[0] for call in run.call_args_list], [list(argv) for argv in commands])
                self.assertNotIn('-c', [arg for command in commands for arg in command])
        self.assertIn('ucomm=', ' '.join(system_observe.MACOS_OBSERVATIONS['processes'][0]))
        self.assertNotIn('args=', ' '.join(system_observe.MACOS_OBSERVATIONS['processes'][0]))

    def test_native_listening_ports_exclude_established_peers(self):
        results = [mock.Mock(returncode=0, stderr='', stdout='tcp4 0 0 *.80 *.* LISTEN\ntcp4 0 0 127.0.0.1.90 127.0.0.1.91 ESTABLISHED\n'),
                   mock.Mock(returncode=0, stderr='', stdout='udp4 0 0 *.53 *.*\nudp4 0 0 *.22 10.0.0.1.23\n')]
        with mock.patch.object(system_observe.sys, 'platform', 'darwin'), \
                mock.patch.object(system_observe, '_trusted_executable', side_effect=lambda paths: paths[0]), \
                mock.patch.object(system_observe, '_run', side_effect=results):
            value = system_observe.observe_macos('listening-ports')
        self.assertEqual(value['observations'][0]['lines'], ['tcp4 0 0 *.80 *.* LISTEN'])
        self.assertEqual(value['observations'][1]['lines'], ['udp4 0 0 *.53 *.*'])

    def test_native_observer_bounds_rows_and_reports_unavailability(self):
        self.assertFalse(system_observe.observe_macos('memory')['available'])
        with mock.patch.object(system_observe.sys, 'platform', 'darwin'), \
                mock.patch.object(system_observe, '_trusted_executable', side_effect=lambda paths: paths[0]), \
                mock.patch.object(system_observe, '_run', return_value=mock.Mock(returncode=0, stdout='row\n' * 300, stderr='')):
            value = system_observe.observe_macos('processes')
            self.assertEqual(len(value['observations'][0]['lines']), 256)
            self.assertTrue(value['observations'][0]['truncated'])
        with mock.patch.object(system_observe.sys, 'platform', 'darwin'), \
                mock.patch.object(system_observe, '_trusted_executable', return_value=None):
            self.assertFalse(system_observe.observe_macos('memory')['available'])

    def test_metal_capability_omits_serials_and_does_not_invent_vram(self):
        value = {'SPDisplaysDataType': [{'sppci_model': 'Apple M5',
                 'spdisplays_mtlgpufamilysupport': 'spdisplays_metal4',
                 'spdisplays_ndrvs': [{'serial': 'private-display-serial'}]}]}
        result = mock.Mock(returncode=0, stdout=json.dumps(value), stderr='')
        with mock.patch.object(system_observe.sys, 'platform', 'darwin'), \
                mock.patch.object(system_observe, '_trusted_executable', return_value='/usr/sbin/system_profiler'), \
                mock.patch.object(system_observe, '_run', return_value=result) as run:
            observation = system_observe.observe_gpu()
        self.assertEqual(observation['backend'], 'metal')
        self.assertEqual(observation['devices'], [{'name': 'Apple M5', 'metal': 'Metal 4'}])
        self.assertNotIn('serial', json.dumps(observation))
        self.assertNotIn('memory', json.dumps(observation))
        run.assert_called_once_with(['/usr/sbin/system_profiler', 'SPDisplaysDataType', '-json'])

    def test_metal_unknown_or_malformed_capability_is_not_claimed(self):
        for value in (None, [], {'SPDisplaysDataType': [{}]},
                      {'SPDisplaysDataType': [{'sppci_model': 'GPU', 'spdisplays_metal': 'unsupported'}]}):
            with mock.patch.object(system_observe, '_trusted_executable', return_value='/usr/sbin/system_profiler'), \
                    mock.patch.object(system_observe, '_run', return_value=mock.Mock(returncode=0, stderr='', stdout=json.dumps(value))):
                self.assertFalse(system_observe.observe_metal()['available'])

    def test_cli_keeps_invalid_command_bytes_out_of_observation_receipts(self):
        for action in ("gpu", "tailscale"):
            for descriptor in (1, 2):
                with self.subTest(action=action, descriptor=descriptor), tempfile.TemporaryDirectory() as directory:
                    executable = pathlib.Path(directory) / "observer"
                    executable.write_text(
                        f"#!{sys.executable}\nimport os\nos.write({descriptor}, bytes([255]))\n",
                        encoding="utf-8",
                    )
                    executable.chmod(0o700)
                    output = io.StringIO()
                    with mock.patch.object(system_observe, "_trusted_executable", return_value=str(executable)), \
                         contextlib.redirect_stdout(output):
                        self.assertEqual(system_observe.main(["observer", action]), 0)
                    value = json.loads(output.getvalue())
                    if action == "gpu":
                        self.assertFalse(value["available"])
                        self.assertEqual(value["devices"], [])
                    else:
                        self.assertEqual(value["state"], "unknown")
                        self.assertFalse(value["serviceRunning"])

    def test_tailscale_cli_rejects_non_object_status_without_crashing(self):
        for payload in ("null", "[]", "42", '"running"'):
            with self.subTest(payload=payload):
                result = mock.Mock(returncode=0, stdout=payload, stderr="")
                output = io.StringIO()
                with mock.patch.object(system_observe, "_trusted_executable", return_value="/usr/bin/tailscale"), \
                     mock.patch.object(system_observe, "_run", return_value=result), \
                     contextlib.redirect_stdout(output):
                    self.assertEqual(system_observe.main(["observer", "tailscale"]), 0)
                value = json.loads(output.getvalue())
                self.assertEqual(value["state"], "unknown")
                self.assertFalse(value["serviceRunning"])

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
            executable = pathlib.Path(directory).resolve() / "powershell.exe"
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

    def test_peer_probes_overlap_with_a_bounded_pool_and_preserve_receipt_order(self):
        addresses = [f"192.168.1.{index}" for index in range(10, 14)]
        ports = list(range(8000, 8008))
        ready = threading.Event()
        lock = threading.Lock()
        active = 0
        maximum = 0
        calls = []

        def probe(family, address, port=None):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                calls.append((family, address, port))
                if active == 8:
                    ready.set()
            try:
                self.assertTrue(ready.wait(2), "peer probes ran serially")
                return port == 8003 if port is not None else None
            finally:
                with lock:
                    active -= 1

        tailscale = {"available": False, "found": False, "online": None, "addresses": []}
        with mock.patch.object(system_observe, "_resolve_peer",
                               return_value=[(socket.AF_INET, address, "lan") for address in addresses]), \
             mock.patch.object(system_observe, "_tailscale_peer_status", return_value=tailscale), \
             mock.patch.object(system_observe, "_probe_icmp", side_effect=probe), \
             mock.patch.object(system_observe, "_probe_tcp", side_effect=probe):
            value = system_observe.observe_network_peer("peer", ",".join(map(str, ports)))
        self.assertEqual(maximum, 8)
        self.assertEqual(len(calls), 36)
        self.assertTrue(value["reachable"])
        self.assertEqual([item["address"] for item in value["addresses"]], addresses)
        for item in value["addresses"]:
            self.assertIsNone(item["icmpReachable"])
            self.assertEqual(item["tcp"], [{"port": port, "open": port == 8003} for port in ports])

    def test_rejected_resolution_starts_no_peer_probes(self):
        with mock.patch.object(system_observe, "_resolve_peer", side_effect=ValueError("private network boundary")), \
             mock.patch.object(system_observe, "_probe_tcp") as tcp, \
             mock.patch.object(system_observe, "_probe_icmp") as icmp, \
             self.assertRaisesRegex(ValueError, "private network boundary"):
            system_observe.observe_network_peer("public.example", "443")
        tcp.assert_not_called()
        icmp.assert_not_called()

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

    def test_link_local_peer_retains_resolved_interface_for_each_probe(self):
        records = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1234", 0, 0, 3)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1234", 0, 0, 4)),
        ]
        tailscale = {"available": False, "found": False, "online": None, "addresses": []}
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.connect_ex.side_effect = lambda endpoint: 0 if endpoint[3] == 3 else 113
        with mock.patch.object(system_observe.socket, "getaddrinfo", return_value=records), \
             mock.patch.object(system_observe.socket, "socket", return_value=connection), \
             mock.patch.object(system_observe, "_tailscale_peer_status", return_value=tailscale), \
             mock.patch.object(system_observe, "_probe_icmp", return_value=False) as icmp:
            value = system_observe.observe_network_peer("printer.local", "443")
        self.assertTrue(value["reachable"])
        self.assertEqual([row["address"] for row in value["addresses"]], ["fe80::1234%3", "fe80::1234%4"])
        self.assertEqual([row["tcp"] for row in value["addresses"]],
                         [[{"port": 443, "open": True}], [{"port": 443, "open": False}]])
        # Probe completion order is intentionally concurrent; only the final
        # receipt order and exact scoped endpoint set are deterministic.
        self.assertEqual(connection.connect_ex.call_count, 2)
        connection.connect_ex.assert_has_calls(
            [mock.call(("fe80::1234", 443, 0, 3)), mock.call(("fe80::1234", 443, 0, 4))], any_order=True)
        self.assertEqual(icmp.call_count, 2)
        icmp.assert_has_calls(
            [mock.call(socket.AF_INET6, "fe80::1234%3"), mock.call(socket.AF_INET6, "fe80::1234%4")], any_order=True)

    def test_receipts_stay_in_resolver_order_when_the_second_peer_finishes_first(self):
        second_finished = threading.Event()
        completed = []
        addresses = ["192.168.1.10", "192.168.1.11"]

        def icmp(_family, address):
            if address == addresses[0]:
                self.assertTrue(second_finished.wait(2), "second probe never started")
                completed.append(address)
                return False
            completed.append(address)
            second_finished.set()
            return True

        tailscale = {"available": False, "found": False, "online": None, "addresses": []}
        with mock.patch.object(system_observe, "_resolve_peer",
                               return_value=[(socket.AF_INET, address, "lan") for address in addresses]), \
             mock.patch.object(system_observe, "_tailscale_peer_status", return_value=tailscale), \
             mock.patch.object(system_observe, "_probe_icmp", side_effect=icmp), \
             mock.patch.object(system_observe, "_probe_tcp", return_value=False):
            value = system_observe.observe_network_peer("peer", "443")
        self.assertEqual(completed, list(reversed(addresses)))
        self.assertEqual([row["address"] for row in value["addresses"]], addresses)
        self.assertEqual([row["icmpReachable"] for row in value["addresses"]], [False, True])

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
