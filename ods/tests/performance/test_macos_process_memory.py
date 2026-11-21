import ctypes
import json
import os
import tempfile
import unittest
from unittest import mock

import telemetry as t


class _FakeRusage(ctypes.Structure):
    _fields_ = [
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        ("ri_proc_start_abstime", ctypes.c_uint64),
        ("ri_proc_exit_abstime", ctypes.c_uint64),
    ]


def _make_fake_lib(script):
    """script: dict pid -> list of (rc, errno, fields) consumed per call."""
    calls = {}

    def fake(pid, flavor, buf_ptr):
        assert flavor == 0
        idx = calls.get(pid, 0)
        calls[pid] = idx + 1
        entries = script[pid]
        rc, err, fields = entries[min(idx, len(entries) - 1)]
        if rc == 0:
            buf = ctypes.cast(buf_ptr, ctypes.POINTER(_FakeRusage)).contents
            for name, value in fields.items():
                setattr(buf, name, value)
        ctypes.set_errno(err)
        return rc

    return fake, calls


def _ok(uuid=b"\x01" * 16, start=1000, exit_=0, resident=100, footprint=200, wired=50):
    return (0, 0, {
        "ri_uuid": (ctypes.c_uint8 * 16)(*uuid),
        "ri_proc_start_abstime": start,
        "ri_proc_exit_abstime": exit_,
        "ri_resident_size": resident,
        "ri_phys_footprint": footprint,
        "ri_wired_size": wired,
    })


def _err(errno_val):
    return (1, errno_val, {})


class Abi(unittest.TestCase):
    def test_size_and_offsets(self):
        self.assertEqual(ctypes.sizeof(t._RusageInfoV0), 96)
        self.assertEqual(t._RusageInfoV0.ri_resident_size.offset, 64)
        self.assertEqual(t._RusageInfoV0.ri_phys_footprint.offset, 72)
        self.assertEqual(t._RusageInfoV0.ri_proc_start_abstime.offset, 80)


class Sampler(unittest.TestCase):
    def _sampler(self, specs, script):
        fake, calls = _make_fake_lib(script)
        with mock.patch.object(t.platform, "system", return_value="Darwin"):
            s = t.MacProcessSampler(specs, _loader=lambda: fake)
        return s, calls

    def test_success_units_and_identity(self):
        s, _ = self._sampler({"engine": 123}, {123: [_ok()]})
        out = s.sample()
        e = out["engine"]
        self.assertEqual(e["pid"], 123)
        self.assertEqual(e["start_abstime"], 1000)
        self.assertEqual(e["resident_bytes"], 100)
        self.assertEqual(e["physical_footprint_bytes"], 200)
        self.assertEqual(e["wired_bytes"], 50)
        self.assertIsNone(e["error"])

    def test_reused_pid_latches(self):
        s, _ = self._sampler(
            {"engine": 123},
            {123: [_ok(), _ok(), _ok(uuid=b"\x02" * 16, start=2000, resident=999)]},
        )
        first = s.sample()["engine"]
        self.assertEqual(first["resident_bytes"], 100)
        second = s.sample()["engine"]
        self.assertEqual(second["error"], "process_identity_changed")
        self.assertIsNone(second["resident_bytes"])
        third = s.sample()["engine"]
        self.assertEqual(third["error"], "process_identity_changed")
        self.assertIsNone(third["resident_bytes"])

    def test_exit_latches(self):
        s, _ = self._sampler(
            {"engine": 123},
            {123: [_ok(), _ok(exit_=5, resident=999)]},
        )
        s.sample()
        second = s.sample()["engine"]
        self.assertEqual(second["error"], "process_exited")
        self.assertIsNone(second["resident_bytes"])
        third = s.sample()["engine"]
        self.assertEqual(third["error"], "process_exited")

    def test_initial_missing_stays_missing(self):
        s, calls = self._sampler({"engine": 123}, {123: [_err(3), _ok()]})
        for _ in range(2):
            out = s.sample()["engine"]
            self.assertEqual(out["error"], "process_missing")
            self.assertIsNone(out["resident_bytes"])
        self.assertEqual(calls[123], 1)

    def test_permission_denied(self):
        s, _ = self._sampler({"engine": 123}, {123: [_err(1)]})
        self.assertEqual(s.sample()["engine"]["error"], "permission_denied")

    def test_initial_exit_or_missing_identity_never_rebinds(self):
        for initial, error in [(_ok(exit_=1), "process_exited"),
                               (_ok(start=0), "request_failed")]:
            with self.subTest(error=error):
                s, calls = self._sampler({"engine": 123}, {123: [initial, _ok()]})
                for _ in range(2):
                    self.assertEqual(s.sample()["engine"]["error"], error)
                self.assertEqual(calls[123], 1)

    def test_uuid_or_start_change_alone_suppresses_memory(self):
        for changed in [_ok(uuid=b"\x02" * 16), _ok(start=2000)]:
            s, calls = self._sampler({"engine": 123}, {123: [_ok(), changed]})
            for _ in range(2):
                row = s.sample()["engine"]
                self.assertEqual(row["error"], "process_identity_changed")
                self.assertIsNone(row["physical_footprint_bytes"])
            self.assertEqual(calls[123], 2)

    def test_non_darwin_no_load(self):
        loader = mock.Mock(side_effect=AssertionError("must not load"))
        with mock.patch.object(t.platform, "system", return_value="Linux"):
            s = t.MacProcessSampler({"engine": 123}, _loader=loader)
        loader.assert_not_called()
        self.assertEqual(s.sample()["engine"]["error"], "unsupported_platform")

    def test_api_unavailable(self):
        def bad_loader():
            raise OSError("nope")
        with mock.patch.object(t.platform, "system", return_value="Darwin"):
            s = t.MacProcessSampler({"engine": 123}, _loader=bad_loader)
        self.assertEqual(s.sample()["engine"]["error"], "api_unavailable")

    def test_validation(self):
        with self.assertRaises(ValueError):
            t.MacProcessSampler({"Bad": 1})
        with self.assertRaises(ValueError):
            t.MacProcessSampler({"ok\n": 1})
        with self.assertRaises(ValueError):
            t.MacProcessSampler({"ok": True})
        with self.assertRaises(ValueError):
            t.MacProcessSampler({"ok": 0})
        with self.assertRaises(ValueError):
            t.MacProcessSampler({"ok": 2147483648})
        with self.assertRaises(ValueError):
            t.MacProcessSampler({f"p{i}": i + 1 for i in range(9)})

    def test_no_secrets_in_output(self):
        s, _ = self._sampler({"engine": 123}, {123: [_ok()]})
        blob = json.dumps(s.sample())
        self.assertNotIn("uuid", blob.lower())
        self.assertNotIn("/usr", blob)


