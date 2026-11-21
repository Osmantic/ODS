import json
import os
import tempfile
import unittest
from unittest import mock

import telemetry as t


class ParseMeminfo(unittest.TestCase):
    def test_units_and_skip(self):
        txt = "MemTotal:       16384 kB\nMemAvailable:   8192 kB\nSwapTotal: 0 kB\nBogus: x kB\nNoColon\n"
        d = t.parse_meminfo(txt)
        self.assertEqual(d["MemTotal"], 16384 * 1024)
        self.assertEqual(d["MemAvailable"], 8192 * 1024)
        self.assertEqual(d["SwapTotal"], 0)
        self.assertNotIn("Bogus", d)


class ParseVmstat(unittest.TestCase):
    def test_counters(self):
        d = t.parse_vmstat("pswpin 10\npswpout 20\nbad line here\nx notint\n")
        self.assertEqual(d, {"pswpin": 10, "pswpout": 20})


class ParseVmStat(unittest.TestCase):
    REAL = (
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free:                               12345.\n"
        "Pages active:                            200000.\n"
        "Pages inactive:                          100000.\n"
        "Pages speculative:                         5000.\n"
        "Pages wired down:                         80000.\n"
        "Pages purgeable:                           1000.\n"
        "Pages occupied by compressor:             20000.\n"
        "Swapins:                                   111.\n"
        "Swapouts:                                  222.\n"
    )

    def test_real_format(self):
        p = t.parse_vm_stat(self.REAL)
        self.assertEqual(p["page_size"], 16384)
        c = p["counts"]
        self.assertEqual(c["Pages free"], 12345)
        self.assertEqual(c["Pages wired down"], 80000)
        self.assertEqual(c["Pages occupied by compressor"], 20000)
        self.assertEqual(c["Swapins"], 111)
        self.assertEqual(c["Swapouts"], 222)

    def test_no_page_size(self):
        p = t.parse_vm_stat("Pages free: 5.\n")
        self.assertIsNone(p["page_size"])
        self.assertEqual(p["counts"]["Pages free"], 5)


class ParseSwapusage(unittest.TestCase):
    def test_real(self):
        s = "total = 2048.00M  used = 512.50M  free = 1535.50M  (encrypted)"
        d = t.parse_swapusage(s)
        self.assertEqual(d["total_bytes"], int(2048.0 * 1024**2))
        self.assertEqual(d["used_bytes"], int(512.5 * 1024**2))
        self.assertEqual(d["free_bytes"], int(1535.5 * 1024**2))

    def test_missing(self):
        d = t.parse_swapusage("garbage")
        self.assertIsNone(d["total_bytes"])


class ParseNvidia(unittest.TestCase):
    def test_na_and_numbers(self):
        txt = (
            "0, 8192, 1024, 7168, 45, 60, 120.5\n"
            "1, 8192, 0, 8192, [N/A], [N/A], [N/A]\n"
            "bad,line\n"
        )
        g = t.parse_nvidia_smi_csv(txt)
        self.assertEqual(len(g), 2)
        self.assertEqual(g[0]["index"], 0)
        self.assertEqual(g[0]["memory_total_mib"], 8192.0)
        self.assertEqual(g[0]["power_draw_w"], 120.5)
        self.assertIsNone(g[1]["utilization_gpu_pct"])
        self.assertIsNone(g[1]["temperature_gpu_c"])
        self.assertIsNone(g[1]["power_draw_w"])


class CollectNvidia(unittest.TestCase):
    def test_timeout(self):
        errs = []
        with mock.patch.object(t, "_run", return_value=(None, b"", b"", True)):
            m = t._collect_nvidia(errs)
        self.assertIsNone(m["gpus"])
        self.assertIn("nvidia_smi_timeout", errs)

    def test_unavailable(self):
        errs = []
        with mock.patch.object(t, "_run", return_value=(None, b"", b"", False)):
            t._collect_nvidia(errs)
        self.assertIn("nvidia_smi_unavailable", errs)

    def test_nonzero(self):
        errs = []
        with mock.patch.object(t, "_run", return_value=(1, b"", b"", False)):
            t._collect_nvidia(errs)
        self.assertIn("nvidia_smi_nonzero_exit", errs)

    def test_parse_error(self):
        errs = []
        with mock.patch.object(t, "_run", return_value=(0, b"garbage\n", b"", False)):
            t._collect_nvidia(errs)
        self.assertIn("nvidia_smi_parse_error", errs)


class Cli(unittest.TestCase):
    def test_exclusive_output(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.jsonl")
            open(path, "w").close()
            rc = t.main(["--output", path, "--duration", "1", "--interval", "1", "--scope", "host"])
            self.assertEqual(rc, 3)

    def test_bad_duration(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "o.jsonl")
            rc = t.main(["--output", path, "--duration", "0", "--interval", "1", "--scope", "host"])
            self.assertEqual(rc, 2)
            self.assertFalse(os.path.exists(path))

    def test_bad_interval(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "o.jsonl")
            rc = t.main(["--output", path, "--duration", "1", "--interval", "11", "--scope", "host"])
            self.assertEqual(rc, 2)

    def test_writes_jsonl_and_terminal(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "o.jsonl")
            with mock.patch.object(t, "collect_sample", return_value=({}, [])):
                rc = t.main(["--output", path, "--duration", "1", "--interval", "1", "--scope", "host"])
            self.assertEqual(rc, 0)
            with open(path) as f:
                lines = [json.loads(x) for x in f if x.strip()]
            self.assertGreaterEqual(len(lines), 1)
            self.assertTrue(lines[-1].get("terminal"))
            self.assertEqual(lines[0]["schema_version"], t.SCHEMA_VERSION)
            self.assertEqual(lines[0]["scope"], "host")


if __name__ == "__main__":
    unittest.main()
