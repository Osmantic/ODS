import fcntl
import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "deploy/release-operator"
DISPATCH = PKG / "dispatch.py"
MANAGED = PKG / "managed.py"
CLIENT = PKG / "client.py"
PROVISION = PKG / "provision-target.sh"
sys.path.insert(0, str(PKG))
import pixel_release_grammar as grammar  # noqa: E402

# Import the managed module in TESTING mode (non-root test process) so its validation and
# receipt helpers can be exercised directly without root ownership checks.
os.environ["PIXEL_RELEASE_MANAGED_TESTING"] = "1"
import managed  # noqa: E402


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()

def rooted_lstat(real_lstat, target):
    """Return an os.lstat wrapper that reports st_uid=0 for the target path."""
    def wrapped(path):
        st = real_lstat(path)
        if os.path.abspath(os.fspath(path)) == os.path.abspath(os.fspath(target)):
            fields = list(st)
            fields[4] = 0  # st_uid
            st = os.stat_result(fields)
        return st
    return wrapped


def lstat_overrides(real_lstat, overrides):
    """Return an os.lstat wrapper applying per-path (mode, uid) overrides.

    overrides maps an absolute path to a (mode, uid) tuple; either value may be
    None to leave the real stat field unchanged.
    """
    def wrapped(path):
        st = real_lstat(path)
        override = overrides.get(os.path.abspath(os.fspath(path)))
        if override is not None:
            mode, uid = override
            fields = list(st)
            if mode is not None:
                fields[0] = mode  # st_mode
            if uid is not None:
                fields[4] = uid  # st_uid
            st = os.stat_result(fields)
        return st
    return wrapped


def write_config(directory: Path, *, templates=None, receipt_root=None):
    templates = templates or {}
    config = {
        "schemaVersion": 1,
        "units": {},
        "readers": {
            "ops": {"user": "agent", "probes": [{"path": str(directory / "private"), "access": "read", "expect": False}]},
            "frontier": {"user": "agent", "probes": [{"path": str(directory / "requests"), "access": "write", "expect": True}]},
        },
        "receiptRoot": str(receipt_root or (directory / "receipts")),
    }
    for unit_id in ("gateway", "courier"):
        spec = templates.get(unit_id, {"bytes": f"{unit_id}\n".encode()})
        template_path = directory / f"{unit_id}.template"
        template_path.write_bytes(spec["bytes"])
        template_path.chmod(0o644)
        destination = spec.get("destination", directory / "etc" / f"{unit_id}.service")
        entry = {
            "name": spec.get("name", destination.name),
            "destination": str(destination),
            "template": str(template_path),
            "templateSha256": sha(spec["bytes"]),
        }
        if spec.get("prior"):
            prior_path = directory / f"{unit_id}.prior"
            prior_path.write_bytes(spec["prior"])
            prior_path.chmod(0o644)
            entry["priorTemplate"] = str(prior_path)
            entry["priorTemplateSha256"] = sha(spec["prior"])
        config["units"][unit_id] = entry
    path = directory / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path, config


