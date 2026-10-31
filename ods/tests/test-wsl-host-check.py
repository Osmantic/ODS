#!/usr/bin/env python3
"""Unit tests for scripts/wsl-host-check.py (Windows interop and Docker mocked).

The doctor reports, for ODS in WSL, the memory Windows can still lend (commit
headroom, GlobalMemoryStatusEx ullAvailPageFile), whether WSL already runs
with its .wslconfig memory limit, containers that lost their data after a WSL
restart, and published ports Windows cannot reach. Figures come from the RTX
Spark outage of 2026-10-25 (126.5 GiB commit limit, 2-5 GiB left before WSL
crashed; 13.5 GiB left once WSL was limited to 12 GB).
"""
import base64
import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("wsl_host_check", ROOT / "scripts" / "wsl-host-check.py")
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)
GIB = 1024 ** 3


def windows(available_gib, limit_gib=126.49, memory=None, ports=None):
    return {"available": True, "physicalGiB": 30.49, "commitLimitGiB": limit_gib,
            "commitAvailableGiB": available_gib, "wslconfigPath": "C:\\Users\\dev\\.wslconfig",
            "wslconfig": {"memoryGiB": memory, "reclaim": None}, "ports": ports or {}}


def ids(findings):
    return [item["id"] for item in findings]


class TestWslConfig(unittest.TestCase):
    def test_sizes_read_as_wsl_reads_them(self):
        self.assertEqual(H.wsl_memory_value_gib("12GB"), 12)
        self.assertEqual(H.wsl_memory_value_gib("12g"), 12)
        self.assertEqual(H.wsl_memory_value_gib("8192MB"), 8)
        self.assertEqual(H.wsl_memory_value_gib(str(12 * GIB)), 12)
        self.assertIsNone(H.wsl_memory_value_gib("half"))

    def test_memory_and_reclaim_come_from_their_own_sections(self):
        text = ("# mine\r\n[wsl2]\r\nprocessors=8\r\n# Set by ODS setup: memory limit for WSL.\r\nmemory=12GB\r\n"
                "[experimental]\r\nautoMemoryReclaim=dropcache\r\n")
        self.assertEqual(H.parse_wslconfig(text), {"memoryGiB": 12, "reclaim": "dropcache"})
        self.assertEqual(H.parse_wslconfig("[experimental]\nmemory=8GB\n"), {"memoryGiB": None, "reclaim": None})
        self.assertEqual(H.parse_wslconfig(None), {"memoryGiB": None, "reclaim": None})


class TestMemoryFindings(unittest.TestCase):
    def test_the_crash_state_is_a_blocker_and_names_the_ways_out(self):
        found = H.memory_findings(windows(1.4), {"memTotalGiB": 15.2, "memFreeGiB": 3.0})
        self.assertEqual(ids(found), ["ODS-WINDOWS-COMMIT-EXHAUSTED"])
        self.assertEqual(found[0]["severity"], "blocker")
        steps = " ".join(found[0]["next_steps"])
        self.assertIn("page file", steps)
        self.assertIn("never changes it", steps)
        self.assertIn("discord.gg/4ntNp9MAwC", steps)
        self.assertIn("ullAvailPageFile 1.4 GiB", found[0]["evidence"][0]["detail"])

    def test_under_8_gib_warns_early(self):
        found = H.memory_findings(windows(4.6), {"memTotalGiB": 15.2, "memFreeGiB": 2.0})
        self.assertEqual(ids(found), ["ODS-WINDOWS-COMMIT-LOW"])
        self.assertEqual(found[0]["severity"], "warn")

    def test_the_limited_spark_is_quiet(self):
        self.assertEqual(H.memory_findings(windows(13.5, memory=12), {"memTotalGiB": 11.65, "memFreeGiB": 3.1}), [])

    def test_wsl_that_can_outgrow_what_windows_has_left_is_named(self):
        found = H.memory_findings(windows(9.0), {"memTotalGiB": 15.2, "memFreeGiB": 11.0})
        self.assertEqual(ids(found), ["ODS-WSL-CAN-EXHAUST-COMMIT"])
        self.assertIn(".wslconfig", " ".join(found[0]["next_steps"]))

    def test_a_limit_wsl_has_not_picked_up_yet_asks_for_a_wsl_restart(self):
        found = H.memory_findings(windows(10.0, memory=12), {"memTotalGiB": 15.2, "memFreeGiB": 2.0})
        self.assertEqual(ids(found), ["ODS-WSL-MEMORY-LIMIT-PENDING"])
        steps = " ".join(found[0]["next_steps"])
        self.assertIn("wsl --shutdown", steps)
        self.assertIn("Docker Desktop", steps)
        self.assertIn("restart", found[0]["title"])


class TestMountsPortsAndRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.install = Path(self.tmp.name)
        (self.install / "data").mkdir()
        (self.install / "data" / "model-state.json").write_text("{}")

    def test_an_empty_data_view_is_a_blocker_with_the_repair_command(self):
        host = (self.install / "data").stat()
        with mock.patch.object(H, "docker_stat", return_value={"device": host.st_dev + 5, "inode": 77, "entries": 0}):
            bind = H.bind_mount_report(self.install, "ods-dashboard-api", True)
        self.assertEqual((bind["state"], bind["containerEntries"], bind["hostEntries"]), ("stale", 0, 1))
        found = H.mount_findings(bind)
        self.assertEqual(ids(found), ["ODS-WSL-BIND-MOUNTS-STALE"])
        self.assertIn("an empty folder", found[0]["title"])
        self.assertIn("ods repair mounts", found[0]["next_steps"][0])

    def test_the_hosts_own_folder_is_healthy_and_nothing_is_checked_without_docker(self):
        host = (self.install / "data").stat()
        with mock.patch.object(H, "docker_stat", return_value={"device": host.st_dev, "inode": host.st_ino, "entries": 2}):
            self.assertEqual(H.bind_mount_report(self.install, "ods-dashboard-api", True)["state"], "match")
        with mock.patch.object(H, "docker_stat") as probe:
            self.assertFalse(H.bind_mount_report(self.install, "ods-dashboard-api", False)["checked"])
        probe.assert_not_called()

    def test_ports_windows_cannot_reach_point_to_a_docker_desktop_restart(self):
        services = [("dashboard", 3001), ("hermes-proxy", 9120), ("searxng", 8888)]
        found = H.port_findings(windows(13.5, ports={"3001": "refused", "9120": "answering", "8888": "timeout"}),
                                services)
        self.assertEqual(ids(found), ["ODS-DOCKER-DESKTOP-PORTS-DEAD"])
        self.assertEqual(found[0]["severity"], "blocker")
        self.assertIn("dashboard (localhost:3001 refused)", found[0]["title"])
        self.assertNotIn("searxng", found[0]["title"])  # A timeout is no evidence.
        self.assertIn("Restart Docker Desktop", found[0]["next_steps"][0])
        self.assertEqual(found[0]["severity"], "blocker")
        other = H.port_findings(windows(13.5, ports={"8888": "closed"}), services)
        self.assertEqual(other[0]["severity"], "warn")

    def test_only_this_boots_agent_record_counts(self):
        record = {"bootId": "b1", "state": "needs-docker-desktop-restart", "next": "Restart Docker Desktop: ...",
                  "detail": "no proof", "repairs": 1, "deadPorts": ["ods-webui"], "portsNext": "Restart Docker Desktop"}
        (self.install / "data" / "wsl-self-heal.json").write_text(json.dumps(record))
        self.assertIsNone(H.self_heal_report(self.install, "b2"))
        seen = H.self_heal_report(self.install, "b1")
        self.assertEqual(ids(H.self_heal_findings(seen)), ["ODS-WSL-SELF-HEAL-NEEDS-HELP", "ODS-WSL-PORTS-STILL-DEAD"])
        self.assertEqual(H.self_heal_findings({**seen, "state": "repaired", "deadPorts": None}), [])