class Cli(unittest.TestCase):
    def test_malformed_process_rejected_before_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "o.jsonl")
            with self.assertRaises(SystemExit) as error:
                t.main(["--output", path, "--duration", "1", "--interval", "1",
                        "--scope", "host", "--process", "bad"])
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(os.path.exists(path))

    def test_duplicate_label_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "o.jsonl")
            with mock.patch.object(t.platform, "system", return_value="Darwin"):
                rc = t.main(["--output", path, "--duration", "1", "--interval", "1",
                             "--scope", "host", "--process", "a=1", "--process", "a=2"])
            self.assertEqual(rc, 2)
            self.assertFalse(os.path.exists(path))

    def test_too_many_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "o.jsonl")
            argv = ["--output", path, "--duration", "1", "--interval", "1", "--scope", "host"]
            for i in range(9):
                argv += ["--process", f"p{i}={i + 1}"]
            with mock.patch.object(t.platform, "system", return_value="Darwin"):
                rc = t.main(argv)
            self.assertEqual(rc, 2)
            self.assertFalse(os.path.exists(path))

    def test_process_requires_host_scope(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "o.jsonl")
            with mock.patch.object(t.platform, "system", return_value="Darwin"):
                rc = t.main(["--output", path, "--duration", "1", "--interval", "1",
                             "--scope", "wsl", "--process", "a=1"])
            self.assertEqual(rc, 2)
            self.assertFalse(os.path.exists(path))

    def test_process_requires_darwin(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "o.jsonl")
            with mock.patch.object(t.platform, "system", return_value="Linux"):
                rc = t.main(["--output", path, "--duration", "1", "--interval", "1",
                             "--scope", "host", "--process", "a=1"])
            self.assertEqual(rc, 2)
            self.assertFalse(os.path.exists(path))

    def test_default_no_option_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "o.jsonl")
            with mock.patch.object(t, "collect_sample", return_value=({}, [])), \
                 mock.patch.object(t, "_load_libproc", side_effect=AssertionError("unexpected load")):
                rc = t.main(["--output", path, "--duration", "1", "--interval", "1", "--scope", "host"])
            self.assertEqual(rc, 0)
            with open(path) as f:
                lines = [json.loads(x) for x in f if x.strip()]
            self.assertNotIn("processes", lines[0])
            self.assertNotIn("process_memory_semantics", lines[0])

    def test_process_attached_to_samples(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "o.jsonl")
            fake, _ = _make_fake_lib({123: [_ok()]})
            with mock.patch.object(t.platform, "system", return_value="Darwin"), \
                 mock.patch.object(t, "collect_sample", return_value=({}, [])), \
                 mock.patch.object(t, "_load_libproc", return_value=fake):
                rc = t.main(["--output", path, "--duration", "1", "--interval", "1",
                             "--scope", "host", "--process", "engine=123"])
            self.assertEqual(rc, 0)
            with open(path) as f:
                lines = [json.loads(x) for x in f if x.strip()]
            sample = lines[0]
            self.assertIn("processes", sample)
            self.assertEqual(sample["processes"]["engine"]["resident_bytes"], 100)
            self.assertEqual(sample["process_memory_semantics"], t.PROCESS_MEMORY_SEMANTICS)


if __name__ == "__main__":
    unittest.main()