class DispatcherTests(unittest.TestCase):
    def run_dispatch(self, command):
        environment = {**os.environ, "SSH_ORIGINAL_COMMAND": command, "PIXEL_RELEASE_DISPATCH_TESTING": "1"}
        return subprocess.run(
            [sys.executable, str(DISPATCH)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, check=False,
        )

    def test_valid_grammar_accepted_without_shell(self):
        helper = grammar.HELPER
        commands = [
            f"{helper} status",
            f"{helper} --validate-config",
            f"{helper} unit install gateway {'0' * 64}",
            f"{helper} unit install courier {'a' * 64}",
            f"{helper} unit remove gateway",
            f"{helper} unit remove courier",
            f"{helper} systemctl daemon-reload",
            f"{helper} systemctl restart gateway",
            f"{helper} systemctl enable courier --now",
            f"{helper} systemctl disable gateway --now",
            f"{helper} systemctl is-active courier",
            f"{helper} probe ops",
            f"{helper} probe frontier",
        ]
        for command in commands:
            completed = self.run_dispatch(command)
            self.assertEqual(completed.returncode, 0, (command, completed.stderr.decode()))
            evidence = json.loads(completed.stdout)
            self.assertFalse(evidence["shell"])
            self.assertTrue(evidence["transportIdentitySeparated"])
            self.assertEqual(evidence["helper"], helper)
            self.assertTrue(evidence["routedArgv"][0].endswith("sudo"))
            self.assertEqual(evidence["routedArgv"][1:3], ["--non-interactive", helper])

    def test_shells_options_and_arbitrary_commands_rejected(self):
        helper = grammar.HELPER
        commands = [
            "/bin/bash -lc id",
            "sh -c id",
            f"{helper} unit install gateway XXXX",
            f"{helper} unit install gateway {'0' * 63}",
            f"{helper} unit install gateway {'A' * 64}",
            f"{helper} unit install evil {'0' * 64}",
            f"{helper} unit remove evil",
            f"{helper} systemctl reboot",
            f"{helper} systemctl restart evil",
            f"{helper} systemctl restart gateway --now",
            f"{helper} probe evil",
            f"{helper} deploy activate app v2",
            f"{helper} ; /usr/bin/id",
            f"{helper} unit install gateway {'0' * 64} ; /usr/bin/id",
            f"cd /tmp && exec {helper} status",
            f"{helper} status && echo pwned",
            f"$(id)",
        ]
        for command in commands:
            completed = self.run_dispatch(command)
            self.assertNotEqual(completed.returncode, 0, command)

    def test_rejects_wrong_helper_and_controls(self):
        for command in ("/usr/local/libexec/pixel-release-managed-other status", "status", ""):
            completed = self.run_dispatch(command)
            self.assertNotEqual(completed.returncode, 0, command)
        completed = self.run_dispatch(f"{grammar.HELPER} status\n")
        self.assertNotEqual(completed.returncode, 0)


class ManagedUnitTests(unittest.TestCase):
    def test_secure_read_rejects_changed_on_open_race(self):
        # Simulate the backing file being changed while it is read (mtime_ns shifts on the
        # post-read fstat) and require secure_read to reject instead of returning bytes.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "f"
            path.write_bytes(b"payload")
            real_fstat = os.fstat
            state = {"calls": 0}

            def fake_fstat(fd):
                st = real_fstat(fd)
                state["calls"] += 1
                if state["calls"] == 2:
                    st = os.stat_result((
                        st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid, st.st_size,
                        st.st_atime, st.st_mtime, st.st_ctime,
                        st.st_atime_ns, st.st_mtime_ns + 1, st.st_ctime_ns,
                    ))
                return st

            with mock.patch.object(managed.os, "fstat", side_effect=fake_fstat):
                with self.assertRaises(managed.ManagedError):
                    managed.secure_read(path, "unit destination", managed.MAX_TEMPLATE)

    def run_managed(self, config_path, *args, env=None):
        environment = {
            **os.environ,
            "PIXEL_RELEASE_MANAGED_CONFIG": str(config_path),
            "PIXEL_RELEASE_MANAGED_TESTING": "1",
        }
        if env:
            environment.update(env)
        return subprocess.run(
            [sys.executable, str(MANAGED), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, check=False,
        )

    def test_install_and_remove_template_by_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            gateway_bytes = b"[Unit]\nDescription=gateway\n"
            config_path, config = write_config(root, templates={"gateway": {"bytes": gateway_bytes, "destination": dest_dir / "gateway.service"}})
            destination = dest_dir / "gateway.service"
            installed = self.run_managed(config_path, "unit", "install", "gateway", sha(gateway_bytes))
            self.assertEqual(installed.returncode, 0, installed.stderr.decode())
            self.assertEqual(destination.read_bytes(), gateway_bytes)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o644)
            evidence = json.loads(installed.stdout)
            self.assertFalse(evidence["idempotent"])
            self.assertTrue(evidence["receipt"])
            self.assertTrue(evidence["receiptSha256"])
            idempotent = self.run_managed(config_path, "unit", "install", "gateway", sha(gateway_bytes))
            self.assertEqual(idempotent.returncode, 0, idempotent.stderr.decode())
            self.assertTrue(json.loads(idempotent.stdout)["idempotent"])
            removed = self.run_managed(config_path, "unit", "remove", "gateway")
            self.assertEqual(removed.returncode, 0, removed.stderr.decode())
            self.assertFalse(destination.exists())
            self.assertFalse(json.loads(removed.stdout)["idempotent"])
            removed_again = self.run_managed(config_path, "unit", "remove", "gateway")
            self.assertEqual(removed_again.returncode, 0, removed_again.stderr.decode())
            self.assertTrue(json.loads(removed_again.stdout)["idempotent"])

    def test_digest_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"real\n", "destination": dest_dir / "gateway.service"}})
            completed = self.run_managed(config_path, "unit", "install", "gateway", "0" * 64)
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((dest_dir / "gateway.service").exists())

    def test_prior_template_install_for_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            current = b"current\n"
            prior = b"prior\n"
            config_path, _ = write_config(root, templates={"gateway": {"bytes": current, "prior": prior, "destination": dest_dir / "gateway.service"}})
            destination = dest_dir / "gateway.service"
            install_prior = self.run_managed(config_path, "unit", "install", "gateway", sha(prior))
            self.assertEqual(install_prior.returncode, 0, install_prior.stderr.decode())
            self.assertEqual(destination.read_bytes(), prior)
            remove_prior = self.run_managed(config_path, "unit", "remove", "gateway")
            self.assertEqual(remove_prior.returncode, 0, remove_prior.stderr.decode())
            self.assertFalse(destination.exists())

    def test_unexpected_existing_destination_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            destination = dest_dir / "gateway.service"
            destination.write_bytes(b"unexpected bytes not matching any template\n")
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"real\n", "destination": destination}})
            completed = self.run_managed(config_path, "unit", "install", "gateway", sha(b"real\n"))
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(destination.read_bytes(), b"unexpected bytes not matching any template\n")

    def test_idempotent_install_repairs_unsafe_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            gateway_bytes = b"[Unit]\nDescription=gateway\n"
            config_path, _ = write_config(root, templates={"gateway": {"bytes": gateway_bytes, "destination": dest_dir / "gateway.service"}})
            destination = dest_dir / "gateway.service"
            first = self.run_managed(config_path, "unit", "install", "gateway", sha(gateway_bytes))
            self.assertEqual(first.returncode, 0, first.stderr.decode())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o644)
            # Bytes still match, but the mode becomes unsafe: reinstall must repair, not
            # report a false idempotent success.
            destination.chmod(0o666)
            repaired = self.run_managed(config_path, "unit", "install", "gateway", sha(gateway_bytes))
            self.assertEqual(repaired.returncode, 0, repaired.stderr.decode())
            self.assertEqual(destination.read_bytes(), gateway_bytes)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o644)
            self.assertFalse(json.loads(repaired.stdout)["idempotent"])
            # A safe reinstall is idempotent again.
            idempotent = self.run_managed(config_path, "unit", "install", "gateway", sha(gateway_bytes))
            self.assertEqual(idempotent.returncode, 0, idempotent.stderr.decode())
            self.assertTrue(json.loads(idempotent.stdout)["idempotent"])

    def test_status_fails_closed_on_unsafe_installed_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            gateway_bytes = b"g\n"
            config_path, _ = write_config(root, templates={"gateway": {"bytes": gateway_bytes, "destination": dest_dir / "gateway.service"}})
            destination = dest_dir / "gateway.service"
            install = self.run_managed(config_path, "unit", "install", "gateway", sha(gateway_bytes))
            self.assertEqual(install.returncode, 0, install.stderr.decode())
            destination.chmod(0o755)
            completed = self.run_managed(config_path, "status")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("root:root:0644", completed.stderr.decode())

    def test_status_fails_closed_on_unsafe_template_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            gateway_bytes = b"g\n"
            config_path, _ = write_config(root, templates={"gateway": {"bytes": gateway_bytes, "destination": dest_dir / "gateway.service"}})
            (root / "gateway.template").chmod(0o666)
            completed = self.run_managed(config_path, "status")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("root:root:0644", completed.stderr.decode())

    def test_symlink_and_hardlink_substitution_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            outside = root / "outside"; outside.write_bytes(b"outside\n")
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"real\n", "destination": dest_dir / "gateway.service"}})
            destination = dest_dir / "gateway.service"
            destination.symlink_to(outside)
            completed = self.run_managed(config_path, "unit", "install", "gateway", sha(b"real\n"))
            self.assertNotEqual(completed.returncode, 0)
            completed = self.run_managed(config_path, "unit", "remove", "gateway")
            self.assertNotEqual(completed.returncode, 0)
            destination.unlink()
            destination.write_bytes(b"real\n")
            os.link(destination, dest_dir / "gateway.hardlink")
            completed = self.run_managed(config_path, "unit", "remove", "gateway")
            self.assertNotEqual(completed.returncode, 0)

    def test_template_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            real = root / "real-template"; real.write_bytes(b"real\n")
            fake = root / "gateway.template"; fake.symlink_to(real)
            config = {
                "schemaVersion": 1,
                "units": {"gateway": {"name": "gateway.service", "destination": str(dest_dir / "gateway.service"), "template": str(fake), "templateSha256": sha(b"real\n")}},
                "readers": {"ops": {"user": "agent", "probes": []}, "frontier": {"user": "agent", "probes": []}},
                "receiptRoot": str(root / "receipts"),
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            completed = self.run_managed(config_path, "unit", "install", "gateway", sha(b"real\n"))
            self.assertNotEqual(completed.returncode, 0)

    def test_arbitrary_unit_verb_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": dest_dir / "gateway.service"}})
            for args in (
                ("unit", "install", "evil", "0" * 64),
                ("unit", "remove", "evil"),
                ("systemctl", "restart", "evil"),
                ("systemctl", "reboot"),
                ("systemctl", "restart", "gateway", "--now"),
                ("probe", "evil"),
                ("deploy", "activate", "app", "v2"),
            ):
                completed = self.run_managed(config_path, *args)
                self.assertNotEqual(completed.returncode, 0, args)

    def test_receipts_parse_bind_and_no_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            config_path, config = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": dest_dir / "gateway.service"}})
            receipt_root = Path(config["receiptRoot"])
            self.run_managed(config_path, "unit", "install", "gateway", sha(b"x\n"))
            self.run_managed(config_path, "unit", "remove", "gateway")
            self.run_managed(config_path, "unit", "install", "gateway", sha(b"x\n"))
            receipts = list(receipt_root.iterdir())
            self.assertEqual(len(receipts), 3)
            names = set()
            for receipt in receipts:
                self.assertGreater(receipt.stat().st_size, 0, "receipts must be non-empty JSON")
                data = json.loads(receipt.read_text())
                self.assertEqual(data["schemaVersion"], 1)
                self.assertIn("timestamp", data)
                self.assertIn("operation", data)
                self.assertEqual(data["exitCode"], 0)
                self.assertIn("resultIdentity", data)
                self.assertIn("evidenceSha256", data)
                self.assertNotIn("path", json.dumps(data))
                names.add(receipt.name)
            self.assertEqual(len(names), 3, "receipt names must be unique/no-replace")
            # Deterministic name forces O_EXCL to reject a second write to the same name.
            value = {"schemaVersion": 1, "receiptRoot": str(root / "receipts2")}
            with mock.patch("os.urandom", return_value=b"\x01" * 32), mock.patch("time.strftime", return_value="20260101T000000Z"):
                name1, sha1 = managed.receipt(value, "status", {"operation": "status"}, exit_code=0)
            self.assertTrue((root / "receipts2" / name1).exists())
            with self.assertRaises(OSError):
                with mock.patch("os.urandom", return_value=b"\x01" * 32), mock.patch("time.strftime", return_value="20260101T000000Z"):
                    managed.receipt(value, "status", {"operation": "status"}, exit_code=0)
            self.assertEqual(sha1, sha((root / "receipts2" / name1).read_bytes()))


class ManagedSystemctlProbeTests(unittest.TestCase):
    def run_managed(self, config_path, *args, env=None):
        environment = {
            **os.environ,
            "PIXEL_RELEASE_MANAGED_CONFIG": str(config_path),
            "PIXEL_RELEASE_MANAGED_TESTING": "1",
        }
        if env:
            environment.update(env)
        return subprocess.run(
            [sys.executable, str(MANAGED), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, check=False,
        )

    def _fake_systemctl(self, directory, exit_code=0, log=None):
        fake = Path(directory) / "fake-systemctl"
        log = log or (Path(directory) / "systemctl.log")
        fake.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" > " + str(log) + "\n"
            "printf '%s\\n' \"$*\"\n"
            f"exit {exit_code}\n"
        )
        os.chmod(fake, 0o700)
        return fake, log

    def test_systemctl_preserves_now_and_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            config_path, config = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": dest_dir / "gateway.service"}})
            fake, log = self._fake_systemctl(root, exit_code=0)
            completed = self.run_managed(
                config_path, "systemctl", "enable", "gateway", "--now",
                env={"PIXEL_RELEASE_MANAGED_SYSTEMCTL": str(fake)},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            self.assertEqual(log.read_text().strip(), "enable --now gateway.service")
            evidence = json.loads(completed.stdout)
            self.assertEqual(evidence["operation"], "systemctl.enable")
            self.assertEqual(evidence["exitCode"], 0)
            self.assertTrue(evidence["receipt"])
            self.assertTrue(evidence["receiptSha256"])
            disable = self.run_managed(
                config_path, "systemctl", "disable", "gateway", "--now",
                env={"PIXEL_RELEASE_MANAGED_SYSTEMCTL": str(fake)},
            )
            self.assertEqual(disable.returncode, 0, disable.stderr.decode())
            self.assertEqual(log.read_text().strip(), "disable --now gateway.service")

    def test_systemctl_failure_is_nonzero_with_failure_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            config_path, config = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": dest_dir / "gateway.service"}})
            fake, _ = self._fake_systemctl(root, exit_code=7)
            completed = self.run_managed(
                config_path, "systemctl", "restart", "gateway",
                env={"PIXEL_RELEASE_MANAGED_SYSTEMCTL": str(fake)},
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("failed with exit code 7", completed.stderr.decode())
            self.assertIn("failure receipt", completed.stderr.decode())
            receipts = list(Path(config["receiptRoot"]).iterdir())
            self.assertEqual(len(receipts), 1)
            data = json.loads(receipts[0].read_text())
            self.assertEqual(data["exitCode"], 1)
            self.assertIn("errorSha256", data)
            self.assertIn("operation", data)

    def test_systemctl_verb_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": dest_dir / "gateway.service"}})
            fake, _ = self._fake_systemctl(root, exit_code=0)
            for args in (("systemctl", "daemon-reload"), ("systemctl", "is-active", "gateway"), ("systemctl", "restart", "courier")):
                completed = self.run_managed(config_path, *args, env={"PIXEL_RELEASE_MANAGED_SYSTEMCTL": str(fake)})
                self.assertEqual(completed.returncode, 0, (args, completed.stderr.decode()))
                evidence = json.loads(completed.stdout)
                self.assertIn("exitCode", evidence)
                self.assertIn("stdout", evidence)

    def test_bounded_run_env_is_fixed_and_sets_home_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": dest_dir / "gateway.service"}})
            fake = Path(directory) / "fake-systemctl"
            env_log = Path(directory) / "env.log"
            fake.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'HOME=%s\\n' \"$HOME\" > " + str(env_log) + "\n"
                "printf 'PATH=%s\\n' \"$PATH\" >> " + str(env_log) + "\n"
                "printf 'LANG=%s\\n' \"$LANG\" >> " + str(env_log) + "\n"
                "exit 0\n"
            )
            os.chmod(fake, 0o700)
            completed = self.run_managed(config_path, "systemctl", "daemon-reload", env={"PIXEL_RELEASE_MANAGED_SYSTEMCTL": str(fake)})
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            lines = env_log.read_text().splitlines()
            self.assertIn("HOME=/", lines)
            self.assertIn("LANG=C.UTF-8", lines)
            # The fixed PATH must be the immutable production search path.
            self.assertIn("PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", lines)

    def test_probe_exactness_and_failure_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readable = root / "readable"; readable.write_bytes(b"x")
            unreadable = root / "unreadable"; unreadable.write_bytes(b"x"); unreadable.chmod(0o000)
            config = {
                "schemaVersion": 1,
                "units": {"gateway": {"name": "gateway.service", "destination": str(root / "gateway.service"), "template": str(root / "gateway.template"), "templateSha256": sha(b"x\n")}, "courier": {"name": "courier.service", "destination": str(root / "courier.service"), "template": str(root / "courier.template"), "templateSha256": sha(b"x\n")}},
                "readers": {
                    "ops": {"user": "agent", "probes": [
                        {"path": str(readable), "access": "read", "expect": True},
                        {"path": str(unreadable), "access": "read", "expect": False},
                    ]},
                    "frontier": {"user": "agent", "probes": [
                        {"path": str(readable), "access": "read", "expect": False},
                    ]},
                },
                "receiptRoot": str(root / "receipts"),
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            ok = self.run_managed(config_path, "probe", "ops")
            self.assertEqual(ok.returncode, 0, ok.stderr.decode())
            evidence = json.loads(ok.stdout)
            self.assertEqual(len(evidence["probes"]), 2)
            fail = self.run_managed(config_path, "probe", "frontier")
            self.assertNotEqual(fail.returncode, 0)
            self.assertIn("failed", fail.stderr.decode())
            self.assertIn(str(readable), fail.stderr.decode())

    def test_probe_rejects_unsafe_user_and_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": root / "g.service"}})
            value = json.loads(config_path.read_text())
            value["readers"]["ops"]["user"] = "bad user!"
            config_path.write_text(json.dumps(value))
            completed = self.run_managed(config_path, "--validate-config")
            self.assertNotEqual(completed.returncode, 0)
            value["readers"]["ops"]["user"] = "agent"
            value["readers"]["ops"]["probes"][0]["path"] = "relative"
            config_path.write_text(json.dumps(value))
            completed = self.run_managed(config_path, "--validate-config")
            self.assertNotEqual(completed.returncode, 0)


class ClientAndConfigTests(unittest.TestCase):
    def run_managed(self, config_path, *args, env=None):
        environment = {
            **os.environ,
            "PIXEL_RELEASE_MANAGED_CONFIG": str(config_path),
            "PIXEL_RELEASE_MANAGED_TESTING": "1",
        }
        if env:
            environment.update(env)
        return subprocess.run(
            [sys.executable, str(MANAGED), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, check=False,
        )

    def test_config_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": dest_dir / "gateway.service"}})
            self.assertEqual(self.run_managed(config_path, "--validate-config").returncode, 0)
            value = json.loads(config_path.read_text())
            value["units"]["gateway"]["templateSha256"] = "ZZZZ"
            config_path.write_text(json.dumps(value))
            self.assertNotEqual(self.run_managed(config_path, "--validate-config").returncode, 0)
            value["units"]["gateway"]["templateSha256"] = "0" * 64
            value["units"]["gateway"]["destination"] = "relative"
            config_path.write_text(json.dumps(value))
            self.assertNotEqual(self.run_managed(config_path, "--validate-config").returncode, 0)

    def test_config_validation_containers_and_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": dest_dir / "gateway.service"}})
            value = json.loads(config_path.read_text())
            # units must be an object.
            value["units"] = ["gateway"]
            config_path.write_text(json.dumps(value))
            self.assertNotEqual(self.run_managed(config_path, "--validate-config").returncode, 0)
            # readers must be an object.
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": dest_dir / "gateway.service"}})
            value = json.loads(config_path.read_text())
            value["readers"] = []
            config_path.write_text(json.dumps(value))
            self.assertNotEqual(self.run_managed(config_path, "--validate-config").returncode, 0)
            # unit name must be a safe .service name matching the destination basename.
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": dest_dir / "gateway.service"}})
            value = json.loads(config_path.read_text())
            value["units"]["gateway"]["name"] = "evil"
            config_path.write_text(json.dumps(value))
            self.assertNotEqual(self.run_managed(config_path, "--validate-config").returncode, 0)
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": dest_dir / "gateway.service"}})
            value = json.loads(config_path.read_text())
            value["units"]["gateway"]["name"] = "other.service"
            config_path.write_text(json.dumps(value))
            self.assertNotEqual(self.run_managed(config_path, "--validate-config").returncode, 0)

    def test_absent_reader_allowed_but_probe_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            config_path, config = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": dest_dir / "gateway.service"}})
            value = json.loads(config_path.read_text())
            # Frontier disabled: omit the frontier reader entirely (ops-only provisioning).
            del value["readers"]["frontier"]
            config_path.write_text(json.dumps(value))
            self.assertEqual(self.run_managed(config_path, "--validate-config").returncode, 0)
            ok = self.run_managed(config_path, "probe", "ops")
            self.assertEqual(ok.returncode, 0, ok.stderr.decode())
            fail = self.run_managed(config_path, "probe", "frontier")
            self.assertNotEqual(fail.returncode, 0)
            self.assertIn("reader is not configured", fail.stderr.decode())

    def test_status_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            gateway_bytes = b"g\n"
            config_path, _ = write_config(root, templates={"gateway": {"bytes": gateway_bytes, "destination": dest_dir / "gateway.service"}})
            status = self.run_managed(config_path, "status")
            self.assertEqual(status.returncode, 0, status.stderr.decode())
            evidence = json.loads(status.stdout)
            self.assertIn("gateway", evidence["units"])
            self.assertIsNone(evidence["units"]["gateway"]["installedSha256"])
            destination = dest_dir / "gateway.service"
            destination.write_bytes(gateway_bytes)
            destination.chmod(0o644)
            status = self.run_managed(config_path, "status")
            evidence = json.loads(status.stdout)
            self.assertEqual(evidence["units"]["gateway"]["installedSha256"], sha(gateway_bytes))

    def test_status_fails_closed_on_unsafe_and_tampered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"g\n", "destination": dest_dir / "gateway.service"}})
            # Unsafe destination symlink -> fail closed, not reported absent.
            (dest_dir / "gateway.service").symlink_to(root / "outside")
            completed = self.run_managed(config_path, "status")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("symlink", completed.stderr.decode())
            (dest_dir / "gateway.service").unlink()
            # Tampered template (bytes no longer match configured SHA) -> fail closed.
            (root / "gateway.template").write_bytes(b"tampered\n")
            completed = self.run_managed(config_path, "status")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("tampered", completed.stderr.decode())

    def test_status_rejects_hardlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            config_path, _ = write_config(root, templates={"gateway": {"bytes": b"g\n", "destination": dest_dir / "gateway.service"}})
            # Hardlinked destination -> fail closed (an extra name can hide a swap).
            destination = dest_dir / "gateway.service"
            destination.write_bytes(b"g\n")
            os.link(destination, dest_dir / "gateway.hardlink")
            completed = self.run_managed(config_path, "status")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("hardlink", completed.stderr.decode())
            os.unlink(dest_dir / "gateway.hardlink")
            destination.unlink()
            # Hardlinked reviewed template -> fail closed.
            os.link(root / "gateway.template", root / "gateway.template.hardlink")
            completed = self.run_managed(config_path, "status")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("hardlink", completed.stderr.decode())

    def test_client_argv_pinning(self):
        import importlib
        client = importlib.import_module("client")
        self.assertEqual(client.KNOWN_HOSTS, "/etc/pixel-release-operator/known_hosts")
        self.assertEqual(client.HOST, "127.0.0.1")
        self.assertEqual(client.PORT, "22")
        self.assertEqual(client.OPERATOR_SSH, "/usr/bin/ssh")
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_bytes(b"private")
            os.chmod(key, 0o600)
            kh = Path(directory) / "known_hosts"
            kh.write_bytes(b"127.0.0.1 ssh-ed25519 AAAA test\n")
            os.chmod(kh, 0o600)
            client.KNOWN_HOSTS = str(kh)
            real_lstat = os.lstat
            overrides = {str(kh): (None, 0)}
            with mock.patch.object(os, "lstat", side_effect=lstat_overrides(real_lstat, overrides)):
                argv = client.build_argv("pixel-release-transport", str(key), "22", ["status"])
                self.assertEqual(argv[0], "/usr/bin/ssh")
                joined = " ".join(argv)
                self.assertIn("-i", argv)
                self.assertIn("IdentitiesOnly=yes", argv)
                self.assertIn("BatchMode=yes", argv)
                self.assertIn("ForwardAgent=no", argv)
                self.assertIn("ForwardX11=no", argv)
                self.assertIn("ControlMaster=no", argv)
                self.assertIn("ControlPath=none", argv)
                self.assertIn("ProxyCommand=none", argv)
                self.assertIn("ProxyJump=none", argv)
                self.assertIn("LocalCommand=none", argv)
                self.assertIn("PermitLocalCommand=no", argv)
                self.assertIn("UpdateHostKeys=no", argv)
                self.assertIn("StrictHostKeyChecking=yes", argv)
                self.assertNotIn("StrictHostKeyChecking=no", joined)
                # No user/system SSH config may influence the fixed transport: -F /dev/null.
                self.assertEqual(argv[argv.index("-F") + 1], "/dev/null")
                self.assertIn("-F /dev/null", joined)
                self.assertIn("UserKnownHostsFile=" + str(kh), argv)
                self.assertIn("GlobalKnownHostsFile=" + str(kh), argv)
                self.assertEqual(argv[argv.index("-p") + 1], "22")
                self.assertEqual(argv[-2], grammar.HELPER)
                self.assertEqual(argv[-1], "status")
                self.assertEqual(joined.count("pixel-release-transport@127.0.0.1"), 1)
                original = " ".join(argv[argv.index("pixel-release-transport@127.0.0.1") + 1:])
                tokens = original.split()
                self.assertEqual(tokens[0], grammar.HELPER)
                grammar.validate_operation(tokens[1:])
                # Alternate ports (including a syntactically valid one) are rejected.
                for bad_port in ("99999", "2222", "80"):
                    with self.assertRaises(client.ClientError):
                        client.build_argv("pixel-release-transport", str(key), bad_port, ["status"])
                # A permissive (group/world-readable) key must be rejected. Report unsafe
                # metadata through lstat rather than chmodding the real temp key.
                overrides[str(key)] = (stat.S_IFREG | 0o644, None)
                with self.assertRaises(client.ClientError) as caught:
                    client.build_argv("pixel-release-transport", str(key), "22", ["status"])
                self.assertIn("private key must be mode 0600", str(caught.exception))
                del overrides[str(key)]
                os.remove(key)
                with self.assertRaises(client.ClientError):
                    client.build_argv("pixel-release-transport", str(key), "22", ["status"])
                # known_hosts must exist and not be group/world writable.
                key.write_bytes(b"private")
                os.chmod(key, 0o600)
                os.remove(kh)
                with self.assertRaises(client.ClientError):
                    client.build_argv("pixel-release-transport", str(key), "22", ["status"])
                kh.write_bytes(b"127.0.0.1 ssh-ed25519 AAAA test\n")
                overrides[str(kh)] = (stat.S_IFREG | 0o666, 0)
                with self.assertRaises(client.ClientError) as caught:
                    client.build_argv("pixel-release-transport", str(key), "22", ["status"])
                self.assertIn("known_hosts must not be group or world writable", str(caught.exception))
                del overrides[str(kh)]

    def test_client_exact_argv_is_fully_pinned(self):
        import importlib
        client = importlib.import_module("client")
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_bytes(b"private")
            os.chmod(key, 0o600)
            kh = Path(directory) / "known_hosts"
            kh.write_bytes(b"127.0.0.1 ssh-ed25519 AAAA test\n")
            os.chmod(kh, 0o600)
            client.KNOWN_HOSTS = str(kh)
            real_lstat = os.lstat
            with mock.patch.object(os, "lstat", side_effect=rooted_lstat(real_lstat, kh)):
                argv = client.build_argv("pixel-release-transport", str(key), "22", ["status"])
            self.assertEqual(argv, [
                "/usr/bin/ssh",
                "-F", "/dev/null",
                "-i", str(key),
                "-o", "IdentitiesOnly=yes",
                "-o", "BatchMode=yes",
                "-o", "ForwardAgent=no",
                "-o", "ForwardX11=no",
                "-o", "ControlMaster=no",
                "-o", "ControlPath=none",
                "-o", "ProxyCommand=none",
                "-o", "ProxyJump=none",
                "-o", "LocalCommand=none",
                "-o", "PermitLocalCommand=no",
                "-o", "UpdateHostKeys=no",
                "-o", "StrictHostKeyChecking=yes",
                "-o", "UserKnownHostsFile=" + str(kh),
                "-o", "GlobalKnownHostsFile=" + str(kh),
                "-o", "LogLevel=ERROR",
                "-o", "ConnectTimeout=15",
                "-p", "22",
                "pixel-release-transport@127.0.0.1",
                "/usr/local/libexec/pixel-release-managed",
                "status",
            ])

    def test_known_hosts_must_be_root_owned(self):
        import importlib
        client = importlib.import_module("client")
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_bytes(b"private")
            os.chmod(key, 0o600)
            # Owner-owned 0600 file (this test runs as a non-root user) must be rejected:
            # it could otherwise be chmodded/rewritten by its owner despite the documented
            # root-owned trust anchor.
            kh = Path(directory) / "known_hosts"
            kh.write_bytes(b"127.0.0.1 ssh-ed25519 AAAA test\n")
            os.chmod(kh, 0o600)
            self.assertNotEqual(kh.stat().st_uid, 0)
            client.KNOWN_HOSTS = str(kh)
            with self.assertRaises(client.ClientError) as caught:
                client.build_argv("pixel-release-transport", str(key), "22", ["status"])
            self.assertIn("root-owned", str(caught.exception))

    def test_client_ssh_absolute_and_timeout(self):
        import importlib
        client = importlib.import_module("client")
        self.assertEqual(client.OPERATOR_SSH, "/usr/bin/ssh")
        self.assertGreater(client.CLIENT_TIMEOUT, 0)
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_bytes(b"private")
            os.chmod(key, 0o600)
            kh = Path(directory) / "known_hosts"
            kh.write_bytes(b"127.0.0.1 ssh-ed25519 AAAA test\n")
            os.chmod(kh, 0o600)
            client.KNOWN_HOSTS = str(kh)
            real_lstat = os.lstat
            with mock.patch.object(os, "lstat", side_effect=rooted_lstat(real_lstat, kh)):
                argv = client.build_argv("pixel-release-transport", str(key), "22", ["status"])
            # A hard timeout must be treated as failure, not an indefinite hang.
            with mock.patch.object(client.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=1)):
                with self.assertRaises(client.ClientError) as caught:
                    client.run_ssh(argv)
                self.assertIn("timeout", str(caught.exception).lower())

    def test_grammar_rejects_arbitrary_operations(self):
        for tokens in (
            ["deploy", "activate", "app", "v2"],
            ["unit", "install", "gateway"],
            ["systemctl", "preset", "gateway"],
            ["probe", "evil"],
            ["unit", "install", "evil", "0" * 64],
            ["--validate-staging-config", "/etc/pixel-release-operator", "/tmp/templates"],
        ):
            with self.assertRaises(grammar.GrammarError):
                grammar.validate_operation(tokens)