class TestWindowsInterop(unittest.TestCase):
    def ps_result(self, payload, code=0):
        return subprocess.CompletedProcess([], code, "\ufeff" + json.dumps(payload), "")

    def test_the_figure_is_globalmemorystatusex_ullavailpagefile(self):
        payload = {"totalPhys": int(30.49 * GIB), "availPhys": 3 * GIB, "totalPageFile": int(126.49 * GIB),
                   "availPageFile": int(13.5 * GIB), "wslconfigPath": "C:\\Users\\dev\\.wslconfig",
                   "wslconfig": "[wsl2]\r\nmemory=12GB\r\n", "ports": {"3001": "answering"}}
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return self.ps_result(payload)

        with mock.patch.object(H, "powershell_path", return_value="powershell.exe"), \
             mock.patch.object(H.subprocess, "run", side_effect=run):
            report = H.windows_report([3001])
        self.assertEqual((report["commitAvailableGiB"], report["commitLimitGiB"]), (13.5, 126.49))
        self.assertEqual(report["wslconfig"]["memoryGiB"], 12)
        self.assertEqual(report["ports"], {"3001": "answering"})
        script = base64.b64decode(calls[0][calls[0].index("-EncodedCommand") + 1]).decode("utf-16-le")
        self.assertIn("GlobalMemoryStatusEx", script)
        self.assertIn("availPageFile = $status.AvailPageFile", script)
        self.assertIn("@(3001)", script)
        # Read-only: nothing in the script changes Windows.
        for verb in ("Set-", "New-Item", "Remove-", "Start-Process", "RunAs"):
            self.assertNotIn(verb, script)

    def test_no_interop_or_a_failed_call_leaves_the_figures_unknown(self):
        with mock.patch.object(H, "powershell_path", return_value=None):
            self.assertFalse(H.windows_report([])["available"])
        with mock.patch.object(H, "powershell_path", return_value="powershell.exe"), \
             mock.patch.object(H.subprocess, "run", side_effect=subprocess.TimeoutExpired("powershell.exe", 45)):
            self.assertIn("did not answer", H.windows_report([])["reason"])
        with mock.patch.object(H, "powershell_path", return_value="powershell.exe"), \
             mock.patch.object(H.subprocess, "run", return_value=self.ps_result({"oops": 1})):
            self.assertFalse(H.windows_report([])["available"])

    def test_ports_must_be_integers_before_they_reach_the_script(self):
        self.assertEqual(H.parse_port("dashboard:3001"), ("dashboard", 3001))
        for junk in ("dashboard:3001;Remove-Item", "3001", "x:0", "x:70000"):
            with self.subTest(junk=junk), self.assertRaises(Exception):
                H.parse_port(junk)

    def test_main_prints_one_json_object_with_findings(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        install = Path(tmp.name)
        (install / "data").mkdir()
        out = io.StringIO()
        with mock.patch.object(H, "windows_report", return_value=windows(1.0)), \
             mock.patch.object(H, "read_meminfo", return_value={"memTotalGiB": 15.2, "memFreeGiB": 3.0}), \
             mock.patch.object(H, "current_boot_id", return_value=""), \
             contextlib.redirect_stdout(out):
            self.assertEqual(H.main(["--install-dir", str(install), "--docker", "false", "--port", "dashboard:3001"]), 0)
        printed = json.loads(out.getvalue())
        self.assertEqual(ids(printed["findings"]), ["ODS-WINDOWS-COMMIT-EXHAUSTED"])
        self.assertTrue(printed["applies"])
        self.assertNotIn("wslconfig_text", json.dumps(printed))


class TestDoctorWiring(unittest.TestCase):
    def test_doctor_runs_the_check_only_in_wsl_and_reports_its_findings(self):
        doctor = (ROOT / "scripts" / "ods-doctor.sh").read_text(encoding="utf-8")
        self.assertIn('"$(uname -r 2>/dev/null)" == *[Mm]icrosoft*', doctor)
        self.assertIn("scripts/wsl-host-check.py", doctor)
        self.assertIn('"wsl_host": wsl_host', doctor)
        self.assertIn("+ wsl_host_diagnoses", doctor)
        cli = (ROOT / "ods-cli").read_text(encoding="utf-8")
        self.assertIn("Windows commit headroom", cli)


if __name__ == "__main__":
    unittest.main()
