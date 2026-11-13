#!/usr/bin/env python3
"""Unit tests for scripts/wsl-bind-recovery.py (Docker mocked)."""
import contextlib
import importlib.util
import io
import json
import os
import stat as statmod
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve()
SCRIPT = HERE.parents[1] / "scripts" / "wsl-bind-recovery.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("wsl_bind_recovery", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = load_helper()


def cp(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def stat_line(dev, ino, mode):
    return f"{dev}:{ino}:{mode:x}\n"


DIR_MODE = statmod.S_IFDIR | 0o755
REG_MODE = statmod.S_IFREG | 0o644


def make_install(tmp):
    d = Path(tmp) / "install"
    d.mkdir()
    return d


def fake_config(name="proj", services=None):
    return {"name": name, "services": services or {}}


def bind(src, dst, ro=False):
    return {"type": "bind", "source": src, "target": dst, "read_only": ro}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.install = make_install(self.tmp.name)
        self.host = Path(self.tmp.name) / "host"
        self.host.mkdir()
        (self.host / "f.txt").write_text("x")
        # The recovery lock (flock, WSL only) has its own tests below.
        unlocked = mock.patch.object(H, "recovery_lock", lambda *_a, **_k: contextlib.nullcontext())
        unlocked.start()
        self.addCleanup(unlocked.stop)

    def run_main(self, argv, run_side_effect, which="docker"):
        with mock.patch.object(H, "run", side_effect=run_side_effect), \
             mock.patch.object(H.shutil, "which", return_value=which), \
             mock.patch.object(H, "is_wsl_docker_desktop", return_value=True):
            return H.main(argv)


class TestProbeNotFreshVolumesFrom(Base):
    def test_exec_stat_uses_original_container(self):
        calls = []

        def fake_run(argv, timeout, check=True, capture=True, binary=False):
            calls.append(list(argv))
            return cp(stat_line(1, 2, DIR_MODE))

        with mock.patch.object(H, "run", side_effect=fake_run):
            H.exec_stat("orig-ctr", "/data")
        self.assertEqual(calls[0][:3], ["docker", "exec", "orig-ctr"])
        self.assertIn("stat", calls[0])
        self.assertNotIn("volumes-from", " ".join(calls[0]))
        self.assertNotIn("run", calls[0][:2])

    def test_exec_stat_parses_filetype(self):
        with mock.patch.object(H, "run", return_value=cp(stat_line(5, 6, REG_MODE))):
            got = H.exec_stat("c", "/x")
        self.assertEqual(got, {"device": 5, "inode": 6, "filetype": "file"})


class TestStaleRunningSelection(Base):
    def test_later_probe_failure_is_not_hidden_by_an_earlier_stale_bind(self):
        binds = [(str(self.host), "/first", False), (str(self.host), "/later", True)]
        with mock.patch.object(H, "exec_stat", side_effect=[
                {"device": 0, "inode": 0, "filetype": "dir"}, RuntimeError("probe failed")]):
            with self.assertRaises(RuntimeError):
                H.classify_running("original", binds)

    def test_stale_when_inode_differs(self):
        binds = [(str(self.host), "/data", False)]
        with mock.patch.object(H, "host_stat", return_value={"device": 1, "inode": 10, "filetype": "dir"}), \
             mock.patch.object(H, "exec_stat", return_value={"device": 1, "inode": 99, "filetype": "dir"}):
            stale, reason = H.classify_running("c", binds)
        self.assertTrue(stale)
        self.assertIn("stale-bind", reason)

    def test_healthy_when_identical(self):
        binds = [(str(self.host), "/data", False)]
        same = {"device": 1, "inode": 10, "filetype": "dir"}
        with mock.patch.object(H, "host_stat", return_value=same), \
             mock.patch.object(H, "exec_stat", return_value=same):
            stale, reason = H.classify_running("c", binds)
        self.assertFalse(stale)
        self.assertEqual(reason, "healthy")


class TestWindowsReadonlyV9fsBind(Base):
    def setUp(self):
        super().setUp()
        self.src = str(self.host)
        self.dst = "/model-stores/windows-lemonade"
        self.host_identity = {"device": 198, "inode": 41376821576481630, "filetype": "dir"}
        self.container_identity = {**self.host_identity, "device": 112}
        self.mount = {"Type": "bind", "Source": self.src,
                      "Destination": self.dst, "RW": False}

    def test_readonly_v9fs_device_drift_is_healthy_before_and_after_recreate(self):
        container = {"State": {"Status": "running"}, "Mounts": [self.mount]}
        with mock.patch.object(H, "host_stat", return_value=self.host_identity), \
             mock.patch.object(H, "exec_stat", return_value=self.container_identity), \
             mock.patch.object(H, "run", return_value=cp("v9fs\n")) as probe, \
             mock.patch.object(H, "inspect_container", return_value=container):
            self.assertEqual(H.classify_running("dashboard", [(self.src, self.dst, True)]),
                             (False, "healthy"))
            with mock.patch.object(H, "compose_ps", return_value=[
                    {"Service": "dashboard-api", "Name": "dashboard"}]):
                self.assertEqual(H.verify([], "dashboard-api", [(self.src, self.dst, True)]),
                                 (True, "ok"))
        self.assertEqual(probe.call_count, 4)

    def test_v9fs_inode_drift_stays_stale(self):
        changed = {**self.container_identity, "inode": 2}
        with mock.patch.object(H, "host_stat", return_value=self.host_identity), \
             mock.patch.object(H, "exec_stat", return_value=changed), \
             mock.patch.object(H, "run") as probe:
            self.assertEqual(H.classify_running("dashboard", [(self.src, self.dst, True)]),
                             (True, f"stale-bind:{self.dst}"))
        probe.assert_not_called()

    def test_native_filesystem_device_drift_stays_stale(self):
        with mock.patch.object(H, "host_stat", return_value=self.host_identity), \
             mock.patch.object(H, "exec_stat", return_value=self.container_identity), \
             mock.patch.object(H, "run", return_value=cp("ext2/ext3\n")), \
             mock.patch.object(H, "inspect_container") as inspect:
            self.assertEqual(H.classify_running("dashboard", [(self.src, self.dst, True)]),
                             (True, f"stale-bind:{self.dst}"))
        inspect.assert_not_called()

    def test_writable_v9fs_remains_strict_and_requires_backup(self):
        with mock.patch.object(H, "host_stat", return_value=self.host_identity), \
             mock.patch.object(H, "exec_stat", return_value=self.container_identity), \
             mock.patch.object(H, "run") as probe:
            self.assertEqual(H.classify_running("dashboard", [(self.src, self.dst, False)]),
                             (True, f"stale-bind:{self.dst}"))
        probe.assert_not_called()

    def test_mismatched_inspected_mount_fails_closed(self):
        wrong = {**self.mount, "Source": "/unexpected/source"}
        with mock.patch.object(H, "host_stat", return_value=self.host_identity), \
             mock.patch.object(H, "exec_stat", return_value=self.container_identity), \
             mock.patch.object(H, "run", return_value=cp("v9fs\n")), \
             mock.patch.object(H, "inspect_container", return_value={"Mounts": [wrong]}):
            with self.assertRaisesRegex(RuntimeError, "unexpected read-only bind"):
                H.classify_running("dashboard", [(self.src, self.dst, True)])


class TestPrevalidateAllBeforeRecreate(Base):
    def test_missing_later_source_fails_before_recreate(self):
        good = self.host
        missing = Path(self.tmp.name) / "nope"
        cfg = fake_config(services={
            "a": {"volumes": [bind(str(good), "/a")]},
            "b": {"volumes": [bind(str(missing), "/b")]},
        })
        ps_rows = [{"Service": "a", "Name": "ca"}, {"Service": "b", "Name": "cb"}]
        inspect = {"Config": {"Labels": {
            "com.docker.compose.project": "proj",
            "com.docker.compose.service": "a"}},
            "State": {"Status": "running"}}
        inspect_b = json.loads(json.dumps(inspect))
        inspect_b["Config"]["Labels"]["com.docker.compose.service"] = "b"

        def fake_run(argv, timeout, check=True, capture=True, binary=False):
            if "config" in argv:
                return cp(json.dumps(cfg))
            if "ps" in argv:
                return cp(json.dumps(ps_rows))
            if "inspect" in argv:
                return cp(json.dumps([inspect if argv[-1] == "ca" else inspect_b]))
            raise AssertionError(f"unexpected call {argv}")

        recreate_calls = []
        with mock.patch.object(H, "recreate", side_effect=lambda *a: recreate_calls.append(a)):
            rc = self.run_main(["--install-dir", str(self.install), "--", "-f", "x.yml"], fake_run)
        self.assertEqual(rc, 1)
        self.assertEqual(recreate_calls, [])


class TestLabelsExact(Base):
    def test_wrong_project_label_skipped(self):
        cfg = fake_config(services={"a": {"volumes": [bind(str(self.host), "/a")]}})
        ps_rows = [{"Service": "a", "Name": "ca"}]
        inspect = {"Config": {"Labels": {
            "com.docker.compose.project": "other",
            "com.docker.compose.service": "a"}},
            "State": {"Status": "running"}}

        def fake_run(argv, timeout, check=True, capture=True, binary=False):
            if "config" in argv:
                return cp(json.dumps(cfg))
            if "ps" in argv:
                return cp(json.dumps(ps_rows))
            if "inspect" in argv:
                return cp(json.dumps([inspect]))
            raise AssertionError(argv)

        rc = self.run_main(["--install-dir", str(self.install), "--", "-f", "x.yml"], fake_run)
        self.assertEqual(rc, 0)


class TestProfiles(Base):
    def test_bulk_wildcard_reaches_running_container_probe(self):
        cfg = fake_config(services={"a": {"profiles": ["optional"], "volumes": [bind(str(self.host), "/data")]}})
        container = {"State": {"Status": "running"}, "Config": {"Labels": {
            "com.docker.compose.project": "proj", "com.docker.compose.service": "a"}}}
        with mock.patch.object(H, "is_wsl_docker_desktop", return_value=True), \
             mock.patch.object(H, "compose_config_json", return_value=cfg), \
             mock.patch.object(H, "compose_ps", return_value=[{"Service": "a", "Name": "original"}]), \
             mock.patch.object(H, "inspect_container", return_value=container), \
             mock.patch.object(H, "exec_stat", return_value=H.host_stat(self.host)) as probe:
            self.assertEqual(H.main(["--install-dir", str(self.install), "--", "--profile=*", "-f", "fixture.yml"]), 0)
        probe.assert_called_once_with("original", "/data")

    def test_normal_profile_gating(self):
        svc = {"profiles": ["dev"], "volumes": [bind(str(self.host), "/a")]}
        self.assertEqual(H.resolve_bind_targets({"services": {"a": svc}}, "a", set()), [])
        self.assertEqual(len(H.resolve_bind_targets({"services": {"a": svc}}, "a", {"dev"})), 1)

    def test_wildcard_enables_all(self):
        svc = {"profiles": ["dev"], "volumes": [bind(str(self.host), "/a")]}
        self.assertEqual(len(H.resolve_bind_targets({"services": {"a": svc}}, "a", set(), True)), 1)

    def test_explicit_service_bypasses_profiles(self):
        svc = {"profiles": ["dev"], "volumes": [bind(str(self.host), "/a")]}
        self.assertEqual(len(H.resolve_bind_targets({"services": {"a": svc}}, "a", None)), 1)

    def test_active_profiles_parses_flags_and_env(self):
        with mock.patch.dict(os.environ, {"COMPOSE_PROFILES": "a,b"}, clear=False):
            profs, wild = H.active_profiles(["--profile", "c", "--profile=*", "-f", "x"])
        self.assertTrue(wild)
        self.assertEqual(profs, {"a", "b", "c"})


class TestPausedRefusal(Base):
    def test_paused_status_refuses(self):
        cfg = fake_config(services={"a": {"volumes": [bind(str(self.host), "/a")]}})
        ps_rows = [{"Service": "a", "Name": "ca"}]
        inspect = {"Config": {"Labels": {
            "com.docker.compose.project": "proj",
            "com.docker.compose.service": "a"}},
            "State": {"Status": "paused", "Paused": True}}

        def fake_run(argv, timeout, check=True, capture=True, binary=False):
            if "config" in argv:
                return cp(json.dumps(cfg))
            if "ps" in argv:
                return cp(json.dumps(ps_rows))
            if "inspect" in argv:
                return cp(json.dumps([inspect]))
            raise AssertionError(argv)

        rc = self.run_main(["--install-dir", str(self.install), "--", "-f", "x.yml"], fake_run)
        self.assertEqual(rc, 1)

    def test_paused_bool_refuses(self):
        cfg = fake_config(services={"a": {"volumes": [bind(str(self.host), "/a")]}})
        ps_rows = [{"Service": "a", "Name": "ca"}]
        inspect = {"Config": {"Labels": {
            "com.docker.compose.project": "proj",
            "com.docker.compose.service": "a"}},
            "State": {"Status": "running", "Paused": True}}

        def fake_run(argv, timeout, check=True, capture=True, binary=False):
            if "config" in argv:
                return cp(json.dumps(cfg))
            if "ps" in argv:
                return cp(json.dumps(ps_rows))
            if "inspect" in argv:
                return cp(json.dumps([inspect]))
            raise AssertionError(argv)

        rc = self.run_main(["--install-dir", str(self.install), "--", "-f", "x.yml"], fake_run)
        self.assertEqual(rc, 1)


class TestStoppedClassification(Base):
    def test_failed_oci_start_is_recreated_without_archiving_a_nonrunning_view(self):
        cfg = fake_config(services={"a": {"volumes": [bind(str(self.host), "/data")]}})
        container = {"State": {"Status": "exited", "Error": 'OCI mount failed at "/data": not a directory'}, "Config": {"Labels": {
            "com.docker.compose.project": "proj", "com.docker.compose.service": "a"}}}
        with mock.patch.object(H, "is_wsl_docker_desktop", return_value=True), \
             mock.patch.object(H, "compose_config_json", return_value=cfg), \
             mock.patch.object(H, "compose_ps", return_value=[{"Service": "a", "Name": "failed"}]), \
             mock.patch.object(H, "inspect_container", return_value=container), \
             mock.patch.object(H, "prove_fresh_views", return_value=(True, "")), \
             mock.patch.object(H, "backup_phantom") as backup, \
             mock.patch.object(H, "recreate") as recreate, \
             mock.patch.object(H, "verify", return_value=(True, "ok")):
            self.assertEqual(H.main(["--install-dir", str(self.install), "--", "-f", "fixture.yml"]), 0)
        backup.assert_not_called()
        recreate.assert_called_once_with(["-f", "fixture.yml"], "a")

    def test_stopped_oci_error_repairs(self):
        binds = [(str(self.host), "/a", False)]
        ctr = {"State": {"Error": 'OCI runtime: error mounting to rootfs at "/a": not a directory'}}
        stale, reason = H.classify_stopped(ctr, binds)
        self.assertTrue(stale)
        self.assertEqual(reason, "stopped-oci-mount-error")

    def test_unrelated_and_partial_target_errors_do_not_recreate(self):
        binds = [(str(self.host), "/data", False)]
        for target in ("/database", "/nested/data", "/data/file", "/data-cache", "/app"):
            with self.subTest(target=target):
                ctr = {"State": {"Error": f'OCI mount or exec at "{target}": not a directory'}}
                self.assertEqual(H.classify_stopped(ctr, binds), (False, "stopped-no-evidence"))

    def test_desktop_mount_and_entrypoint_errors_name_actual_file_target(self):
        binds = [(str(self.host / "f.txt"), "/app/entrypoint.sh", True)]
        for error in (
            'error mounting "/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/Ubuntu/abc" '
            'to rootfs at "/app/entrypoint.sh": create mountpoint: not a directory',
            'OCI runtime create failed: exec: "/app/entrypoint.sh": is a directory',
        ):
            with self.subTest(error=error):
                self.assertEqual(H.classify_stopped({"State": {"Error": error}}, binds),
                                 (True, "stopped-oci-mount-error"))

    def test_ordinary_exited_no_repair(self):
        binds = [(str(self.host), "/a", False)]
        ctr = {"State": {"Error": ""}}
        stale, reason = H.classify_stopped(ctr, binds)
        self.assertFalse(stale)
        self.assertEqual(reason, "stopped-no-evidence")


class TestReadonlyNoMutation(Base):
    def test_readonly_bind_skipped_in_backup(self):
        binds = [(str(self.host), "/a", True)]
        with mock.patch.object(H, "exec_stat") as es:
            total, entries = H.backup_phantom(self.install, "c", binds)
        self.assertEqual((total, entries), (0, 0))
        es.assert_not_called()


class TestBackupUsesContainerTar(Base):
    def test_backup_streams_from_container_not_host(self):
        binds = [(str(self.host), "/data", False)]
        host_meta = {"device": 1, "inode": 10, "filetype": "dir"}
        ctr_meta = {"device": 1, "inode": 99, "filetype": "dir"}

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo("f.txt")
            data = b"hello"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        tar_bytes = buf.getvalue()

        popen_calls = []
        real_popen = subprocess.Popen

        def fake_popen(argv, stdout=None, stderr=None, **kw):
            popen_calls.append(list(argv))
            code = (
                "import sys;"
                "sys.stdout.buffer.write(" + repr(tar_bytes) + ")"
            )
            return real_popen([sys.executable, "-c", code],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        with mock.patch.object(H, "host_stat", return_value=host_meta), \
             mock.patch.object(H, "exec_stat", return_value=ctr_meta), \
             mock.patch.object(H.subprocess, "Popen", side_effect=fake_popen):
            total, entries = H.backup_phantom(self.install, "c", binds)

        self.assertGreater(total, 0)
        self.assertEqual(entries, 1)
        self.assertTrue(popen_calls)
        argv = popen_calls[0]
        self.assertEqual(argv[:3], ["docker", "exec", "c"])
        self.assertIn("tar", argv)
        self.assertNotIn("cp", argv)


class TestArchiveSafety(Base):
    def test_symlink_root_rejected(self):
        root = self.install / "data"
        root.mkdir()
        link = root / "wsl-bind-recovery"
        link.symlink_to(self.host)
        with self.assertRaises(RuntimeError):
            H.backup_phantom(self.install, "c", [])

    def test_private_perms_enforced(self):
        binds = [(str(self.host), "/data", False)]
        host_meta = {"device": 1, "inode": 10, "filetype": "dir"}
        ctr_meta = {"device": 1, "inode": 99, "filetype": "dir"}
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo("f")
            info.size = 0
            tf.addfile(info, io.BytesIO(b""))
        tar_bytes = buf.getvalue()

        real_popen = subprocess.Popen

        def fake_popen(argv, stdout=None, stderr=None, **kw):
            code = (
                "import sys;"
                "sys.stdout.buffer.write(" + repr(tar_bytes) + ")"
            )
            return real_popen([sys.executable, "-c", code],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        with mock.patch.object(H, "host_stat", return_value=host_meta), \
             mock.patch.object(H, "exec_stat", return_value=ctr_meta), \
             mock.patch.object(H.subprocess, "Popen", side_effect=fake_popen):
            H.backup_phantom(self.install, "c", binds)
        root = self.install / "data" / "wsl-bind-recovery"
        self.assertEqual(statmod.S_IMODE(root.stat().st_mode) & 0o077, 0)
        self.assertEqual(statmod.S_IMODE(root.stat().st_mode) & 0o700, 0o700)

    def test_bounded_tar_stream_capacity_fails_before_popen(self):
        archive = Path(self.tmp.name) / "cap.tar"
        fake_vfs = types.SimpleNamespace(f_bavail=1, f_frsize=4096)
        popen_calls = []

        def fake_popen(*a, **kw):
            popen_calls.append(a)
            raise AssertionError("Popen must not be called")

        with mock.patch.object(H.os, "statvfs", return_value=fake_vfs), \
             mock.patch.object(H.subprocess, "Popen", side_effect=fake_popen):
            with self.assertRaises(RuntimeError):
                H._bounded_tar_stream(
                    "c", ["docker", "exec", "c", "tar", "-cf", "-", "."],
                    str(archive), H.time.monotonic() + 5)
        self.assertEqual(popen_calls, [])

    def test_bounded_tar_data_rejects_oversize(self):
        archive = Path(self.tmp.name) / "a.tar"
        big = b"x" * 2048
        real_popen = subprocess.Popen

        def fake_popen(argv, stdout=None, stderr=None, **kw):
            code = (
                "import sys;"
                "sys.stdout.buffer.write(" + repr(big) + ")"
            )
            return real_popen([sys.executable, "-c", code],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        with mock.patch.object(H, "BACKUP_MAX_BYTES", 1024), \
             mock.patch.object(H.subprocess, "Popen", side_effect=fake_popen):
            with self.assertRaises(RuntimeError):
                H._bounded_tar_stream("c", ["docker", "exec", "c", "tar", "-cf", "-", "."],
                                      str(archive), H.time.monotonic() + 5)


class TestRepeatHealthyNoRecreate(Base):
    def test_orphan_is_not_inspected_or_recreated_while_selected_service_is_checked(self):
        cfg = fake_config(services={"a": {"volumes": [bind(str(self.host), "/data")]}})
        rows = [{"Service": "baserow", "Name": "orphan"}, {"Service": "a", "Name": "active"}]
        container = {"State": {"Status": "running"}, "Config": {"Labels": {
            "com.docker.compose.project": "proj", "com.docker.compose.service": "a"}}}
        with mock.patch.object(H, "is_wsl_docker_desktop", return_value=True), \
             mock.patch.object(H, "compose_config_json", return_value=cfg), \
             mock.patch.object(H, "compose_ps", return_value=rows), \
             mock.patch.object(H, "inspect_container", return_value=container) as inspect, \
             mock.patch.object(H, "classify_running", return_value=(False, "healthy")) as classify, \
             mock.patch.object(H, "backup_phantom") as backup, \
             mock.patch.object(H, "recreate") as recreate:
            result = H.main(["--install-dir", str(self.install), "--", "-f", "x.yml"])
        self.assertEqual(result, 0)
        inspect.assert_called_once_with("active")
        classify.assert_called_once_with("active", [(str(self.host), "/data", False)])
        backup.assert_not_called()
        recreate.assert_not_called()

    def test_healthy_start_no_recreate(self):
        cfg = fake_config(services={"a": {"volumes": [bind(str(self.host), "/a")]}})
        ps_rows = [{"Service": "a", "Name": "ca"}]
        inspect = {"Config": {"Labels": {
            "com.docker.compose.project": "proj",
            "com.docker.compose.service": "a"}},
            "State": {"Status": "running"}}
        same = {"device": 1, "inode": 10, "filetype": "dir"}

        def fake_run(argv, timeout, check=True, capture=True, binary=False):
            if "config" in argv:
                return cp(json.dumps(cfg))
            if "ps" in argv:
                return cp(json.dumps(ps_rows))
            if "inspect" in argv:
                return cp(json.dumps([inspect]))
            if "exec" in argv:
                return cp(stat_line(1, 10, DIR_MODE))
            raise AssertionError(argv)

        recreate_calls = []
        with mock.patch.object(H, "host_stat", return_value=same), \
             mock.patch.object(H, "recreate", side_effect=lambda *a: recreate_calls.append(a)):
            rc = self.run_main(["--install-dir", str(self.install), "--", "-f", "x.yml"], fake_run)
        self.assertEqual(rc, 0)
        self.assertEqual(recreate_calls, [])


class TestMalformedComposePs(Base):
    def test_malformed_ps_fails_closed(self):
        with mock.patch.object(H, "run", return_value=cp("not json at all\n")):
            with self.assertRaises(RuntimeError):
                H.compose_ps(["-f", "x.yml"])


class TestRecreateFlags(Base):
    def test_all_stale_replica_views_are_archived_before_one_service_recreate(self):
        cfg = fake_config(services={"a": {"volumes": [bind(str(self.host), "/data")]}})
        names = ("original-1", "original-2")
        rows = [{"Service": "a", "Name": name} for name in names]
        container = {"State": {"Status": "running"}, "Config": {"Labels": {
            "com.docker.compose.project": "proj", "com.docker.compose.service": "a"}}}
        events = []
        with mock.patch.object(H, "is_wsl_docker_desktop", return_value=True), \
             mock.patch.object(H, "compose_config_json", return_value=cfg), \
             mock.patch.object(H, "compose_ps", return_value=rows), \
             mock.patch.object(H, "inspect_container", return_value=container), \
             mock.patch.object(H, "classify_running", return_value=(True, "stale-bind:/data")), \
             mock.patch.object(H, "prove_fresh_views", return_value=(True, "")), \
             mock.patch.object(H, "backup_phantom", side_effect=lambda _root, name, _binds: (events.append(name) or (1, 1))), \
             mock.patch.object(H, "recreate", side_effect=lambda _flags, service: events.append("recreate:" + service)), \
             mock.patch.object(H, "verify", return_value=(True, "ok")) as verify:
            self.assertEqual(H.main(["--install-dir", str(self.install), "--", "-f", "fixture.yml"]), 0)
        self.assertEqual(events, [*names, "recreate:a"])
        verify.assert_called_once()

    def test_verify_does_not_hide_a_bad_later_replica(self):
        binds = [(str(self.host), "/data", False)]
        same = {"device": 1, "inode": 10, "filetype": "dir"}
        stale = {**same, "inode": 99}
        running = {"State": {"Status": "running"}}
        rows = [{"Service": "a", "Name": "first"}, {"Service": "a", "Name": "second"}]
        for case, second, seen, expected in (
            ("stale", running, stale, "stale-bind:/data"),
            ("paused", {"State": {"Status": "running", "Paused": True}}, same, "paused"),
            ("missing", None, same, "inspect-failed"),
            ("healthy", running, same, "ok"),
        ):
            with self.subTest(case=case), \
                 mock.patch.object(H, "compose_ps", return_value=rows), \
                 mock.patch.object(H, "inspect_container", side_effect=[running, second]) as inspect, \
                 mock.patch.object(H, "host_stat", return_value=same), \
                 mock.patch.object(H, "exec_stat", side_effect=[same, seen]):
                ok, reason = H.verify(["-f", "fixture.yml"], "a", binds)
                self.assertEqual((ok, reason), (case == "healthy", expected))
                self.assertEqual(inspect.call_args_list, [mock.call("first"), mock.call("second")])

    def test_recreate_no_pull_no_build_no_deps(self):
        calls = []

        def fake_run(argv, timeout, check=True, capture=True, binary=False):
            calls.append(list(argv))
            return cp("")

        with mock.patch.object(H, "run", side_effect=fake_run):
            H.recreate(["-f", "x.yml"], "svc")
        argv = calls[0]
        self.assertIn("--no-deps", argv)
        self.assertIn("--no-build", argv)
        self.assertIn("--pull", argv)
        self.assertIn("never", argv)
        self.assertIn("--force-recreate", argv)
        self.assertEqual(argv[-1], "svc")

    def test_verify_reprobes_after_recreate(self):
        binds = [(str(self.host), "/a", False)]
        ps_rows = [{"Service": "a", "Name": "ca"}]
        inspect = {"State": {"Status": "running"}}
        same = {"device": 1, "inode": 10, "filetype": "dir"}

        def fake_run(argv, timeout, check=True, capture=True, binary=False):
            if "ps" in argv:
                return cp(json.dumps(ps_rows))
            if "inspect" in argv:
                return cp(json.dumps([inspect]))
            if "exec" in argv:
                return cp(stat_line(1, 10, DIR_MODE))
            raise AssertionError(argv)

        with mock.patch.object(H, "host_stat", return_value=same), \
             mock.patch.object(H, "run", side_effect=fake_run):
            ok, reason = H.verify(["-f", "x.yml"], "a", binds)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")


IMAGE = "sha256:" + "ab" * 32
OTHER_IMAGE = "sha256:" + "cd" * 32


class TestFreshContainerProof(Base):
    """After a WSL restart Docker Desktop brought ODS's containers back with an
    empty folder where ODS's data belongs (RTX Spark, 2026-10-25). A recreate
    helps only when a NEW container sees the host files, so that is proved
    first, with an image already on this machine, never pulled or built."""

    SOURCE = "/home/owner/ods/data"

    def setUp(self):
        super().setUp()
        self.host_meta = {"device": 2080, "inode": 655361, "filetype": "dir"}
        host = mock.patch.object(H, "host_stat", return_value=self.host_meta)
        host.start()
        self.addCleanup(host.stop)

    def fresh_run(self, seen=None, fs="ext2/ext3", host_fs="ext2/ext3", calls=None, fail=False):
        seen = seen if seen is not None else self.host_meta

        def fake_run(argv, timeout, check=True, capture=True, binary=False):
            if calls is not None:
                calls.append(list(argv))
            if argv[:2] == ["docker", "run"]:
                if fail:
                    raise subprocess.CalledProcessError(125, argv, "", "No such image")
                if "-f" in argv:
                    return cp(fs + "\n")
                return cp(stat_line(seen["device"], seen["inode"], DIR_MODE))
            if argv[:1] == ["stat"]:
                return cp(host_fs + "\n")
            raise AssertionError(argv)
        return fake_run

    def test_probe_is_a_throwaway_offline_container_of_the_local_image(self):
        calls = []
        with mock.patch.object(H, "run", side_effect=self.fresh_run(calls=calls)):
            self.assertTrue(H.fresh_view_matches(IMAGE, self.SOURCE))
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertEqual(argv[:3], ["docker", "run", "--rm"])
        self.assertEqual(argv[argv.index("--pull") + 1], "never")
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertEqual(argv[argv.index("--entrypoint") + 1], "stat")
        self.assertEqual(argv[argv.index("-v") + 1], f"{self.SOURCE}:{H.PROBE_MOUNT}:ro")
        self.assertEqual(argv[argv.index(IMAGE) + 1:], ["-Lc", "%d:%i:%f", "--", H.PROBE_MOUNT])
        self.assertNotIn("build", argv)

    def test_a_new_container_that_sees_another_folder_is_no_proof(self):
        empty = {**self.host_meta, "inode": self.host_meta["inode"] + 1}
        with mock.patch.object(H, "run", side_effect=self.fresh_run(seen=empty)):
            self.assertFalse(H.fresh_view_matches(IMAGE, self.SOURCE))

    def test_windows_v9fs_device_drift_is_proof_only_on_v9fs(self):
        drift = {**self.host_meta, "device": self.host_meta["device"] + 7}
        with mock.patch.object(H, "run", side_effect=self.fresh_run(seen=drift, fs="v9fs", host_fs="v9fs")):
            self.assertTrue(H.fresh_view_matches(IMAGE, self.SOURCE))
        with mock.patch.object(H, "run", side_effect=self.fresh_run(seen=drift, fs="ext2/ext3", host_fs="ext2/ext3")):
            self.assertFalse(H.fresh_view_matches(IMAGE, self.SOURCE))

    def test_a_probe_that_cannot_run_is_an_error_not_a_verdict(self):
        with mock.patch.object(H, "run", side_effect=self.fresh_run(fail=True)):
            with self.assertRaisesRegex(RuntimeError, "could not check"):
                H.fresh_view_matches(IMAGE, self.SOURCE)

    def test_a_source_the_probe_syntax_cannot_carry_is_refused(self):
        for source in ("relative/data", "/home/o:wner/data", "/home/owner/a,b"):
            with self.subTest(source=source), mock.patch.object(H, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "cannot probe bind source"):
                    H.fresh_view_matches(IMAGE, source)
                run.assert_not_called()

    def test_sources_shared_by_services_are_probed_once_with_each_services_image(self):
        src = str(self.host)
        stale = [("a", "ca", [(src, "/data", False)], "running", IMAGE),
                 ("b", "cb", [(src, "/data", False)], "running", OTHER_IMAGE)]
        with mock.patch.object(H, "fresh_view_matches", return_value=True) as probe:
            self.assertEqual(H.prove_fresh_views(stale), (True, ""))
        probe.assert_called_once_with(IMAGE, src)

    def test_without_a_local_image_id_nothing_is_proved(self):
        stale = [("a", "ca", [(str(self.host), "/data", False)], "running", "")]
        with self.assertRaisesRegex(RuntimeError, "no local image id"):
            H.prove_fresh_views(stale)

    def stale_stack(self, events, proven):
        cfg = fake_config(services={"a": {"volumes": [bind(str(self.host), "/data")]}})
        container = {"Image": IMAGE, "State": {"Status": "running"}, "Config": {"Labels": {
            "com.docker.compose.project": "proj", "com.docker.compose.service": "a"}}}
        return contextlib.ExitStack(), [
            mock.patch.object(H, "is_wsl_docker_desktop", return_value=True),
            mock.patch.object(H, "compose_config_json", return_value=cfg),
            mock.patch.object(H, "compose_ps", return_value=[{"Service": "a", "Name": "empty"}]),
            mock.patch.object(H, "inspect_container", return_value=container),
            mock.patch.object(H, "classify_running", return_value=(True, "stale-bind:/data")),
            mock.patch.object(H, "fresh_view_matches", side_effect=lambda image, src: (
                events.append(("probe", image, src)) or proven)),
            mock.patch.object(H, "backup_phantom", side_effect=lambda _root, name, _binds: (
                events.append(("backup", name)) or (0, 0))),
            mock.patch.object(H, "recreate", side_effect=lambda _flags, service: events.append(("recreate", service))),
            mock.patch.object(H, "verify", return_value=(True, "ok")),
        ]

    def run_stale(self, proven, argv=None):
        events = []
        stack, patches = self.stale_stack(events, proven)
        with stack:
            for patch in patches:
                stack.enter_context(patch)
            code = H.main(argv or ["--install-dir", str(self.install), "--", "-f", "fixture.yml"])
        return code, events

    def test_proof_comes_before_any_backup_or_recreate(self):
        code, events = self.run_stale(proven=True)
        self.assertEqual(code, 0)
        self.assertEqual(events, [("probe", IMAGE, str(self.host)), ("backup", "empty"), ("recreate", "a")])

    def test_without_proof_nothing_is_backed_up_or_recreated(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code, events = self.run_stale(proven=False)
        self.assertEqual(code, H.EXIT_NOT_PROVEN)
        self.assertEqual(events, [("probe", IMAGE, str(self.host))])
        self.assertIn("Restart Docker Desktop", stderr.getvalue())
        self.assertIn("ods repair mounts", stderr.getvalue())

    def test_check_mode_reports_stale_without_probing_or_changing(self):
        code, events = self.run_stale(proven=True, argv=["--install-dir", str(self.install), "--check", "--", "-f", "x.yml"])
        self.assertEqual(code, H.EXIT_STALE)
        self.assertEqual(events, [])

    def test_an_unusable_lock_directory_does_not_stop_a_recovery(self):
        def unusable(*_args, **_kwargs):
            raise RuntimeError("unsafe backup directory ownership or type")

        stderr = io.StringIO()
        with mock.patch.object(H, "recovery_lock", unusable), \
             mock.patch.object(H, "is_wsl_docker_desktop", return_value=True), \
             mock.patch.object(H.shutil, "which", return_value="docker"), \
             mock.patch.object(H, "compose_config_json", return_value=fake_config(services={})), \
             mock.patch.object(H, "compose_ps", return_value=[]), \
             contextlib.redirect_stderr(stderr):
            self.assertEqual(H.main(["--install-dir", str(self.install), "--", "-f", "x.yml"]), 0)
        self.assertIn("continuing without the recovery lock", stderr.getvalue())

    def test_repeated_service_options_select_only_those_services(self):
        cfg = fake_config(services={name: {"volumes": [bind(str(self.host), "/data")]} for name in ("a", "b", "c")})
        rows = [{"Service": name, "Name": "c" + name} for name in ("a", "b", "c")]

        def container(name):
            return {"Image": IMAGE, "State": {"Status": "running"}, "Config": {"Labels": {
                "com.docker.compose.project": "proj", "com.docker.compose.service": name[1:]}}}

        with mock.patch.object(H, "is_wsl_docker_desktop", return_value=True), \
             mock.patch.object(H, "compose_config_json", return_value=cfg), \
             mock.patch.object(H, "compose_ps", return_value=rows), \
             mock.patch.object(H, "inspect_container", side_effect=container) as inspect, \
             mock.patch.object(H, "classify_running", return_value=(False, "healthy")):
            code = H.main(["--install-dir", str(self.install), "--service", "a", "--service", "c",
                           "--service", "", "--", "-f", "x.yml"])
        self.assertEqual(code, 0)
        self.assertEqual(inspect.call_args_list, [mock.call("ca"), mock.call("cc")])


@unittest.skipUnless(hasattr(os, "getuid"), "the recovery lock is flock, which WSL (Linux) provides")
class TestRecoveryLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.install = make_install(self.tmp.name)

    def test_a_second_recovery_waits_then_reports_busy(self):
        with H.recovery_lock(self.install):
            with self.assertRaises(H.RecoveryBusy):
                with H.recovery_lock(self.install, wait_seconds=0):
                    self.fail("two recoveries held the lock at once")
        with H.recovery_lock(self.install, wait_seconds=0):
            pass  # Released when the first one finished.
        root = self.install / "data" / "wsl-bind-recovery"
        self.assertEqual(statmod.S_IMODE(root.stat().st_mode) & 0o077, 0)

    def test_main_exits_busy_while_another_recovery_holds_the_lock(self):
        with H.recovery_lock(self.install), \
             mock.patch.object(H, "LOCK_WAIT", 0), \
             mock.patch.object(H, "is_wsl_docker_desktop", return_value=True), \
             mock.patch.object(H.shutil, "which", return_value="docker"), \
             mock.patch.object(H, "compose_config_json") as config:
            code = H.main(["--install-dir", str(self.install), "--", "-f", "x.yml"])
        self.assertEqual(code, H.EXIT_BUSY)
        config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