class ManagedValidationDirectTests(unittest.TestCase):
    """Direct unit tests against managed.validate_unit with production gating enabled."""

    def test_production_destination_and_template_path_rules(self):
        real_testing = managed.TESTING
        managed.TESTING = False
        try:
            good = {
                "name": "openclaw-gateway.service",
                "destination": "/etc/systemd/system/openclaw-gateway.service",
                "template": "/etc/pixel-release-operator/templates/openclaw-gateway.service",
                "templateSha256": "0" * 64,
            }
            managed.validate_unit(good, "gateway")
            bad_dest = dict(good, destination="/etc/systemd/system/other.service")
            with self.assertRaises(managed.ManagedError):
                managed.validate_unit(bad_dest, "gateway")
            bad_template = dict(good, template="/tmp/evil.service")
            with self.assertRaises(managed.ManagedError):
                managed.validate_unit(bad_template, "gateway")
            with_prior = dict(good, priorTemplate="/etc/pixel-release-operator/templates/openclaw-gateway.service.prior", priorTemplateSha256="1" * 64)
            managed.validate_unit(with_prior, "gateway")
            bad_prior = dict(with_prior, priorTemplate="/var/lib/evil.prior")
            with self.assertRaises(managed.ManagedError):
                managed.validate_unit(bad_prior, "gateway")
        finally:
            managed.TESTING = real_testing


class StagingValidationTests(unittest.TestCase):
    """Root staging validation must reject hardlinked config/templates while keeping the
    stable no-follow secure read. Exercised directly with geteuid mocked to 0."""

    def _staging(self, directory):
        root = Path(directory)
        templates = root / "templates"; templates.mkdir()
        gateway = templates / "gateway.template"; gateway.write_bytes(b"g\n"); gateway.chmod(0o644)
        courier = templates / "courier.template"; courier.write_bytes(b"c\n"); courier.chmod(0o644)
        config = {
            "schemaVersion": 1,
            "units": {
                "gateway": {"name": "gateway.service", "destination": "/etc/systemd/system/gateway.service", "template": str(templates / "gateway.template"), "templateSha256": sha(b"g\n")},
                "courier": {"name": "courier.service", "destination": "/etc/systemd/system/courier.service", "template": str(templates / "courier.template"), "templateSha256": sha(b"c\n")},
            },
            "readers": {},
            "receiptRoot": str(root / "receipts"),
        }
        config_path = root / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        config_path.chmod(0o644)
        return config_path, templates

    def test_valid_staging_accepted_when_root(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path, templates = self._staging(directory)
            with mock.patch.object(managed.os, "geteuid", return_value=0):
                self.assertEqual(managed.validate_staging_config(str(config_path), str(templates)), 0)

    def test_hardlinked_staging_config_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path, templates = self._staging(directory)
            os.link(config_path, Path(directory) / "config.hardlink")
            with mock.patch.object(managed.os, "geteuid", return_value=0):
                with self.assertRaises(managed.ManagedError) as caught:
                    managed.validate_staging_config(str(config_path), str(templates))
            self.assertIn("must not be a hardlink", str(caught.exception))

    def test_hardlinked_staging_template_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path, templates = self._staging(directory)
            os.link(templates / "gateway.template", templates / "gateway.hardlink")
            with mock.patch.object(managed.os, "geteuid", return_value=0):
                with self.assertRaises(managed.ManagedError) as caught:
                    managed.validate_staging_config(str(config_path), str(templates))
            self.assertIn("must not be a hardlink", str(caught.exception))


class EntrypointStaticTests(unittest.TestCase):
    """Privileged Python entrypoints must use a fixed interpreter, not env."""

    def test_privileged_entrypoints_use_fixed_python3(self):
        for path in (MANAGED, DISPATCH):
            first = path.read_text().splitlines()[0]
            self.assertEqual(first, "#!/usr/bin/python3", path.name)
        # The owner-side client is not privileged and may keep the portable env shebang.
        self.assertEqual(CLIENT.read_text().splitlines()[0], "#!/usr/bin/env python3")


class ConcurrencyAndProvisioningTests(unittest.TestCase):
    def run_managed(self, config_path, *args, env=None):
        environment = {
            **os.environ,
            "PIXEL_RELEASE_MANAGED_CONFIG": str(config_path),
            "PIXEL_RELEASE_MANAGED_TESTING": "1",
        }
        if env:
            environment.update(env)
        return subprocess.Popen(
            [sys.executable, str(MANAGED), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment,
        )

    def test_concurrent_operations_are_serialized_by_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest_dir = root / "etc"; dest_dir.mkdir()
            config_path, config = write_config(root, templates={"gateway": {"bytes": b"x\n", "destination": dest_dir / "gateway.service"}})
            lock_path = Path(config["receiptRoot"]).parent / "operator.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                process = self.run_managed(config_path, "status")
                time.sleep(0.4)
                self.assertIsNone(process.poll(), "managed operation proceeded while the lock was held")
                fcntl.flock(fd, fcntl.LOCK_UN)
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, stderr.decode())
                self.assertIn("status", stdout.decode())
            finally:
                os.close(fd)

    def test_provision_script_static_contract(self):
        text = PROVISION.read_text()
        self.assertIn('Cmnd_Alias PIXEL_RELEASE_MANAGED = \\', text)
        self.assertIn('ALL=(root) NOPASSWD: PIXEL_RELEASE_MANAGED', text)
        self.assertIn('owner-sudo=password-backed-only', text)
        self.assertIn('restrict,command="/usr/local/libexec/pixel-release-dispatch"', text)
        self.assertIn('/usr/sbin/visudo -cf', text)
        self.assertNotIn('ALL=(ALL) NOPASSWD', text)
        # Transport shell must be the fixed /bin/sh (sshd runs forced commands via -c).
        self.assertIn('--shell /bin/sh', text)
        self.assertNotIn('--shell /usr/sbin/nologin', text)
        self.assertIn('Transport login shell is not the fixed /bin/sh', text)
        # Shared grammar module is required and installed beside both helpers.
        self.assertIn('pixel_release_grammar.py', text)
        self.assertIn('install -o root -g root -m 0644 "$staging/pixel_release_grammar.py" /usr/local/libexec/pixel_release_grammar.py', text)
        # Immutability relies on root ownership/mode; no best-effort chattr +i.
        self.assertNotIn('chattr', text)
        # Safe staging validation flow is present and is NOT part of the sudoers grammar.
        self.assertIn('--validate-staging-config', text)
        self.assertIn('/usr/bin/python3 /usr/local/libexec/pixel-release-managed --validate-config', text)
        self.assertIn('/usr/bin/python3 "$staging/managed.py" --validate-staging-config', text)
        # Security-sensitive account/provision tools use fixed absolute Debian paths so a
        # hostile PATH entry can never steer them on either supported host.
        self.assertIn('/usr/bin/passwd', text)
        self.assertIn('/usr/sbin/usermod', text)
        # usermod lives at /usr/sbin on Debian; /usr/bin/usermod must never be invoked or
        # even referenced (exact absence), so a wrong-PATH binary can never be reached.
        self.assertNotIn('/usr/bin/usermod', text)
        self.assertIn('/usr/bin/id', text)
        # Fail-closed transport validation must precede the first mutation (groupadd):
        # UID 0, dedicated GID 0, and any privileged group (root/sudo/wheel) are refused
        # before any groupadd/useradd/usermod, home, file, or password change.
        self.assertIn('reject_unsafe_transport() {', text)
        self.assertIn('if entry=$(getent passwd "$t"); then', text)
        self.assertIn("t_uid=$(printf '%s' \"$entry\" | cut -d: -f3)", text)
        self.assertIn('if entry=$(getent group "$t"); then', text)
        self.assertIn("t_gid=$(printf '%s' \"$entry\" | cut -d: -f3)", text)
        self.assertIn("grep -Eq '^(root|sudo|wheel)$'", text)
        self.assertIn("Refusing: transport '$t' resolves to UID 0", text)
        self.assertIn("Refusing: transport group '$t' resolves to GID 0", text)
        self.assertIn('reject_unsafe_transport "$transport"', text)
        self.assertLess(text.index('reject_unsafe_transport "$transport"'), text.index('groupadd --system'))
        self.assertLess(text.index('groupadd --system'), text.index('useradd --system'))
        self.assertLess(text.index('useradd --system'), text.index('/usr/sbin/usermod --gid'))
        # Host-key pinning is provisioned from the exact local Ed25519 host key.
        self.assertIn('/etc/pixel-release-operator/known_hosts', text)
        self.assertIn('127.0.0.1 %s %s', text)
        self.assertIn('ssh_host_ed25519_key.pub', text)
        # The pinned host public key must be root-owned mode 0644 before it is trusted.
        self.assertIn('Local Ed25519 host public key is not root-owned mode 0644', text)
        self.assertIn("stat -c '%U:%G:%a' \"$host_key_file\"", text)
        # Post-copy staged-to-installed SHA-256 equality must gate the sudoers grant.
        self.assertIn('Staged-to-installed integrity check failed', text)
        self.assertIn('Integrity mismatch', text)
        self.assertIn('sha256sum', text)
        self.assertGreater(text.index('sha256sum'), text.index('install -o root -g root -m 0644 "$known_tmp"'))
        self.assertLess(text.index('Staged-to-installed integrity check failed'), text.index('sudoers_temporary=$(mktemp)'))
        # The installed config/helper must be validated BEFORE the sudoers grant so a
        # post-grant validation failure can never leave a live partial grant.
        self.assertLess(text.index('Staged-to-installed integrity check failed'), text.index('--validate-config'))
        self.assertLess(text.index('--validate-config'), text.index('sudoers_temporary=$(mktemp)'))
        self.assertLess(text.index('sudoers_temporary=$(mktemp)'), text.index('/usr/sbin/visudo -cf'))
        # The transport password is locked and supplementary groups cleared to a single
        # primary unprivileged group while key-based forced-command operation is retained.
        self.assertIn('/usr/bin/passwd -l "$transport"', text)
        self.assertIn('/usr/sbin/usermod -G "" "$transport"', text)
        # A pre-existing account's primary gid is normalized to the dedicated transport
        # group (idempotent usermod --gid), then verified as exactly that one group.
        self.assertIn('/usr/sbin/usermod --gid "$transport_group"', text)
        self.assertIn('Transport primary gid is not the dedicated transport group', text)
        self.assertIn('Transport identity has unexpected supplementary groups', text)
        # The lock must be verified (passwd -S 'L' or Debian-equivalent locked marker)
        # immediately after locking and before clearing/verifying groups: fail closed.
        self.assertIn('password_status=$(/usr/bin/passwd -S "$transport"', text)
        self.assertIn('Transport password is not verified locked', text)
        self.assertLess(text.index('/usr/bin/passwd -l "$transport"'), text.index('password_status=$(/usr/bin/passwd -S "$transport"'))
        self.assertLess(text.index('password_status=$(/usr/bin/passwd -S "$transport"'), text.index('/usr/sbin/usermod -G "" "$transport"'))
        self.assertLess(text.index('/usr/sbin/usermod --gid "$transport_group"'), text.index('/usr/sbin/usermod -G "" "$transport"'))
        self.assertLess(text.index('Transport primary gid is not the dedicated transport group'), text.index('Transport identity has unexpected supplementary groups'))
        self.assertIn('restrict,command="/usr/local/libexec/pixel-release-dispatch"', text)
        # Every sudoers command line must correspond to a valid grammar operation.
        seen = 0
        for line in text.splitlines():
            stripped = line.strip().rstrip("\\").rstrip(",").strip()
            marker = f"{grammar.HELPER} "
            if not stripped.startswith(marker):
                continue
            rest = stripped[len(marker):].replace(",", "").replace("\\", "").strip()
            if " *" in rest or rest.endswith("*"):
                rest = rest.replace(" *", " " + "0" * 64).replace("*", "0" * 64)
            tokens = rest.split()
            grammar.validate_operation(tokens)
            seen += 1
        self.assertGreaterEqual(seen, 20)

    def _provision_reject_func(self):
        """Extract the reject_unsafe_transport function body from the provisioning script."""
        text = PROVISION.read_text()
        marker = "reject_unsafe_transport() {"
        start = text.index(marker)
        line_start = text.rfind("\n", 0, start) + 1
        lines = text[line_start:].splitlines()
        body = [lines[0]]
        for line in lines[1:]:
            body.append(line)
            if line.strip() == "}":
                break
        return "\n".join(body)

    def test_provision_rejects_root_before_any_mutation(self):
        func = self._provision_reject_func()
        self.assertTrue(func.startswith("reject_unsafe_transport() {"))
        # The dedicated fail-closed validation must reject `root` (UID 0 / GID 0) on its
        # own, before any groupadd/useradd/usermod/home/file/password mutation.
        script = f"{func}\nreject_unsafe_transport root; echo rc=$?\n"
        completed = subprocess.run(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.decode().strip(), "rc=2")
        self.assertIn("UID 0", completed.stderr.decode())
        # The validation call must precede the first mutation (groupadd) in the script.
        text = PROVISION.read_text()
        self.assertLess(text.index('reject_unsafe_transport "$transport"'), text.index("groupadd --system"))

    def test_provision_safe_absent_name_succeeds_under_set_euo_pipefail(self):
        func = self._provision_reject_func()
        self.assertTrue(func.startswith("reject_unsafe_transport() {"))
        # A guaranteed-absent safe name must be accepted even when the function runs under
        # `set -euo pipefail` (the same strict mode the provisioner uses globally). The
        # old getent assignment pipelines would abort on the nonzero lookup for a
        # genuinely new identity before user creation; the rewritten if/getent form must
        # treat absence as an explicit safe path instead.
        script = f"set -euo pipefail\n{func}\nreject_unsafe_transport pxrelabsent; echo rc=$?\n"
        ok = subprocess.run(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(ok.stdout.decode().strip(), "rc=0")
        self.assertEqual(ok.stderr.decode(), "")


    def _run_reject_with_fake_getent(self, *, passwd_rc=2, group_rc=2, passwd_out="", group_out=""):
        """Run reject_unsafe_transport against a controllable fake getent.

        The fake getent returns FAKE_GETENT_*_RC (and, on rc=0, prints
        FAKE_GETENT_*_OUT) per database, so these tests never depend on host NSS.
        """
        func = self._provision_reject_func()
        self.assertTrue(func.startswith("reject_unsafe_transport() {"))
        with tempfile.TemporaryDirectory() as directory:
            bindir = Path(directory) / "bin"
            bindir.mkdir()
            fake = bindir / "getent"
            fake.write_text(
                "#!/usr/bin/env bash\n"
                'db="$1"\n'
                'case "$db" in\n'
                '  passwd) rc="${FAKE_GETENT_PASSWD_RC:-0}"; out="${FAKE_GETENT_PASSWD_OUT:-}";;\n'
                '  group) rc="${FAKE_GETENT_GROUP_RC:-0}"; out="${FAKE_GETENT_GROUP_OUT:-}";;\n'
                '  *) exit 2;;\n'
                'esac\n'
                'if [[ "$rc" == 0 ]]; then printf "%s\\n" "$out"; fi\n'
                'exit "$rc"\n'
            )
            os.chmod(fake, 0o700)
            env_vars = (
                f"FAKE_GETENT_PASSWD_RC={passwd_rc} FAKE_GETENT_GROUP_RC={group_rc} "
                f"FAKE_GETENT_PASSWD_OUT={shlex.quote(passwd_out)} "
                f"FAKE_GETENT_GROUP_OUT={shlex.quote(group_out)} "
                f"PATH={bindir}:$PATH"
            )
            # Capture a nonzero reject rc without letting `set -e` abort the script;
            # the `|| rc=$?` idiom records the function status while staying -e-safe.
            script = f"set -euo pipefail\n{func}\nrc=0\n{env_vars} reject_unsafe_transport pxtest || rc=$?\necho rc=$rc\n"
            return subprocess.run(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_provision_fake_getent_rc2_absent_accepted_for_both_lookups(self):
        completed = self._run_reject_with_fake_getent(passwd_rc=2, group_rc=2)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.decode().strip(), "rc=0")
        self.assertEqual(completed.stderr.decode(), "")

    def test_provision_fake_getent_passwd_nonzero_rejected(self):
        for rc in (1, 3):
            completed = self._run_reject_with_fake_getent(passwd_rc=rc, group_rc=2)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            self.assertEqual(completed.stdout.decode().strip(), "rc=2")
            self.assertIn(f"NSS lookup for transport user 'pxtest' failed (rc={rc})", completed.stderr.decode())

    def test_provision_fake_getent_group_nonzero_rejected(self):
        for rc in (1, 3):
            completed = self._run_reject_with_fake_getent(passwd_rc=2, group_rc=rc)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            self.assertEqual(completed.stdout.decode().strip(), "rc=2")
            self.assertIn(f"NSS lookup for transport group 'pxtest' failed (rc={rc})", completed.stderr.decode())

    def test_provision_fake_getent_malformed_numeric_records_rejected(self):
        malformed_passwd = self._run_reject_with_fake_getent(
            passwd_rc=0, passwd_out="pxtest:x:notanumber:5::/:/bin/sh", group_rc=2
        )
        self.assertEqual(malformed_passwd.stdout.decode().strip(), "rc=2")
        self.assertIn("malformed numeric UID", malformed_passwd.stderr.decode())
        malformed_group = self._run_reject_with_fake_getent(
            passwd_rc=2, group_rc=0, group_out="pxtest:x:notanumber:"
        )
        self.assertEqual(malformed_group.stdout.decode().strip(), "rc=2")
        self.assertIn("malformed numeric GID", malformed_group.stderr.decode())


class VerifyLimbRegressionTests(unittest.TestCase):
    def run_bash(self, script):
        return subprocess.run(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_verify_limbs_retain_exact_original_fallback_probes_and_route(self):
        verify = (ROOT / "scripts/verify.sh").read_text()
        ops_originals = [
            '! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/private" || pixel_die "Gateway owner can read Operations private state"',
            '! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/.ssh" || pixel_die "Gateway owner can read Operations SSH state"',
            '! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_POLICY_PATH" || pixel_die "Gateway owner can read Operations policy"',
            '! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/plans" || pixel_die "Gateway owner can read immutable Operations plans"',
            '! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/approvals" || pixel_die "Gateway owner can read Operations approvals"',
        ]
        frontier_originals = [
            'pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" w "$PIXEL_FRONTIER_REQUEST_DIR" || pixel_die "Gateway owner cannot publish Frontier requests"',
            'pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_RESULT_DIR" || pixel_die "Gateway owner cannot read Frontier results"',
            '! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_CREDENTIAL_PATH" || pixel_die "Gateway owner can read Frontier provider credential"',
            '! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_POLICY_PATH" || pixel_die "Gateway owner can read Frontier policy"',
            'pixel_user_can_access "$PIXEL_FRONTIER_BROKER_USER" w "$PIXEL_FRONTIER_CREDENTIAL_PATH" || pixel_die "Frontier worker cannot refresh its ChatGPT auth cache"',
            '! pixel_user_can_access "$PIXEL_FRONTIER_BROKER_USER" w "$PIXEL_FRONTIER_BROKER_STATE_DIR/private" || pixel_die "Frontier worker can replace its ChatGPT auth directory"',
            '! pixel_user_can_access "$PIXEL_FRONTIER_BROKER_USER" w "$(dirname "$PIXEL_FRONTIER_CREDENTIAL_PATH")" || pixel_die "Frontier worker can replace its API key"',
            '! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_BROKER_STATE_DIR/$directory" || pixel_die "Gateway owner can read Frontier $directory state"',
        ]
        for probe in ops_originals:
            self.assertIn(probe, verify)
        for probe in frontier_originals:
            self.assertIn(probe, verify)
        # Both limbs must route to the operator probe when enabled, and the else branch must
        # retain the exact original sudo fallback probes.
        self.assertEqual(verify.count("if pixel_release_operator_enabled; then"), 2)
        self.assertEqual(verify.count('pixel_release_operator_probe ops "Gateway owner can read Operations authority state"'), 1)
        self.assertEqual(verify.count('pixel_release_operator_probe frontier "Gateway owner can read Frontier authority state"'), 1)
        # Both gateway-env leak checks must remain outside the routing branch.
        self.assertIn('^PIXEL_OPS_(POLICY_PATH|SSH|PRIVATE|CREDENTIAL)=', verify)
        self.assertIn('^PIXEL_FRONTIER_(POLICY|CREDENTIAL)', verify)
        self.assertIn('Gateway environment exposes Operations authority', verify)
        self.assertIn('Gateway environment exposes Frontier authority or credentials', verify)

    def test_common_sh_probe_routing(self):
        root = ROOT
        script = (
            f'source {root}/scripts/lib/common.sh; '
            f'export PIXEL_RELEASE_OPERATOR_ENABLED=1; '
            f'pixel_release_operator_client() {{ printf "%s\\n" "$*"; }}; '
            f'pixel_release_operator_probe ops "message"; '
            f'pixel_release_operator_probe frontier "message"'
        )
        completed = self.run_bash(script)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(completed.stdout.decode().splitlines(), ["probe ops", "probe frontier"])

    def test_preflight_limbs_route_enabled_operator_and_retain_sudo_fallback(self):
        preflight = (ROOT / "scripts/preflight.sh").read_text()
        for probe in (
            '! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/private"',
            '! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_POLICY_PATH"',
            '! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_CREDENTIAL_PATH"',
            '! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_POLICY_PATH"',
            '! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_BROKER_STATE_DIR/plans"',
        ):
            self.assertIn(probe, preflight)
        self.assertEqual(preflight.count("if pixel_release_operator_enabled; then"), 2)
        self.assertEqual(
            preflight.count('pixel_release_operator_probe ops "Gateway owner can read Operations authority state"'), 1
        )
        self.assertEqual(
            preflight.count('pixel_release_operator_probe frontier "Gateway owner can read Frontier authority state"'), 1
        )


class FallbackRegressionTests(unittest.TestCase):
    def run_bash(self, script):
        return subprocess.run(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_common_sh_retains_sudo_fallback_and_operator_gate(self):
        root = ROOT
        disabled = self.run_bash(
            f'source {root}/scripts/lib/common.sh; if pixel_release_operator_enabled; then echo enabled; else echo disabled; fi'
        )
        self.assertEqual(disabled.returncode, 0, disabled.stderr.decode())
        self.assertEqual(disabled.stdout.decode().strip(), "disabled")
        enabled = self.run_bash(
            f'export PIXEL_RELEASE_OPERATOR_ENABLED=1; source {root}/scripts/lib/common.sh; '
            f'if pixel_release_operator_enabled; then echo enabled; else echo disabled; fi'
        )
        self.assertEqual(enabled.returncode, 0, enabled.stderr.decode())
        self.assertEqual(enabled.stdout.decode().strip(), "enabled")
        common = (root / "scripts/lib/common.sh").read_text()
        for fragment in ("sudo systemctl", "sudo install", "sudo rm -f", "pixel_release_operator_enabled"):
            self.assertIn(fragment, common)

    def test_release_operator_preflight_is_disabled_by_default_and_probes_when_enabled(self):
        root = ROOT
        disabled = self.run_bash(
            f'source {root}/scripts/lib/common.sh; '
            f'pixel_release_operator_client() {{ echo unexpected; return 99; }}; '
            f'pixel_release_operator_preflight; echo disabled-ok'
        )
        self.assertEqual(disabled.returncode, 0, disabled.stderr.decode())
        self.assertEqual(disabled.stdout.decode().strip(), "disabled-ok")
        enabled = self.run_bash(
            f'source {root}/scripts/lib/common.sh; '
            f'export PIXEL_RELEASE_OPERATOR_ENABLED=1; '
            f'export PIXEL_RELEASE_OPERATOR_USER=pixel-release-transport; '
            f'export PIXEL_RELEASE_OPERATOR_KEY=/private/operator-key; '
            f'pixel_release_operator_client() {{ printf "%s\\n" "$*" >&2; }}; '
            f'pixel_release_operator_preflight; echo enabled-ok'
        )
        self.assertEqual(enabled.returncode, 0, enabled.stderr.decode())
        self.assertEqual(enabled.stdout.decode().strip(), "enabled-ok")
        self.assertEqual(enabled.stderr.decode().strip(), "status")
        missing = self.run_bash(
            f'source {root}/scripts/lib/common.sh; '
            f'export PIXEL_RELEASE_OPERATOR_ENABLED=1; '
            f'unset PIXEL_RELEASE_OPERATOR_USER PIXEL_RELEASE_OPERATOR_KEY; '
            f'pixel_release_operator_preflight'
        )
        self.assertNotEqual(missing.returncode, 0)
        failed = self.run_bash(
            f'source {root}/scripts/lib/common.sh; '
            f'export PIXEL_RELEASE_OPERATOR_ENABLED=1; '
            f'export PIXEL_RELEASE_OPERATOR_USER=pixel-release-transport; '
            f'export PIXEL_RELEASE_OPERATOR_KEY=/private/operator-key; '
            f'pixel_release_operator_client() {{ return 23; }}; '
            f'pixel_release_operator_preflight'
        )
        self.assertNotEqual(failed.returncode, 0)

    def test_operator_routes_systemctl_by_which_and_validates_unit(self):
        root = ROOT
        script = (
            f'source {root}/scripts/lib/common.sh; '
            f'export PIXEL_RELEASE_OPERATOR_ENABLED=1; '
            f'export PIXEL_SYSTEMD_UNIT=openclaw-gateway.service; '
            f'export PIXEL_WEB_COURIER_UNIT=pixel-web-courier.service; '
            f'pixel_release_operator_client() {{ printf "%s\\n" "$*"; }}; '
            f'pixel_systemctl restart "$PIXEL_SYSTEMD_UNIT"; '
            f'pixel_courier_systemctl disable --now "$PIXEL_WEB_COURIER_UNIT"'
        )
        completed = self.run_bash(script)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        lines = completed.stdout.decode().splitlines()
        self.assertEqual(lines, ["systemctl restart gateway", "systemctl disable courier --now"])
        mismatch = (
            f'source {root}/scripts/lib/common.sh; '
            f'export PIXEL_RELEASE_OPERATOR_ENABLED=1; '
            f'export PIXEL_SYSTEMD_UNIT=openclaw-gateway.service; '
            f'pixel_release_operator_client() {{ printf "%s\\n" "$*"; }}; '
            f'pixel_systemctl restart not-the-unit'
        )
        bad = self.run_bash(mismatch)
        self.assertNotEqual(bad.returncode, 0)

    def test_operator_disabled_uses_systemctl_bin_fallback(self):
        root = ROOT
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "fake-systemctl"
            fake.write_text("#!/usr/bin/env bash\nprintf 'fallback-ran\\n'\n")
            os.chmod(fake, 0o700)
            script = (
                f"source {root}/scripts/lib/common.sh; "
                f"PIXEL_SYSTEMCTL_BIN={fake} PIXEL_COURIER_SYSTEMCTL_BIN={fake} "
                f"PIXEL_RELEASE_OPERATOR_ENABLED=0; "
                f"pixel_systemctl is-active openclaw-gateway.service; "
                f"pixel_courier_systemctl is-active pixel-web-courier.service"
            )
            completed = self.run_bash(script)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            self.assertEqual(completed.stdout.decode().count("fallback-ran"), 2)


if __name__ == "__main__":
    unittest.main()
