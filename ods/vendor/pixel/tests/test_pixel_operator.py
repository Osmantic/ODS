import contextlib
import copy
import errno
import hashlib
import importlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "deploy/release-operator"
DISPATCH = PKG / "dispatch.py"
sys.path.insert(0, str(PKG))

# The thin operator must be imported with TESTING on (non-root) so ownership checks are
# relaxed; the config path is supplied per-test via PIXEL_OPERATOR_CONFIG.
os.environ["PIXEL_RELEASE_MANAGED_TESTING"] = "1"
os.environ["PIXEL_RELEASE_DISPATCH_TESTING"] = "1"
import pixel_release_grammar as grammar  # noqa: E402
import pixel_operator as pop  # noqa: E402
managed_mod = importlib.import_module("managed")  # noqa: E402
client_mod = importlib.import_module("client")  # noqa: E402


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def broker_bytes_config(root: Path):
    return {
        "backupRoot": str(root / "broker-bytes"),
        "limbs": {
            "source": {"enabled": True, "installDir": str(root / "source-broker"), "directPathEnabled": False},
            "ops": {"enabled": True, "installDir": str(root / "ops-broker")},
            "frontier": {"enabled": False, "installDir": str(root / "frontier-broker")},
        },
    }


def write_pixel_config(root: Path, **over):
    config = {
        "schemaVersion": 1,
        "ownerUid": 1000,
        "pixelWorkUid": 1001,
        "pixelWorkGid": 1001,
        "inboxRoot": str(root / "inbox"),
        "bundleRoot": str(root / "bundles"),
        "runtimePath": str(root / "runtime"),
        "runtimeTreeSha256": "0" * 64,
        "nodePath": "/usr/bin/node",
        "systemctlPath": "/usr/bin/systemctl",
        "systemdAnalyzePath": "/usr/bin/systemd-analyze",
        "receiptRoot": str(root / "receipts"),
        "rebootRoot": str(root / "reboot"),
        "brokerBytes": broker_bytes_config(root),
        "readersEnabled": False,
    }
    config.update(over)
    path = root / "pixel-config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path, config


def set_config(root: Path):
    path, config = write_pixel_config(root)
    os.environ["PIXEL_OPERATOR_CONFIG"] = str(path)
    return path, config


def make_inbox_bundle(root: Path, kind: str, manifest_bytes: bytes, extra=None, manifest_name="service-bundle.json"):
    """Create <inboxRoot>/<kind>/<sha>/ with the given manifest + optional units."""
    inbox = root / "inbox" / kind / sha(manifest_bytes)
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / manifest_name).write_bytes(manifest_bytes)
    for name, data in (extra or {}).items():
        (inbox / name).write_bytes(data)
    for p in inbox.iterdir():
        p.chmod(0o600)
    return inbox


def write_state_systemctl(root: Path, *, active_code=0, enabled_code=0):
    """A fake systemctl with distinct expected/inactive/error codes per verb."""
    script = root / "fake-state-systemctl"
    lines = [
        "#!/bin/sh",
        'case "$1" in',
        "  is-enabled)",
        f'    exit "$ENABLED_CODE"',
        "    ;;",
        "  is-active)",
        f'    exit "$ACTIVE_CODE"',
        "    ;;",
        "  *) exit 0 ;;",
        "esac",
    ]
    content = "\n".join(lines) + "\n"
    content = content.replace("$ACTIVE_CODE", str(active_code)).replace("$ENABLED_CODE", str(enabled_code))
    script.write_text(content)
    script.chmod(0o755)
    os.environ["PIXEL_RELEASE_MANAGED_SYSTEMCTL"] = str(script)
    return script


def write_fake_systemctl(root: Path, *, active=True, enabled=True):
    """A fixed fake systemctl embedding literal enabled/active answers (no env reliance)."""
    script = root / "fake-systemctl"
    active_v = "0" if active else "1"
    enabled_v = "0" if enabled else "1"
    lines = [
        "#!/bin/sh",
        'case "$1" in',
        "  is-enabled)",
        f'    if [ "$2" = --quiet ]; then [ "$ENABLED_V" = 0 ] && exit 0 || exit 1; fi',
        f'    [ "$ENABLED_V" = 0 ] && echo enabled && exit 0 || echo disabled && exit 1',
        "    ;;",
        "  is-active)",
        f'    if [ "$2" = --quiet ]; then [ "$ACTIVE_V" = 0 ] && exit 0 || exit 1; fi',
        f'    [ "$ACTIVE_V" = 0 ] && echo active && exit 0 || echo inactive && exit 1',
        "    ;;",
        "  *) exit 0 ;;",
        "esac",
    ]
    content = "\n".join(lines) + "\n"
    content = content.replace("$ENABLED_V", enabled_v).replace("$ACTIVE_V", active_v)
    script.write_text(content)
    script.chmod(0o755)
    os.environ["PIXEL_RELEASE_MANAGED_SYSTEMCTL"] = str(script)
    return script


def make_runtime_snapshot(root: Path, files: dict):
    """Build a dependency-free runtime snapshot dir with runtime-manifest.json."""
    runtime = root / f"snapshot-{os.urandom(4).hex()}"
    runtime.mkdir()
    manifest = {"schemaVersion": 1, "files": {}}
    for rel, data in files.items():
        p = runtime / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        p.chmod(0o644)
        manifest["files"][rel] = sha(data)
    mb = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    (runtime / "runtime-manifest.json").write_bytes(mb)
    (runtime / "runtime-manifest.json").chmod(0o644)
    return runtime, sha(mb)


def make_reboot_dirs(root: Path):
    """Create the reboot state roots root-private (0700), matching provisioning."""
    for sub in ("evidence", "grants", "intent", "intent/archive", "grants/archive"):
        d = root / "reboot" / sub
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)


def make_harness_config(root: Path, tree_sha: str):
    config = {
        "schemaVersion": 1, "ownerUid": 1000, "pixelWorkUid": 1001, "pixelWorkGid": 1001,
        "inboxRoot": str(root / "inbox"), "bundleRoot": str(root / "bundles"),
        "runtimePath": str(root / "runtime"), "runtimeTreeSha256": tree_sha,
        "nodePath": "/usr/bin/node", "systemctlPath": "/usr/bin/systemctl",
        "systemdAnalyzePath": "/usr/bin/systemd-analyze",
        "receiptRoot": str(root / "receipts"), "rebootRoot": str(root / "reboot"),
        "brokerBytes": broker_bytes_config(root),
        "readersEnabled": False,
    }
    cfg = root / "pixel-config.json"
    cfg.write_text(json.dumps(config))
    return cfg, config

class ConfigValidationTests(unittest.TestCase):
    def test_valid_config_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, config = write_pixel_config(root)
            self.assertEqual(pop.validate_config(config), config)

    def test_missing_key_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, config = write_pixel_config(Path(d))
            del config["rebootRoot"]
            with self.assertRaises(pop.PixelOperatorError):
                pop.validate_config(config)

    def test_extra_key_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, config = write_pixel_config(Path(d))
            config["extra"] = True
            with self.assertRaises(pop.PixelOperatorError):
                pop.validate_config(config)

    def test_root_pixel_work_uid_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, config = write_pixel_config(Path(d), pixelWorkUid=0)
            with self.assertRaises(pop.PixelOperatorError):
                pop.validate_config(config)

    def test_root_owner_uid_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, config = write_pixel_config(Path(d), ownerUid=0)
            with self.assertRaises(pop.PixelOperatorError):
                pop.validate_config(config)

    def test_non_absolute_path_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, config = write_pixel_config(Path(d), inboxRoot="relative/inbox")
            with self.assertRaises(pop.PixelOperatorError):
                pop.validate_config(config)

    def test_bad_runtime_tree_sha_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, config = write_pixel_config(Path(d), runtimeTreeSha256="notasha")
            with self.assertRaises(pop.PixelOperatorError):
                pop.validate_config(config)

    def test_readers_enabled_non_bool_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, config = write_pixel_config(Path(d), readersEnabled="yes")
            with self.assertRaises(pop.PixelOperatorError):
                pop.validate_config(config)

    def test_non_fixed_system_path_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, config = write_pixel_config(Path(d), nodePath="/tmp/node")
            with self.assertRaises(pop.PixelOperatorError):
                pop.validate_config(config)


class GrammarTests(unittest.TestCase):
    def test_valid_bundle_service_reboot_verbs_accepted(self):
        z = "0" * 64
        valid = [
            ["bundle", "install", "goal", z],
            ["bundle", "install", "fleet-goal", z],
            ["bundle", "install", "deep-work-soak", z],
            ["bundle", "inspect", "goal", z],
            ["bundle", "status", "fleet-goal", z],
            ["bundle", "activate", "deep-work-soak", z],
            ["bundle", "remove", "goal", z],
            ["bundle", "rollback", "goal", z, "1" * 64],
            ["service", "status", "goal", z],
            ["reboot", "prepare", "goal", z],
            ["reboot", "prepare", "deep-work-soak", z],
            ["reboot", "execute", z],
            ["reboot", "status"],
            ["reboot", "reconcile"],
            ["broker-bytes", "backup"],
            ["broker-bytes", "install"],
            ["broker-bytes", "restore"],
            ["broker-bytes", "verify"],
        ]
        for op in valid:
            self.assertEqual(grammar.validate_operation(op), op, op)

    def test_broker_bytes_grammar_rejects_caller_authority(self):
        invalid = [
            ["broker-bytes"],
            ["broker-bytes", "cat"],
            ["broker-bytes", "backup", "/tmp/evil"],
            ["broker-bytes", "install", "../../etc/shadow"],
            ["broker-bytes", "verify", "pixel-ops-broker.service"],
            ["broker-bytes", "restore;rm"],
        ]
        for op in invalid:
            with self.assertRaises(grammar.GrammarError, msg=op):
                grammar.validate_operation(op)

    def test_journal_verb_removed(self):
        # The raw-journal reader was removed: service journal must not be accepted.
        z = "0" * 64
        for op in (["service", "journal", "goal", z], ["service", "journal", "deep-work-soak", z]):
            with self.assertRaises(grammar.GrammarError):
                grammar.validate_operation(op)

    def test_invalid_token_counts_rejected(self):
        z = "0" * 64
        invalid = [
            ["bundle", "install", "goal"],                     # missing sha
            ["bundle", "install", "goal", z, z],                # extra token
            ["bundle", "rollback", "goal", z],                  # missing prior sha
            ["service", "status", "goal"],                      # missing sha
            ["reboot", "execute"],                              # missing grant
            ["reboot", "status", z],                            # extra token
            ["bundle", "install", "goal", "a"],                 # short sha
            ["bundle", "install", "goal", "A" * 64],            # uppercase sha
            ["bundle", "install", "goal", "0" * 63],            # 63 hex
        ]
        for op in invalid:
            with self.assertRaises(grammar.GrammarError, msg=str(op)):
                grammar.validate_operation(op)

    def test_kind_is_exact_not_substring(self):
        z = "0" * 64
        # A "goal" request must never match a fleet-goal/deep-work-soak token and vice versa.
        for bad in ("goalist", "fleet", "soak", "deep-work-soakx", "fleetgoal"):
            with self.assertRaises(grammar.GrammarError):
                grammar.validate_operation(["bundle", "install", bad, z])
        # reboot prepare supports only goal/deep-work-soak, never fleet-goal.
        with self.assertRaises(grammar.GrammarError):
            grammar.validate_operation(["reboot", "prepare", "fleet-goal", z])

    def test_injection_rejected(self):
        z = "0" * 64
        for op in [
            ["bundle", "install", "goal", z, ";", "id"],
            ["bundle", "install", "../../etc", z],
            ["service", "status", "goal", "0" * 64, "/etc/passwd"],
            ["bundle", "install", "goal", f"x{z}"],
        ]:
            with self.assertRaises(grammar.GrammarError):
                grammar.validate_operation(op)


class DispatcherTests(unittest.TestCase):
    def run_dispatch(self, command):
        env = {**os.environ, "SSH_ORIGINAL_COMMAND": command, "PIXEL_RELEASE_DISPATCH_TESTING": "1"}
        return subprocess.run([sys.executable, str(DISPATCH)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_new_verbs_routed_to_fixed_helper(self):
        z = "0" * 64
        for command in [
            f"{grammar.HELPER} bundle install goal {z}",
            f"{grammar.HELPER} bundle rollback goal {z} {'1' * 64}",
            f"{grammar.HELPER} reboot prepare goal {z}",
            f"{grammar.HELPER} reboot execute {z}",
            f"{grammar.HELPER} reboot status",
        ]:
            completed = self.run_dispatch(command)
            self.assertEqual(completed.returncode, 0, (command, completed.stderr.decode()))
            evidence = json.loads(completed.stdout)
            self.assertEqual(evidence["routedArgv"][1:3], ["--non-interactive", grammar.HELPER])
            self.assertFalse(evidence["shell"])

    def test_out_of_grammar_commands_rejected(self):
        z = "0" * 64
        for command in [
            f"{grammar.HELPER} bundle install goal",
            f"{grammar.HELPER} bundle install goal {z} ; id",
            f"{grammar.HELPER} bundle install evil {z}",
            f"{grammar.HELPER} systemctl reboot",
            f"{grammar.HELPER} reboot prepare fleet-goal {z}",
            f"{grammar.HELPER} service status goal",
        ]:
            completed = self.run_dispatch(command)
            self.assertNotEqual(completed.returncode, 0, command)


class EndToEndRoutingTests(unittest.TestCase):
    """End-to-end forced-command/client routing for the thin root-operator verbs.

    Proves a new fixed verb is accepted by the owner client grammar, routed by the forced
    command dispatcher, and reaches pixel_operator.run_operation through the updated managed
    helper; an invalid verb still fails at every grammar layer (client, dispatch, managed).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path, self.config = set_config(self.root)
        make_reboot_dirs(self.root)
        self.z = "0" * 64

    def tearDown(self):
        self._tmp.cleanup()

    def run_dispatch(self, command):
        env = {**os.environ, "SSH_ORIGINAL_COMMAND": command, "PIXEL_RELEASE_DISPATCH_TESTING": "1"}
        return subprocess.run([sys.executable, str(DISPATCH)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_new_verb_reaches_run_operation_via_managed(self):
        operation = ["bundle", "install", "goal", self.z]
        captured = {}

        def fake_run_operation(op):
            captured["op"] = list(op)
            return {"operation": "pixel-operator-bundle-install", "state": "ok"}

        buf = io.StringIO()
        with mock.patch.object(pop, "run_operation", side_effect=fake_run_operation), \
             mock.patch.object(sys, "argv", ["managed.py", *operation]), \
             contextlib.redirect_stdout(buf):
            rc = managed_mod.main()
        self.assertEqual(rc, 0)
        self.assertEqual(captured["op"], operation)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["operation"], "pixel-operator-bundle-install")

    def test_broker_bytes_verb_reaches_run_operation_via_managed(self):
        operation = ["broker-bytes", "backup"]
        captured = {}

        def fake_run_operation(op):
            captured["op"] = list(op)
            return {"operation": "pixel-operator-broker-bytes-backup", "state": "backed-up"}

        buf = io.StringIO()
        with mock.patch.object(pop, "run_operation", side_effect=fake_run_operation), \
             mock.patch.object(sys, "argv", ["managed.py", *operation]), \
             contextlib.redirect_stdout(buf):
            rc = managed_mod.main()
        self.assertEqual(rc, 0)
        self.assertEqual(captured["op"], operation)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["operation"], "pixel-operator-broker-bytes-backup")

    def test_dispatch_routes_new_verb_to_fixed_helper(self):
        command = f"{grammar.HELPER} reboot prepare goal {self.z}"
        completed = self.run_dispatch(command)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        evidence = json.loads(completed.stdout)
        self.assertEqual(evidence["routedArgv"], ["/usr/bin/sudo", "--non-interactive", grammar.HELPER, "reboot", "prepare", "goal", self.z])

    def test_invalid_verb_fails_at_every_grammar_layer(self):
        invalid = ["bundle", "install", "evil", self.z]
        # Owner client grammar layer.
        with mock.patch.object(sys, "argv", ["client.py", *invalid]):
            with self.assertRaises(grammar.GrammarError):
                client_mod.main()
        # Managed root-helper grammar layer.
        with mock.patch.object(sys, "argv", ["managed.py", *invalid]):
            with self.assertRaises(grammar.GrammarError):
                managed_mod.main()
        # Forced-command dispatcher grammar layer.
        completed = self.run_dispatch(f"{grammar.HELPER} {' '.join(invalid)}")
        self.assertNotEqual(completed.returncode, 0)

    def test_client_accepts_new_verb_past_grammar(self):
        # A valid new verb passes the owner client grammar (reaching env/key validation
        # rather than a grammar rejection).
        with mock.patch.object(sys, "argv", ["client.py", "bundle", "inspect", "goal", self.z]):
            with self.assertRaises(client_mod.ClientError):
                client_mod.main()


class SecureCopyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path, self.config = set_config(self.root)
        (self.root / "bundles").mkdir()
        (self.root / "bundles" / "goal").mkdir()
        (self.root / "inbox").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_copy_verifies_and_stages_bundle(self):
        manifest = b'{"schemaVersion":1}'
        inbox = make_inbox_bundle(self.root, "goal", manifest, extra={"x.service": b"[Unit]\n"})
        bundle = pop.secure_copy_bundle(self.config, "goal", sha(manifest))
        self.assertEqual(bundle, self.root / "bundles" / "goal" / sha(manifest))
        self.assertEqual((bundle / "service-bundle.json").read_bytes(), manifest)
        self.assertEqual((bundle / "x.service").read_bytes(), b"[Unit]\n")
        # Idempotent re-copy resolves the existing stable bundle.
        again = pop.secure_copy_bundle(self.config, "goal", sha(manifest))
        self.assertEqual(again, bundle)

    def test_missing_manifest_rejected(self):
        inbox = self.root / "inbox" / "goal" / ("1" * 64)
        inbox.mkdir(parents=True)
        (inbox / "not-manifest.json").write_bytes(b"{}")
        with self.assertRaises(pop.PixelOperatorError):
            pop.secure_copy_bundle(self.config, "goal", "1" * 64)

    def test_manifest_sha_mismatch_rejected(self):
        manifest = b'{"schemaVersion":1}'
        make_inbox_bundle(self.root, "goal", manifest)
        with self.assertRaises(pop.PixelOperatorError):
            pop.secure_copy_bundle(self.config, "goal", "2" * 64)

    def test_symlink_rejected(self):
        manifest = b'{"schemaVersion":1}'
        inbox = make_inbox_bundle(self.root, "goal", manifest)
        outside = self.root / "outside"; outside.write_bytes(b"x")
        (inbox / "link.service").symlink_to(outside)
        with self.assertRaises(pop.PixelOperatorError):
            pop.secure_copy_bundle(self.config, "goal", sha(manifest))

    def test_hardlink_rejected(self):
        manifest = b'{"schemaVersion":1}'
        inbox = make_inbox_bundle(self.root, "goal", manifest, extra={"x.service": b"data"})
        os.link(inbox / "x.service", inbox / "y.service")
        with self.assertRaises(pop.PixelOperatorError):
            pop.secure_copy_bundle(self.config, "goal", sha(manifest))

    def test_non_regular_file_rejected(self):
        manifest = b'{"schemaVersion":1}'
        inbox = make_inbox_bundle(self.root, "goal", manifest)
        (inbox / "subdir").mkdir()
        with self.assertRaises(pop.PixelOperatorError):
            pop.secure_copy_bundle(self.config, "goal", sha(manifest))


class ServiceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path, self.config = set_config(self.root)
        make_reboot_dirs(self.root)
        (self.root / "bundles").mkdir()
        (self.root / "bundles" / "goal").mkdir()
        (self.root / "inbox").mkdir()
        manifest = json.dumps({"serviceName": "pixel-work-x.service", "timerName": "pixel-work-x.timer"}).encode()
        make_inbox_bundle(self.root, "goal", manifest)
        pop.secure_copy_bundle(self.config, "goal", sha(manifest))
        self.manifest_sha = sha(manifest)
        self.fake = write_fake_systemctl(self.root, active=True, enabled=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_service_status_content_free(self):
        result = pop.service_status(self.config, "goal", self.manifest_sha)
        self.assertEqual(result["operation"], "pixel-operator-service-status")
        units = result["units"]
        self.assertEqual(units["pixel-work-x.service"]["active"], "active")
        self.assertEqual(units["pixel-work-x.timer"]["enabled"], "enabled")
        # An authenticated root-private evidence receipt is created only when all units are
        # verified enabled+active, so reboot prepare is reachable.
        evidence = list((self.root / "reboot" / "evidence").glob("*.json"))
        self.assertEqual(len(evidence), 1)
        record = json.loads(evidence[0].read_text())
        self.assertEqual(record["kind"], "goal")
        self.assertEqual(record["manifestSha256"], self.manifest_sha)
        self.assertEqual(record["operator"], pop.OPERATOR_IDENTITY)
        self.assertEqual(set(record["units"]), set(units))
        for state in record["units"].values():
            self.assertEqual(state, {"enabled": "enabled", "active": "active"})
        self.assertGreater(record["observedAt"], 0)

    def test_service_status_returns_evidence_sha_and_status_to_prepare_round_trip(self):
        # service_status must return the content-free evidenceSha256 (not discard it) so the
        # caller can supply the exact evidence SHA to reboot prepare.
        result = pop.service_status(self.config, "goal", self.manifest_sha)
        self.assertIn("evidenceSha256", result)
        self.assertEqual(result["evidenceSha256"], self._single_evidence())
        evidence = list((self.root / "reboot" / "evidence").glob("*.json"))
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].name, f"{result['evidenceSha256']}.json")
        # Real status-to-prepare round trip using the returned SHA.
        prepared = pop.reboot_prepare(self.config, "goal", result["evidenceSha256"])
        self.assertEqual(prepared["state"], "prepared")
        self.assertEqual(prepared["kind"], "goal")

    def _single_evidence(self):
        evidence = list((self.root / "reboot" / "evidence").glob("*.json"))
        self.assertEqual(len(evidence), 1)
        return evidence[0].name[:-5]

    def test_evidence_receipt_interruption_leaves_no_partial_final_and_retry_round_trips(self):
        # A short write mid-publish must never leave a partial final evidence receipt, and a
        # retry must produce the exact content-addressed receipt usable by reboot prepare.
        real_write_all = pop.write_all
        calls = {"n": 0}

        def flaky(fd, payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("simulated crash mid-write")
            return real_write_all(fd, payload)

        with mock.patch.object(pop, "write_all", side_effect=flaky):
            with self.assertRaises(OSError):
                pop.service_status(self.config, "goal", self.manifest_sha)
        self.assertEqual(list((self.root / "reboot" / "evidence").glob("*.json")), [])
        result = pop.service_status(self.config, "goal", self.manifest_sha)
        prepared = pop.reboot_prepare(self.config, "goal", result["evidenceSha256"])
        self.assertEqual(prepared["state"], "prepared")

    def test_service_status_no_evidence_when_not_ready(self):
        write_state_systemctl(self.root, active_code=3, enabled_code=0)
        pop.service_status(self.config, "goal", self.manifest_sha)
        self.assertEqual(list((self.root / "reboot" / "evidence").glob("*.json")), [])

    def test_service_status_unknown_sha_fails_closed(self):
        with self.assertRaises(pop.PixelOperatorError):
            pop.service_status(self.config, "goal", "f" * 64)

    def test_journal_reader_removed(self):
        # The raw-journal reader is gone: no service-journal helper remains.
        self.assertFalse(hasattr(pop, "service_journal"))


class RebootProtocolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path, self.config = set_config(self.root)
        make_reboot_dirs(self.root)
        (self.root / "bundles").mkdir()
        (self.root / "bundles" / "goal").mkdir()
        (self.root / "inbox").mkdir()
        manifest = json.dumps({"serviceName": "pixel-work-x.service", "timerName": "pixel-work-x.timer"}).encode()
        make_inbox_bundle(self.root, "goal", manifest)
        pop.secure_copy_bundle(self.config, "goal", sha(manifest))
        self.manifest_sha = sha(manifest)
        self.fake = write_fake_systemctl(self.root, active=True, enabled=True)
        fake_reboot = self.root / "fake-reboot"
        fake_reboot.write_text("#!/bin/sh\nexit 0\n")
        fake_reboot.chmod(0o755)
        os.environ["PIXEL_OPERATOR_REBOOT"] = str(fake_reboot)
        self.real_boot_id = pop.read_boot_id()
        pop.service_status(self.config, "goal", self.manifest_sha)
        self.evidence_sha = self._single_evidence()

    def _single_evidence(self):
        evidence = list((self.root / "reboot" / "evidence").glob("*.json"))
        self.assertEqual(len(evidence), 1)
        return evidence[0].name[:-5]

    def tearDown(self):
        self._tmp.cleanup()

    def test_prepare_execute_status_reconcile(self):
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        self.assertEqual(prepared["state"], "prepared")
        grant = prepared["grant"]
        executed = pop.reboot_execute(self.config, grant)
        self.assertEqual(executed["state"], "accepted-asynchronous")
        status = pop.reboot_status(self.config)
        self.assertFalse(status["bootChanged"])  # same boot in-test
        # Simulate a boot that changed and the exact services resuming.
        with mock.patch.object(pop, "read_boot_id", return_value="f" * 32):
            reconciled = pop.reboot_reconcile(self.config)
            self.assertEqual(reconciled["state"], "resumed")
            self.assertTrue(reconciled["bootChanged"])

    def test_replay_refused(self):
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        pop.reboot_execute(self.config, prepared["grant"])
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_execute(self.config, prepared["grant"])

    def test_expired_grant_refused(self):
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        grant_path = self.root / "reboot" / "grants" / f"{prepared['grant']}.json"
        record = json.loads(grant_path.read_text())
        record["expiresAt"] = int(time.time()) - 10
        grant_path.write_text(json.dumps(record))
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_execute(self.config, prepared["grant"])

    def test_evidence_forgery_refused(self):
        # An arbitrary caller evidence SHA with no matching root-owned receipt fails closed.
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_prepare(self.config, "goal", "e" * 64)

    def test_rate_limit_second_prepare_blocked(self):
        pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_prepare(self.config, "goal", self.evidence_sha)

    def test_execute_requires_unit_enabled_and_active(self):
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        write_fake_systemctl(self.root, active=False, enabled=True)
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_execute(self.config, prepared["grant"])

    def test_stale_evidence_refused(self):
        evidence = list((self.root / "reboot" / "evidence").glob("*.json"))[0]
        record = json.loads(evidence.read_text())
        record["observedAt"] = int(time.time()) - (pop.REBOOT_EVIDENCE_TTL_SECONDS + 60)
        stale_payload = json.dumps(record).encode()
        stale_sha = sha(stale_payload)
        (self.root / "reboot" / "evidence" / f"{stale_sha}.json").write_bytes(stale_payload)
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_prepare(self.config, "goal", stale_sha)

    def test_boot_drift_fails_closed_on_reconcile_without_change(self):
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        pop.reboot_execute(self.config, prepared["grant"])
        # boot id unchanged -> reconcile must fail closed.
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_reconcile(self.config)

    def test_second_prepare_rejected_while_current_committed(self):
        # A committed (unreconciled) current.json must block a fresh prepare until it is
        # archived by reconciliation.
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        pop.reboot_execute(self.config, prepared["grant"])
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_prepare(self.config, "goal", self.evidence_sha)

    def test_direct_execute_cannot_replace_current(self):
        # With a current.json already committed, a direct execute must fail closed before
        # consuming authority and must leave the committed intent byte-for-byte untouched.
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        intent_root = self.root / "reboot" / "intent"
        current = intent_root / "current.json"
        current.write_text("sentinel-committed-intent")
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_execute(self.config, prepared["grant"])
        self.assertEqual(current.read_text(), "sentinel-committed-intent")
        grant_path = self.root / "reboot" / "grants" / f"{prepared['grant']}.json"
        record = json.loads(grant_path.read_text())
        self.assertIs(record["consumed"], False)

    def test_execute_commit_uses_no_replace_primitive(self):
        # The prepared -> current.json publication must go through the true no-replace
        # primitive, never an os.rename replacement.
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        intent_root = self.root / "reboot" / "intent"
        calls = []
        real = pop.no_replace_rename

        def spy(src, dst):
            calls.append((str(src), str(dst)))
            return real(src, dst)

        with mock.patch.object(pop, "no_replace_rename", side_effect=spy):
            pop.reboot_execute(self.config, prepared["grant"])
        self.assertTrue(any(str(src).endswith(".prepared.json") and str(dst).endswith("current.json") for src, dst in calls))
        self.assertTrue((intent_root / "current.json").exists())

    def test_malformed_current_blocks_prepare_and_remains_untouched(self):
        intent_root = self.root / "reboot" / "intent"
        current = intent_root / "current.json"
        current.write_text("{ not valid json")
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        self.assertEqual(current.read_text(), "{ not valid json")

    def test_linked_current_blocks_prepare_and_remains_untouched(self):
        intent_root = self.root / "reboot" / "intent"
        target = self.root / "outside.json"
        target.write_text("{}")
        current = intent_root / "current.json"
        current.symlink_to(target)
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        self.assertTrue(current.is_symlink())
        self.assertEqual(target.read_text(), "{}")

    def test_reconcile_then_fresh_prepare_allowed(self):
        # Archiving the committed intent on reconcile releases the prepare gate.
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        pop.reboot_execute(self.config, prepared["grant"])
        with mock.patch.object(pop, "read_boot_id", return_value="f" * 32):
            reconciled = pop.reboot_reconcile(self.config)
        self.assertEqual(reconciled["state"], "resumed")
        again = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        self.assertEqual(again["state"], "prepared")

    def test_load_intent_fails_closed_on_wrong_owner_current(self):
        # A wrong-owner committed intent is ambiguous and must fail closed, untouched.
        intent_root = self.root / "reboot" / "intent"
        current = intent_root / "current.json"
        payload = json.dumps({"schemaVersion": 1, "bootIdBefore": "a" * 32})
        current.write_text(payload)
        with mock.patch.object(pop, "TESTING", False), \
             mock.patch.object(pop, "require_dir", side_effect=lambda *a, **k: None):
            with self.assertRaises(pop.PixelOperatorError):
                pop._load_intent(self.config)
        self.assertEqual(current.read_text(), payload)


class BrokerBytesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.runtime, tree_sha = make_runtime_snapshot(self.root, {
            "deploy/source-broker/broker.py": b"candidate-source\n",
            "deploy/ops-broker/broker.py": b"candidate-ops\n",
            "deploy/frontier-broker/broker.py": b"candidate-frontier\n",
            "scripts/verify-frontier-codex.py": b"candidate-frontier-verify\n",
        })
        self.config_path, self.config = write_pixel_config(
            self.root, runtimePath=str(self.runtime), runtimeTreeSha256=tree_sha,
        )
        os.environ["PIXEL_OPERATOR_CONFIG"] = str(self.config_path)
        for path, mode in (
            (self.root / "broker-bytes", 0o700),
            (self.root / "broker-bytes" / "snapshots", 0o700),
            (self.root / "source-broker", 0o755),
            (self.root / "ops-broker", 0o755),
        ):
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(mode)
        self._write_installed("source", "broker.py", b"old-source\n", 0o755)
        self._write_installed("ops", "broker.py", b"old-ops\n", 0o755)
        self.systemctl = self.root / "fake-broker-systemctl"
        self.systemctl.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  show) echo success; exit 0 ;;\n"
            "  is-active) echo active; exit 0 ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n"
        )
        self.systemctl.chmod(0o755)
        os.environ["PIXEL_RELEASE_MANAGED_SYSTEMCTL"] = str(self.systemctl)

    def tearDown(self):
        os.environ.pop("PIXEL_RELEASE_MANAGED_SYSTEMCTL", None)
        self._tmp.cleanup()

    def _write_installed(self, limb, relative, payload, mode):
        path = self.root / f"{limb}-broker" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent != self.root / f"{limb}-broker":
            path.parent.chmod(0o755)
        path.write_bytes(payload)
        path.chmod(mode)
        return path

    def test_backup_install_verify_restore_round_trip_and_idempotency(self):
        backup = pop.broker_bytes_backup(self.config)
        self.assertEqual(backup["state"], "backed-up")
        self.assertEqual(set(backup["limbs"]), {"source", "ops"})
        installed = pop.broker_bytes_install(self.config)
        self.assertEqual(installed["state"], "installed")
        self.assertEqual((self.root / "source-broker" / "broker.py").read_bytes(), b"candidate-source\n")
        self.assertEqual((self.root / "ops-broker" / "broker.py").read_bytes(), b"candidate-ops\n")
        self.assertEqual(pop.broker_bytes_verify(self.config)["state"], "verified")
        restored = pop.broker_bytes_restore(self.config)
        self.assertEqual(restored["state"], "restored")
        self.assertEqual((self.root / "source-broker" / "broker.py").read_bytes(), b"old-source\n")
        self.assertEqual((self.root / "ops-broker" / "broker.py").read_bytes(), b"old-ops\n")
        self.assertFalse((self.root / "source-broker" / "action_journal" / "__init__.py").exists())
        self.assertEqual(pop.broker_bytes_restore(self.config)["state"], "restored")

    def test_legacy_action_journal_present_is_restored(self):
        legacy = self._write_installed("source", "action_journal/__init__.py", b"legacy\n", 0o644)
        pop.broker_bytes_backup(self.config)
        legacy.unlink()
        pop.broker_bytes_install(self.config)
        pop.broker_bytes_restore(self.config)
        self.assertEqual(legacy.read_bytes(), b"legacy\n")
        self.assertEqual(stat.S_IMODE(legacy.stat().st_mode), 0o644)

    def test_failed_install_leaves_mutation_state_and_restore_recovers(self):
        pop.broker_bytes_backup(self.config)
        real_restart = pop._broker_restart

        def fail_ops(value, limb):
            if limb == "ops":
                raise pop.PixelOperatorError("simulated restart failure")
            return real_restart(value, limb)

        with mock.patch.object(pop, "_broker_restart", side_effect=fail_ops):
            with self.assertRaises(pop.PixelOperatorError):
                pop.broker_bytes_install(self.config)
        self.assertEqual(pop._broker_load_state(self.config)["phase"], "mutation-started")
        with self.assertRaises(pop.PixelOperatorError):
            pop.broker_bytes_install(self.config)
        self.assertEqual(pop.broker_bytes_restore(self.config)["state"], "restored")
        self.assertEqual((self.root / "source-broker" / "broker.py").read_bytes(), b"old-source\n")
        self.assertEqual((self.root / "ops-broker" / "broker.py").read_bytes(), b"old-ops\n")

    def test_symlink_swap_after_backup_fails_without_following(self):
        pop.broker_bytes_backup(self.config)
        outside = self.root / "outside"
        outside.write_bytes(b"untouched")
        target = self.root / "source-broker" / "broker.py"
        target.unlink()
        target.symlink_to(outside)
        with self.assertRaises(pop.PixelOperatorError):
            pop.broker_bytes_install(self.config)
        self.assertEqual(outside.read_bytes(), b"untouched")
        self.assertTrue(target.is_symlink())
        self.assertEqual(pop._broker_load_state(self.config)["phase"], "mutation-started")

    def test_hardlinked_installed_byte_is_rejected_before_backup(self):
        target = self.root / "source-broker" / "broker.py"
        os.link(target, self.root / "source-broker" / "alias.py")
        with self.assertRaises(pop.PixelOperatorError):
            pop.broker_bytes_backup(self.config)

    def test_incomplete_snapshot_state_fails_closed_on_restore(self):
        fake = "f" * 64
        pop._broker_write_state(self.config, phase="mutation-started", snapshot_sha=fake, limbs=["source"])
        with self.assertRaises(pop.PixelOperatorError):
            pop.broker_bytes_restore(self.config)

    def test_runtime_tamper_is_rejected_by_install(self):
        pop.broker_bytes_backup(self.config)
        (self.runtime / "deploy/source-broker/broker.py").write_bytes(b"tampered\n")
        with self.assertRaises(pop.PixelOperatorError):
            pop.broker_bytes_install(self.config)
        self.assertEqual(pop._broker_load_state(self.config)["phase"], "backed-up")

    def test_backed_up_restore_detects_out_of_band_byte_change(self):
        pop.broker_bytes_backup(self.config)
        target = self.root / "source-broker" / "broker.py"
        target.write_bytes(b"out-of-band\n")
        target.chmod(0o755)
        with self.assertRaises(pop.PixelOperatorError):
            pop.broker_bytes_restore(self.config)

    def test_enabled_but_absent_limb_fails_closed(self):
        (self.root / "ops-broker" / "broker.py").unlink()
        with self.assertRaises(pop.PixelOperatorError):
            pop.broker_bytes_backup(self.config)

    def test_backup_is_idempotent_before_mutation_and_rearms_after_restore(self):
        first = pop.broker_bytes_backup(self.config)
        repeated = pop.broker_bytes_backup(self.config)
        self.assertEqual(repeated["snapshotSha256"], first["snapshotSha256"])
        pop._broker_write_state(
            self.config, phase="mutation-started", snapshot_sha=first["snapshotSha256"], limbs=first["limbs"],
        )
        with self.assertRaises(pop.PixelOperatorError):
            pop.broker_bytes_backup(self.config)
        pop._broker_write_state(
            self.config, phase="backed-up", snapshot_sha=first["snapshotSha256"], limbs=first["limbs"],
        )
        pop.broker_bytes_install(self.config)
        with self.assertRaises(pop.PixelOperatorError):
            pop.broker_bytes_backup(self.config)
        pop.broker_bytes_restore(self.config)
        rearmed = pop.broker_bytes_backup(self.config)
        self.assertEqual(rearmed["snapshotSha256"], first["snapshotSha256"])
        self.assertEqual(pop._broker_load_state(self.config)["phase"], "backed-up")

    def test_new_runtime_can_snapshot_the_prior_installed_candidate(self):
        first = pop.broker_bytes_backup(self.config)
        pop.broker_bytes_install(self.config)
        next_config = copy.deepcopy(self.config)
        next_config["runtimeTreeSha256"] = "e" * 64
        second = pop.broker_bytes_backup(next_config)
        self.assertNotEqual(second["snapshotSha256"], first["snapshotSha256"])
        self.assertEqual(pop._broker_load_state(next_config)["runtimeTreeSha256"], "e" * 64)


class RuntimeTreeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path, self.config = set_config(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _build_runtime(self, files):
        runtime = self.root / "runtime"
        runtime.mkdir()
        manifest = {"schemaVersion": 1, "files": {}}
        for rel, data in files.items():
            p = runtime / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            manifest["files"][rel] = sha(data)
        mb = json.dumps(manifest, sort_keys=True).encode()
        (runtime / "runtime-manifest.json").write_bytes(mb)
        self.config["runtimePath"] = str(runtime)
        self.config["runtimeTreeSha256"] = sha(mb)
        (self.root / "pixel-config.json").write_text(json.dumps(self.config))

    def test_verify_runtime_tree_matches_manifest(self):
        self._build_runtime({"deploy/work-controller/a.mjs": b"export const a=1;", "scripts/lib/b.mjs": b"export const b=2;"})
        files = pop.verify_runtime_tree(self.config)
        self.assertEqual(len(files), 2)

    def test_verify_runtime_tree_detects_tampering(self):
        self._build_runtime({"deploy/work-controller/a.mjs": b"export const a=1;"})
        (self.root / "runtime" / "deploy/work-controller/a.mjs").write_bytes(b"tampered")
        with self.assertRaises(pop.PixelOperatorError):
            pop.verify_runtime_tree(self.config)

    def test_verify_runtime_tree_rejects_unsafe_relative_path(self):
        self._build_runtime({"deploy/work-controller/a.mjs": b"export const a=1;"})
        manifest = {"schemaVersion": 1, "files": {"../../etc/evil": "0" * 64}}
        (self.root / "runtime" / "runtime-manifest.json").write_bytes(json.dumps(manifest).encode())
        self.config["runtimeTreeSha256"] = sha(json.dumps(manifest).encode())
        with self.assertRaises(pop.PixelOperatorError):
            pop.verify_runtime_tree(self.config)

    def test_verify_runtime_tree_rejects_extra_undeclared_file(self):
        self._build_runtime({"deploy/work-controller/a.mjs": b"export const a=1;"})
        (self.root / "runtime" / "undeclared.mjs").write_bytes(b"x")
        with self.assertRaises(pop.PixelOperatorError):
            pop.verify_runtime_tree(self.config)

    def test_verify_runtime_tree_rejects_symlink(self):
        self._build_runtime({"deploy/work-controller/a.mjs": b"export const a=1;"})
        outside = self.root / "outside"
        outside.write_bytes(b"x")
        (self.root / "runtime" / "evil.mjs").symlink_to(outside)
        with self.assertRaises(pop.PixelOperatorError):
            pop.verify_runtime_tree(self.config)

    def test_verify_runtime_tree_rejects_hardlink(self):
        self._build_runtime({"deploy/work-controller/a.mjs": b"export const a=1;", "deploy/work-controller/b.mjs": b"export const b=2;"})
        os.link(self.root / "runtime" / "deploy/work-controller/a.mjs", self.root / "runtime" / "deploy/work-controller/c.mjs")
        with self.assertRaises(pop.PixelOperatorError):
            pop.verify_runtime_tree(self.config)


class CliInvocationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path, self.config = set_config(self.root)
        (self.root / "bundles").mkdir()
        (self.root / "bundles" / "goal").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_bundle_activate_passes_fixed_cli_argv(self):
        # The operator must invoke the fixed Node lifecycle CLI with an argv array (never a
        # shell), delegating lifecycle semantics to the reviewed CLI.
        manifest = json.dumps({"schemaVersion": 1}).encode()
        make_inbox_bundle(self.root, "goal", manifest)
        pop.secure_copy_bundle(self.config, "goal", sha(manifest))
        captured = {}

        def fake_run(argv, timeout=300, check=True):
            captured["argv"] = list(argv)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(pop, "_run", side_effect=fake_run), \
             mock.patch.object(pop, "verify_runtime_tree", return_value={}):
            pop.bundle_activate(self.config, "goal", sha(manifest))
        argv = captured["argv"]
        self.assertEqual(argv[0], "/usr/bin/node")
        self.assertTrue(str(argv[1]).endswith("goal-service-lifecycle-cli.mjs"))
        self.assertEqual(argv[2:], ["activate", "--bundle", str(self.root / "bundles" / "goal" / sha(manifest)), "--confirm-manifest-sha256", sha(manifest)])

    def test_rollback_requires_distinct_shas(self):
        with self.assertRaises(pop.PixelOperatorError):
            pop.bundle_rollback(self.config, "goal", "a" * 64, "a" * 64)

    def test_invoke_node_cli_runs_actual_lifecycle_cli(self):
        # Prove delegation: the operator must invoke the actual reviewed Node lifecycle
        # CLI (never a Python reimplementation). Point runtimePath at the real repo and
        # feed a bundle that the real goal-service-lifecycle-cli rejects; the surfaced error
        # must name the real CLI module and its nonzero exit, not a Python-side message.
        config = dict(self.config)
        config["runtimePath"] = str(ROOT)
        config["nodePath"] = "/usr/bin/node"
        with mock.patch.object(pop, "verify_runtime_tree", return_value={}):
            with self.assertRaises(pop.PixelOperatorError) as ctx:
                pop.invoke_node_cli(config, "goal", ["inspect", "--bundle", str(self.root / "missing-bundle")])
        self.assertIn("goal-service-lifecycle-cli.mjs failed with exit code 1", str(ctx.exception))


class OwnershipPolicyTests(unittest.TestCase):
    def test_owner_mode_ok_private(self):
        ok = pop.owner_mode_ok(uid=1000, gid=1000, mode=0o700, owner_uid=1000, owner_gid=1000, private=True)
        self.assertTrue(ok)
        self.assertFalse(pop.owner_mode_ok(uid=1001, gid=1000, mode=0o700, owner_uid=1000, owner_gid=1000, private=True))
        self.assertFalse(pop.owner_mode_ok(uid=1000, gid=1001, mode=0o700, owner_uid=1000, owner_gid=1000, private=True))
        self.assertFalse(pop.owner_mode_ok(uid=1000, gid=1000, mode=0o755, owner_uid=1000, owner_gid=1000, private=True))

    def test_owner_mode_ok_shared(self):
        self.assertTrue(pop.owner_mode_ok(uid=0, gid=0, mode=0o755, owner_uid=0, owner_gid=0, private=False))
        self.assertFalse(pop.owner_mode_ok(uid=0, gid=0, mode=0o777, owner_uid=0, owner_gid=0, private=False))
        self.assertFalse(pop.owner_mode_ok(uid=1000, gid=0, mode=0o755, owner_uid=0, owner_gid=0, private=False))

    def test_boundary_mapping(self):
        _, config = write_pixel_config(Path(tempfile.mkdtemp()))
        self.assertEqual(pop.boundary(config, "inbox"), (config["ownerUid"], None, True))
        self.assertEqual(pop.boundary(config, "bundle"), (config["pixelWorkUid"], config["pixelWorkGid"], True))
        self.assertEqual(pop.boundary(config, "root"), (0, 0, False))
        self.assertEqual(pop.boundary(config, "root_private"), (0, 0, True))


class FleetManifestTests(unittest.TestCase):
    def test_fleet_manifest_filename_per_kind(self):
        self.assertEqual(pop.MANIFEST_FILENAME["fleet-goal"], "fleet-service-bundle.json")
        self.assertEqual(pop.MANIFEST_FILENAME["goal"], "service-bundle.json")
        self.assertEqual(pop.MANIFEST_FILENAME["deep-work-soak"], "service-bundle.json")

    def test_fleet_bundle_staging_uses_fleet_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, config = set_config(root)
            (root / "bundles").mkdir()
            (root / "bundles" / "fleet-goal").mkdir()
            (root / "inbox").mkdir()
            manifest = b'{"schemaVersion":1,"fleetId":"f"}'
            make_inbox_bundle(root, "fleet-goal", manifest, manifest_name="fleet-service-bundle.json")
            bundle = pop.secure_copy_bundle(config, "fleet-goal", sha(manifest))
            self.assertEqual((bundle / "fleet-service-bundle.json").read_bytes(), manifest)

    def test_fleet_bundle_without_fleet_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, config = set_config(root)
            (root / "bundles").mkdir()
            (root / "bundles" / "fleet-goal").mkdir()
            (root / "inbox").mkdir()
            manifest = b'{"schemaVersion":1,"fleetId":"f"}'
            make_inbox_bundle(root, "fleet-goal", manifest)  # wrong manifest filename
            with self.assertRaises(pop.PixelOperatorError):
                pop.secure_copy_bundle(config, "fleet-goal", sha(manifest))


class GoalPathRegressionTests(unittest.TestCase):
    def test_goal_kind_points_to_lifecycle_cli(self):
        # The wrong render-only path must never regress: goal must use the lifecycle CLI.
        self.assertEqual(pop.KIND_CLI["goal"], "deploy/work-controller/goal-service-lifecycle-cli.mjs")
        self.assertEqual(pop.KIND_CLI["fleet-goal"], "deploy/work-controller/goal-fleet-service-lifecycle-cli.mjs")
        self.assertEqual(pop.KIND_CLI["deep-work-soak"], "deploy/work-controller/deep-work-soak-service-cli.mjs")


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path, self.config = set_config(self.root)
        (self.root / "bundles").mkdir()
        (self.root / "bundles" / "goal").mkdir()
        (self.root / "inbox").mkdir()
        self.current = json.dumps({"schemaVersion": 1, "goalId": "c"}).encode()
        self.prior = json.dumps({"schemaVersion": 1, "goalId": "p"}).encode()
        make_inbox_bundle(self.root, "goal", self.current)
        make_inbox_bundle(self.root, "goal", self.prior)
        self.current_sha = sha(self.current)
        self.prior_sha = sha(self.prior)
        pop.secure_copy_bundle(self.config, "goal", self.current_sha)
        pop.secure_copy_bundle(self.config, "goal", self.prior_sha)

    def tearDown(self):
        self._tmp.cleanup()

    def test_rollback_installs_activates_then_removes(self):
        captured = []

        def fake_run(argv, timeout=300, check=True):
            captured.append(argv)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(pop, "_run", side_effect=fake_run), \
             mock.patch.object(pop, "verify_runtime_tree", return_value={}):
            result = pop.bundle_rollback(self.config, "goal", self.current_sha, self.prior_sha)
        self.assertEqual(result["state"], "rolled-back")
        verbs = [(a[2], a[6]) for a in captured]
        self.assertEqual(verbs, [
            ("install", self.prior_sha),
            ("activate", self.prior_sha),
            ("remove", self.current_sha),
        ])

    def test_rollback_activate_failure_compensates(self):
        calls = []

        def fake_run(argv, timeout=300, check=True):
            calls.append(argv)
            if argv[2] == "activate" and argv[6] == self.prior_sha:
                raise pop.PixelOperatorError("prior activate failed")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(pop, "_run", side_effect=fake_run), \
             mock.patch.object(pop, "verify_runtime_tree", return_value={}):
            with self.assertRaises(pop.PixelOperatorError):
                pop.bundle_rollback(self.config, "goal", self.current_sha, self.prior_sha)
        compensation = [a for a in calls if (a[2] == "install" or a[2] == "activate") and a[6] == self.current_sha]
        self.assertEqual(len(compensation), 2)

    def test_rollback_compensation_failure_fails_closed(self):
        def fake_run(argv, timeout=300, check=True):
            if argv[2] in ("activate", "install") and argv[6] == self.prior_sha:
                raise pop.PixelOperatorError("prior failed")
            raise pop.PixelOperatorError("compensation failed")

        with mock.patch.object(pop, "_run", side_effect=fake_run), \
             mock.patch.object(pop, "verify_runtime_tree", return_value={}):
            with self.assertRaises(pop.PixelOperatorError) as ctx:
                pop.bundle_rollback(self.config, "goal", self.current_sha, self.prior_sha)
        self.assertIn("compensation could not restore", str(ctx.exception))


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path, self.config = set_config(self.root)
        (self.root / "receipts").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_receipt_content_addressed_and_content_free(self):
        name, sha_hex = pop.write_receipt(self.config, "op", {"kind": "goal"}, outcome={"operation": "x", "state": "y"})
        self.assertTrue(name.endswith(".json"))
        path = self.root / "receipts" / name
        self.assertTrue(path.is_file())
        data = json.loads(path.read_text())
        self.assertEqual(data["inputs"], {"kind": "goal"})
        self.assertNotIn("stdout", json.dumps(data))
        self.assertNotIn("secret", json.dumps(data))
        self.assertEqual(name, f"{sha_hex}.json")

    def test_write_receipt_idempotent(self):
        n1, _ = pop.write_receipt(self.config, "op", {}, outcome={"operation": "x"})
        n2, _ = pop.write_receipt(self.config, "op", {}, outcome={"operation": "x"})
        self.assertEqual(n1, n2)

    def test_run_operation_writes_failure_receipt(self):
        with self.assertRaises(pop.PixelOperatorError):
            pop.run_operation(["bundle", "inspect", "goal", "f" * 64])
        receipts = list((self.root / "receipts").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        data = json.loads(receipts[0].read_text())
        self.assertEqual(data["exitCode"], 1)

    def test_write_receipt_interruption_leaves_no_partial_final_and_retry_succeeds(self):
        # A crash/short write mid-publish must never leave a partial final content-addressed
        # receipt; a retry republishes the exact bytes atomically.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path, config = set_config(root)
            (root / "receipts").mkdir()
            real_write_all = pop.write_all
            calls = {"n": 0}

            def flaky(fd, payload):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("simulated crash mid-write")
                return real_write_all(fd, payload)

            with mock.patch.object(pop, "write_all", side_effect=flaky):
                with self.assertRaises(OSError):
                    pop.write_receipt(config, "op", {"kind": "goal"})
            # No final receipt (and no stray temp with a .json suffix) is published.
            self.assertEqual(list((root / "receipts").glob("*.json")), [])
            # Retry succeeds and publishes the exact final content-addressed receipt.
            name, sha_hex = pop.write_receipt(config, "op", {"kind": "goal"})
            self.assertEqual(name, f"{sha_hex}.json")
            path = root / "receipts" / name
            self.assertTrue(path.is_file())
            data = json.loads(path.read_text())
            self.assertEqual(data["inputs"], {"kind": "goal"})


class BoundedRunTests(unittest.TestCase):
    def test_oversized_output_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "noisy"
            script.write_text("#!/bin/sh\nhead -c 300000 /dev/zero | tr '\\0' 'x'\n")
            script.chmod(0o755)
            with self.assertRaises(pop.PixelOperatorError):
                pop._run([str(script)])

    def test_hard_timeout_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "slow"
            script.write_text("#!/bin/sh\nsleep 5\n")
            script.chmod(0o755)
            with self.assertRaises(pop.PixelOperatorError):
                pop.run_bounded([str(script)], timeout=1)

    def test_grandchild_holding_pipe_fails_closed(self):
        # A grandchild that keeps the stdout pipe open after the child exits would make the
        # captured output silently incomplete. run_bounded must terminate the process group
        # and fail closed rather than return a normal result.
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "hold"
            script.write_text(
                "#!/bin/sh\n"
                "python3 -c 'import time; time.sleep(30)' &\n"
                "echo done\n"
                "exit 0\n"
            )
            script.chmod(0o755)
            with mock.patch.object(pop, "RUN_DRAIN_TIMEOUT", 0.5):
                with self.assertRaises(pop.PixelOperatorError):
                    pop.run_bounded([str(script)], timeout=5)

    def test_systemctl_inactive_and_disabled_expected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, config = set_config(root)
            (root / "bundles").mkdir()
            (root / "bundles" / "goal").mkdir()
            manifest = json.dumps({"serviceName": "a.service"}).encode()
            make_inbox_bundle(root, "goal", manifest)
            pop.secure_copy_bundle(config, "goal", sha(manifest))
            write_state_systemctl(root, active_code=3, enabled_code=1)
            result = pop.service_status(config, "goal", sha(manifest))
            self.assertEqual(result["units"]["a.service"]["active"], "inactive")
            self.assertEqual(result["units"]["a.service"]["enabled"], "disabled")

    def test_systemctl_error_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, config = set_config(root)
            (root / "bundles").mkdir()
            (root / "bundles" / "goal").mkdir()
            manifest = json.dumps({"serviceName": "a.service"}).encode()
            make_inbox_bundle(root, "goal", manifest)
            pop.secure_copy_bundle(config, "goal", sha(manifest))
            write_state_systemctl(root, active_code=4, enabled_code=0)
            with self.assertRaises(pop.PixelOperatorError):
                pop.service_status(config, "goal", sha(manifest))


class RebootAuthorityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path, self.config = set_config(self.root)
        make_reboot_dirs(self.root)
        (self.root / "bundles").mkdir()
        (self.root / "bundles" / "goal").mkdir()
        (self.root / "inbox").mkdir()
        manifest = json.dumps({"serviceName": "pixel-work-x.service"}).encode()
        make_inbox_bundle(self.root, "goal", manifest)
        pop.secure_copy_bundle(self.config, "goal", sha(manifest))
        self.manifest_sha = sha(manifest)
        write_fake_systemctl(self.root, active=True, enabled=True)
        fake_reboot = self.root / "fake-reboot"
        fake_reboot.write_text("#!/bin/sh\nexit 0\n")
        fake_reboot.chmod(0o755)
        os.environ["PIXEL_OPERATOR_REBOOT"] = str(fake_reboot)
        pop.service_status(self.config, "goal", self.manifest_sha)
        self.evidence_sha = list((self.root / "reboot" / "evidence").glob("*.json"))[0].name[:-5]

    def tearDown(self):
        self._tmp.cleanup()

    def test_execute_requires_current_boot_equality(self):
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        with mock.patch.object(pop, "read_boot_id", return_value="a" * 32):
            with self.assertRaises(pop.PixelOperatorError):
                pop.reboot_execute(self.config, prepared["grant"])

    def test_expired_grant_does_not_block_prepare(self):
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        grant_path = self.root / "reboot" / "grants" / f"{prepared['grant']}.json"
        record = json.loads(grant_path.read_text())
        record["expiresAt"] = int(time.time()) - 10
        grant_path.write_text(json.dumps(record))
        again = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        self.assertEqual(again["state"], "prepared")

    def test_reconcile_is_one_use(self):
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        pop.reboot_execute(self.config, prepared["grant"])
        with mock.patch.object(pop, "read_boot_id", return_value="b" * 32):
            reconciled = pop.reboot_reconcile(self.config)
            self.assertEqual(reconciled["state"], "resumed")
            with self.assertRaises(pop.PixelOperatorError):
                pop.reboot_reconcile(self.config)

    def test_evidence_kind_mismatch_rejected(self):
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_prepare(self.config, "deep-work-soak", self.evidence_sha)

    def test_evidence_missing_kind_rejected(self):
        evidence = {"schemaVersion": 1, "unit": "pixel-work-x.service"}
        payload = json.dumps(evidence, sort_keys=True).encode()
        (self.root / "reboot" / "evidence" / f"{sha(payload)}.json").write_bytes(payload)
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_prepare(self.config, "goal", sha(payload))


class SecureCopyHardeningTests(unittest.TestCase):
    def test_write_all_handles_short_writes(self):
        payload = b"x" * 1000
        written = bytearray()

        def short_write(fd, view):
            written.extend(view[:1])
            return 1

        with mock.patch.object(pop.os, "write", side_effect=short_write):
            pop.write_all(None, payload)
        self.assertEqual(bytes(written), payload)

    def test_leftover_stage_dir_does_not_block_copy(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, config = set_config(root)
            (root / "bundles").mkdir()
            (root / "bundles" / "goal").mkdir()
            (root / "inbox").mkdir()
            (root / "bundles" / "goal" / ".stage-dead").mkdir()
            manifest = b'{"schemaVersion":1}'
            make_inbox_bundle(root, "goal", manifest)
            bundle = pop.secure_copy_bundle(config, "goal", sha(manifest))
            self.assertEqual(bundle.name, sha(manifest))

    def test_no_replace_never_replaces_existing_dest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, config = set_config(root)
            (root / "bundles").mkdir()
            (root / "bundles" / "goal").mkdir()
            (root / "inbox").mkdir()
            manifest = b'{"schemaVersion":1}'
            make_inbox_bundle(root, "goal", manifest)
            dest = root / "bundles" / "goal" / sha(manifest)
            dest.mkdir()
            (dest / "service-bundle.json").write_bytes(b"malicious")
            # A pre-existing destination is never replaced by staging.
            with self.assertRaises(pop.PixelOperatorError):
                pop.secure_copy_bundle(config, "goal", sha(manifest))
            self.assertEqual((dest / "service-bundle.json").read_bytes(), b"malicious")

    def test_no_replace_rename_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "src"
            dst = root / "dst"
            src.write_text("new")
            dst.write_text("old")
            with self.assertRaises(FileExistsError):
                pop.no_replace_rename(src, dst)
            self.assertEqual(dst.read_text(), "old")
            # Publishing to a missing destination succeeds atomically.
            pop.no_replace_rename(src, root / "fresh")
            self.assertEqual((root / "fresh").read_text(), "new")

    def test_no_replace_rename_fails_closed_when_primitive_unavailable(self):
        # On a Linux host without a usable renameat2, no_replace_rename must fail closed and
        # must never weaken to os.rename (which could replace an empty destination dir).
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "src"
            dst = root / "dst"
            src.mkdir()
            (src / "f").write_text("data")
            dst.mkdir()  # empty destination directory (os.rename could replace it)
            rename_calls = []

            def deny_rename(s, t):
                rename_calls.append((s, t))

            def no_primitive(sb, db):
                raise OSError(errno.ENOSYS, "no renameat2")

            with mock.patch.object(pop, "_renameat2_noreplace", side_effect=no_primitive), \
                 mock.patch.object(pop.os, "rename", side_effect=deny_rename):
                with self.assertRaises(OSError):
                    pop.no_replace_rename(src, dst)
            # os.rename was never used as a fallback and the empty destination is intact.
            self.assertEqual(rename_calls, [])
            self.assertTrue(dst.is_dir())
            self.assertFalse((dst / "f").exists())

    def test_no_replace_rename_never_replaces_empty_destination(self):
        # With the real no-replace primitive, a concurrent empty-destination publication is
        # never replaced; the source directory's contents never leak into the destination.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "src"
            dst = root / "dst"
            src.mkdir()
            (src / "f").write_text("data")
            dst.mkdir()
            with self.assertRaises(FileExistsError):
                pop.no_replace_rename(src, dst)
            self.assertTrue(dst.is_dir())
            self.assertFalse((dst / "f").exists())
            self.assertTrue((src / "f").exists())

    def test_concurrent_same_sha_copy_produces_one_bundle(self):
        import threading
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, config = set_config(root)
            (root / "bundles").mkdir()
            (root / "bundles" / "goal").mkdir()
            (root / "inbox").mkdir()
            manifest = b'{"schemaVersion":1}'
            make_inbox_bundle(root, "goal", manifest)
            results = []

            def worker():
                try:
                    results.append(pop.secure_copy_bundle(config, "goal", sha(manifest)))
                except Exception as exc:
                    results.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            expected = root / "bundles" / "goal" / sha(manifest)
            self.assertTrue(expected.is_dir())
            self.assertEqual((expected / "service-bundle.json").read_bytes(), manifest)
            self.assertEqual(list((root / "bundles" / "goal").glob(".stage-*")), [])


class StagedValidationTests(unittest.TestCase):
    def _write_runtime(self, root, files):
        runtime = root / "runtime"
        runtime.mkdir()
        manifest = {"schemaVersion": 1, "files": {}}
        for rel, data in files.items():
            p = runtime / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            manifest["files"][rel] = sha(data)
        mb = (json.dumps(manifest, sort_keys=True) + "\n").encode()
        (runtime / "runtime-manifest.json").write_bytes(mb)
        return runtime, sha(mb)

    def test_staged_validation_accepts_valid(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            runtime, tree_sha = self._write_runtime(root, {"deploy/work-controller/a.mjs": b"export const a=1;"})
            config = {
                "schemaVersion": 1, "ownerUid": 1000, "pixelWorkUid": 1001, "pixelWorkGid": 1001,
                "inboxRoot": "/tmp/inbox", "bundleRoot": "/tmp/bundles", "runtimePath": "/tmp/runtime",
                "runtimeTreeSha256": tree_sha, "nodePath": "/usr/bin/node", "systemctlPath": "/usr/bin/systemctl",
                "systemdAnalyzePath": "/usr/bin/systemd-analyze", "receiptRoot": "/tmp/receipts",
                "rebootRoot": "/tmp/reboot", "brokerBytes": broker_bytes_config(root), "readersEnabled": False,
            }
            cfg = root / "cfg.json"
            cfg.write_text(json.dumps(config))
            self.assertTrue(pop._validate_staging_bytes(str(cfg), str(runtime)))

    def test_staged_validation_accepts_owner_built_runtime_before_root_install(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            runtime, tree_sha = self._write_runtime(root, {"deploy/work-controller/a.mjs": b"export const a=1;"})
            config = {
                "schemaVersion": 1, "ownerUid": 1000, "pixelWorkUid": 1001, "pixelWorkGid": 1001,
                "inboxRoot": "/tmp/inbox", "bundleRoot": "/tmp/bundles", "runtimePath": "/tmp/runtime",
                "runtimeTreeSha256": tree_sha, "nodePath": "/usr/bin/node", "systemctlPath": "/usr/bin/systemctl",
                "systemdAnalyzePath": "/usr/bin/systemd-analyze", "receiptRoot": "/tmp/receipts",
                "rebootRoot": "/tmp/reboot",
                "brokerBytes": {
                    "backupRoot": "/var/lib/pixel-release-operator/broker-bytes",
                    "limbs": {
                        "source": {"enabled": True, "installDir": "/opt/pixel-source-broker", "directPathEnabled": False},
                        "ops": {"enabled": True, "installDir": "/opt/pixel-ops-broker"},
                        "frontier": {"enabled": False, "installDir": "/opt/pixel-frontier-broker"},
                    },
                },
                "readersEnabled": False,
            }
            cfg = root / "cfg.json"
            cfg.write_text(json.dumps(config))
            with mock.patch.object(pop, "TESTING", False):
                self.assertTrue(pop._validate_staging_bytes(str(cfg), str(runtime)))

    def test_staged_validation_rejects_sha_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            runtime, tree_sha = self._write_runtime(root, {"deploy/work-controller/a.mjs": b"export const a=1;"})
            config = {
                "schemaVersion": 1, "ownerUid": 1000, "pixelWorkUid": 1001, "pixelWorkGid": 1001,
                "inboxRoot": "/tmp/inbox", "bundleRoot": "/tmp/bundles", "runtimePath": "/tmp/runtime",
                "runtimeTreeSha256": "0" * 64, "nodePath": "/usr/bin/node", "systemctlPath": "/usr/bin/systemctl",
                "systemdAnalyzePath": "/usr/bin/systemd-analyze", "receiptRoot": "/tmp/receipts",
                "rebootRoot": "/tmp/reboot", "brokerBytes": broker_bytes_config(root), "readersEnabled": False,
            }
            cfg = root / "cfg.json"
            cfg.write_text(json.dumps(config))
            with self.assertRaises(pop.PixelOperatorError):
                pop._validate_staging_bytes(str(cfg), str(runtime))



class InstalledConfigModeTests(unittest.TestCase):
    def test_root_private_policy_accepts_0600(self):
        # The real installed-mode config validator requires root ownership and 0600.
        self.assertTrue(pop.owner_mode_ok(uid=0, gid=0, mode=0o600, owner_uid=0, owner_gid=0, private=True))

    def test_root_private_policy_rejects_0644(self):
        # Provisioning must install the config 0600; 0644 group/world-read is rejected.
        self.assertFalse(pop.owner_mode_ok(uid=0, gid=0, mode=0o644, owner_uid=0, owner_gid=0, private=True))
        self.assertFalse(pop.owner_mode_ok(uid=1000, gid=0, mode=0o600, owner_uid=0, owner_gid=0, private=True))


class ProvisioningHarnessTests(unittest.TestCase):
    """Rootless but production-byte faithful provisioning harness.

    Runs the exact provisioning implementation (provision_scaffolding / install_runtime /
    build_final_config) against a redirected temporary prefix. Modes are checked for real;
    owners are simulated (rootless) by capturing _chown transfers.
    """

    def test_chown_preserves_an_unconstrained_group(self):
        path = Path("/not-accessed")
        with mock.patch.object(pop, "TESTING", False), mock.patch.object(pop.os, "chown") as chown:
            pop._chown(path, 1000, None)
        chown.assert_called_once_with(path, 1000, -1)

    def test_provision_scaffolding_modes_and_owners(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, config = make_harness_config(root, "0" * 64)
            transfers = []
            with mock.patch.object(pop, "_chown", side_effect=lambda p, u, g: transfers.append((str(p), u, g))):
                pop.provision_scaffolding(config)
            self.assertEqual(stat.S_IMODE((root / "inbox").stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((root / "bundles").stat().st_mode), 0o700)
            for kind in ("goal", "fleet-goal", "deep-work-soak"):
                self.assertEqual(stat.S_IMODE((root / "bundles" / kind).stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((root / "receipts").stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((root / "reboot").stat().st_mode), 0o700)
            for sub in ("evidence", "grants", "intent"):
                self.assertEqual(stat.S_IMODE((root / "reboot" / sub).stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((root / "reboot" / "intent" / "archive").stat().st_mode), 0o700)
            owner = {p: (u, g) for p, u, g in transfers}
            self.assertEqual(owner[str(root / "inbox")][0], config["ownerUid"])
            self.assertEqual(owner[str(root / "bundles")][0], config["pixelWorkUid"])
            for kind in ("goal", "fleet-goal", "deep-work-soak"):
                self.assertEqual(owner[str(root / "bundles" / kind)][0], config["pixelWorkUid"])
                self.assertEqual(owner[str(root / "bundles" / kind)][1], config["pixelWorkGid"])
            for pth in ("receipts", "reboot", "reboot/evidence", "reboot/grants", "reboot/intent", "reboot/intent/archive"):
                self.assertEqual(owner[str(root / pth)][0], 0)

    def test_provision_refuses_preexisting_unsafe_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, config = make_harness_config(root, "0" * 64)
            (root / "inbox").mkdir()
            (root / "inbox").chmod(0o755)  # owner-private inbox must be 0700
            with mock.patch.object(pop, "_chown", side_effect=lambda p, u, g: None):
                with self.assertRaises(pop.PixelOperatorError):
                    pop.provision_scaffolding(config)

    def test_provision_refuses_preexisting_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, config = make_harness_config(root, "0" * 64)
            outside = root / "outside"
            outside.mkdir()
            (root / "inbox").symlink_to(outside)
            with mock.patch.object(pop, "_chown", side_effect=lambda p, u, g: None):
                with self.assertRaises(pop.PixelOperatorError):
                    pop.provision_scaffolding(config)

    def test_directory_mode_exact_refuses_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "target"
            target.mkdir()
            target.chmod(0o700)
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(pop.PixelOperatorError):
                pop._set_directory_mode_exact(link, 0o755, "test directory")
            self.assertTrue(link.is_symlink())
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_directory_mode_exact_refuses_raced_inode_before_fchmod(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "target"
            target.mkdir()
            target.chmod(0o700)
            decoy = root / "decoy"
            decoy.mkdir()
            decoy.chmod(0o700)
            real_lstat = Path.lstat

            def raced_lstat(path, *args, **kwargs):
                if path == target:
                    return real_lstat(decoy, *args, **kwargs)
                return real_lstat(path, *args, **kwargs)

            with mock.patch.object(Path, "lstat", autospec=True, side_effect=raced_lstat):
                with mock.patch.object(pop.os, "fchmod") as fchmod:
                    with self.assertRaisesRegex(pop.PixelOperatorError, "changed during secure open"):
                        pop._set_directory_mode_exact(target, 0o755, "test directory")
                    fchmod.assert_not_called()
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(decoy.stat().st_mode), 0o700)

    def test_runtime_install_never_uses_path_chmod_for_directory_contracts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snap, tree_sha = make_runtime_snapshot(
                root,
                {
                    "a.mjs": b"export const a=1;",
                    "sub/nested/b.mjs": b"export const b=2;",
                },
            )
            parent = root / "runtime-intermediate" / "runtime-parent"
            old_umask = os.umask(0o077)
            try:
                with mock.patch.object(
                    pop.os,
                    "chmod",
                    side_effect=AssertionError("runtime provisioning must not use path chmod"),
                ):
                    final = pop.provision_install_runtime(str(snap), str(parent))
            finally:
                os.umask(old_umask)
            self.assertEqual(Path(final), parent / tree_sha)
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)
            self.assertEqual(
                stat.S_IMODE((root / "runtime-intermediate").stat().st_mode), 0o755
            )
            for dirpath, _dirnames, filenames in os.walk(final):
                self.assertEqual(stat.S_IMODE(os.stat(dirpath).st_mode), 0o755, dirpath)
                for name in filenames:
                    self.assertEqual(
                        stat.S_IMODE((Path(dirpath) / name).stat().st_mode), 0o644, name
                    )

    def test_install_runtime_exact_closure_and_retained_prior(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snap1, sha1 = make_runtime_snapshot(root, {"a.mjs": b"export const a=1;", "sub/b.mjs": b"export const b=2;"})
            parent = root / "runtime-parent"
            final1 = pop.provision_install_runtime(str(snap1), str(parent))
            self.assertEqual(Path(final1), parent / sha1)
            self.assertEqual(stat.S_IMODE((Path(final1)).stat().st_mode), 0o755)
            # exact closure: manifest + declared files only, mode 0644
            files = list((Path(final1) / "sub").glob("*.mjs")) + list(Path(final1).glob("*.json")) + list(Path(final1).glob("*.mjs"))
            self.assertEqual(len(files), 3)
            for pth in files:
                self.assertEqual(stat.S_IMODE(pth.stat().st_mode), 0o644)
            # idempotent re-provision returns same final path
            again = pop.provision_install_runtime(str(snap1), str(parent))
            self.assertEqual(again, str(final1))
            # prior version retained
            snap2, sha2 = make_runtime_snapshot(root, {"a.mjs": b"export const a=2;"})
            self.assertNotEqual(sha1, sha2)
            final2 = pop.provision_install_runtime(str(snap2), str(parent))
            self.assertEqual(Path(final2), parent / sha2)
            self.assertTrue((parent / sha1).is_dir())

    def test_install_runtime_rejects_extra_undeclared_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snap, tree_sha = make_runtime_snapshot(root, {"a.mjs": b"export const a=1;"})
            parent = root / "runtime-parent"
            (snap / "undeclared.mjs").write_bytes(b"x")
            with self.assertRaises(pop.PixelOperatorError):
                pop.provision_install_runtime(str(snap), str(parent))
            self.assertEqual(list(parent.glob(tree_sha)), [])
            self.assertEqual(list(parent.glob(".stage-*")), [])

    def test_install_runtime_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snap, tree_sha = make_runtime_snapshot(root, {"a.mjs": b"export const a=1;"})
            parent = root / "runtime-parent"
            (snap / "a.mjs").unlink()
            (snap / "link.mjs").symlink_to(snap / "runtime-manifest.json")
            with self.assertRaises(pop.PixelOperatorError):
                pop.provision_install_runtime(str(snap), str(parent))
            self.assertEqual(list(parent.glob(tree_sha)), [])
            self.assertEqual(list(parent.glob(".stage-*")), [])

    def test_install_runtime_atomic_interruption_retry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snap, tree_sha = make_runtime_snapshot(root, {"a.mjs": b"export const a=1;", "b.mjs": b"export const b=2;"})
            parent = root / "runtime-parent"
            real = pop._write_runtime_file
            calls = {"n": 0}

            def flaky(target, data):
                calls["n"] += 1
                if calls["n"] >= 3:
                    raise OSError("simulated crash mid-copy")
                return real(target, data)

            with mock.patch.object(pop, "_write_runtime_file", side_effect=flaky):
                with self.assertRaises(OSError):
                    pop.provision_install_runtime(str(snap), str(parent))
            # A partial final path is never selected and staging is cleaned up.
            self.assertEqual(list(parent.glob(tree_sha)), [])
            self.assertEqual(list(parent.glob(".stage-*")), [])
            # Retry succeeds and selects the exact final runtime.
            final = pop.provision_install_runtime(str(snap), str(parent))
            self.assertEqual(Path(final), parent / tree_sha)

    def test_install_runtime_umask_0077_proves_exact_contract_modes(self):
        # Adversarial: a restrictive umask (0077) must not leak into the installed runtime.
        # Root and every nested directory must be exactly 0755 (service-traversable) and
        # every manifest/runtime file exactly 0644, via descriptor-safe fchmod plus a
        # complete-tree normalization before publication.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snap, tree_sha = make_runtime_snapshot(
                root, {"a.mjs": b"export const a=1;", "sub/nested/b.mjs": b"export const b=2;"}
            )
            # One scaffold level deeper so the intermediate component is created by the
            # scaffold walk itself and its 0755 contract is proven under umask 0077.
            parent = root / "runtime-intermediate" / "runtime-parent"
            real_normalize = pop._normalize_runtime_tree_modes

            def spy_normalize(staging):
                # Construction privacy: when normalization begins, every byte is already
                # written but the staging root is still private (0700); the incomplete tree
                # was never traversable by another identity.
                self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)
                return real_normalize(staging)

            old_umask = os.umask(0o077)
            try:
                with mock.patch.object(pop, "_normalize_runtime_tree_modes", side_effect=spy_normalize):
                    final = pop.provision_install_runtime(str(snap), str(parent))
            finally:
                os.umask(old_umask)
            self.assertEqual(Path(final), parent / tree_sha)
            self.assertEqual(list(parent.glob(".stage-*")), [])
            # The runtime parent contract (0755 shared root) holds even under umask 0077,
            # including every intermediate component created by the scaffold walk.
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((root / "runtime-intermediate").stat().st_mode), 0o755)
            for dirpath, _dirnames, filenames in os.walk(parent / tree_sha):
                self.assertEqual(stat.S_IMODE(os.stat(dirpath).st_mode), 0o755, dirpath)
                for name in filenames:
                    self.assertEqual(stat.S_IMODE((Path(dirpath) / name).stat().st_mode), 0o644, name)

    def test_install_runtime_interruption_retry_idempotent_under_umask_0077(self):
        # Adversarial: mid-copy crash under umask 0077 exposes nothing traversable, and the
        # retry is idempotent with the exact contract modes. Link/closure checks stay on.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snap, tree_sha = make_runtime_snapshot(
                root, {"a.mjs": b"export const a=1;", "sub/nested/b.mjs": b"export const b=2;"}
            )
            parent = root / "runtime-parent"
            real = pop._write_runtime_file
            calls = {"n": 0}

            def flaky(target, data):
                calls["n"] += 1
                if calls["n"] >= 3:
                    raise OSError("simulated crash mid-copy")
                return real(target, data)

            old_umask = os.umask(0o077)
            try:
                with mock.patch.object(pop, "_write_runtime_file", side_effect=flaky):
                    with self.assertRaises(OSError):
                        pop.provision_install_runtime(str(snap), str(parent))
                # No partial final path is ever selected; staging is cleaned up; the runtime
                # parent (a root-boundary "shared root" leaf) is exactly 0755 by contract.
                self.assertEqual(list(parent.glob(tree_sha)), [])
                self.assertEqual(list(parent.glob(".stage-*")), [])
                self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)
                final = pop.provision_install_runtime(str(snap), str(parent))
                again = pop.provision_install_runtime(str(snap), str(parent))
            finally:
                os.umask(old_umask)
            self.assertEqual(Path(final), parent / tree_sha)
            self.assertEqual(again, str(final))
            for dirpath, _dirnames, filenames in os.walk(parent / tree_sha):
                self.assertEqual(stat.S_IMODE(os.stat(dirpath).st_mode), 0o755, dirpath)
                for name in filenames:
                    self.assertEqual(stat.S_IMODE((Path(dirpath) / name).stat().st_mode), 0o644, name)

    def test_install_runtime_idempotent_reprovision_rejects_file_mode_0600(self):
        # Adversarial: a pre-existing runtime installed under a bad umask (regular file 0600)
        # must be rejected fail-closed by exact-mode verification during idempotent
        # re-provision; the published immutable runtime is never silently repaired. The retry
        # succeeds only after the test restores the exact contract mode.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snap, tree_sha = make_runtime_snapshot(
                root, {"a.mjs": b"export const a=1;", "sub/nested/b.mjs": b"export const b=2;"}
            )
            parent = root / "runtime-parent"
            final = parent / tree_sha
            first = pop.provision_install_runtime(str(snap), str(parent))
            self.assertEqual(Path(first), final)
            # Simulate bad-umask install / later mode drift on the published runtime.
            (final / "a.mjs").chmod(0o600)
            with self.assertRaises(pop.PixelOperatorError):
                pop.provision_install_runtime(str(snap), str(parent))
            # Fail closed: drift is neither accepted nor repaired, no staging residue.
            self.assertEqual(stat.S_IMODE((final / "a.mjs").stat().st_mode), 0o600)
            self.assertEqual(list(parent.glob(".stage-*")), [])
            (final / "a.mjs").chmod(0o644)
            again = pop.provision_install_runtime(str(snap), str(parent))
            self.assertEqual(Path(again), final)
            for dirpath, _dirnames, filenames in os.walk(final):
                self.assertEqual(stat.S_IMODE(os.stat(dirpath).st_mode), 0o755, dirpath)
                for name in filenames:
                    self.assertEqual(stat.S_IMODE((Path(dirpath) / name).stat().st_mode), 0o644, name)

    def test_install_runtime_idempotent_reprovision_rejects_directory_mode_0700(self):
        # Adversarial: a pre-existing runtime whose nested directory is 0700 (bad umask or
        # later drift) must be rejected fail-closed during idempotent re-provision; the
        # published immutable runtime is never silently repaired. The retry succeeds only
        # after the test restores the exact contract mode.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snap, tree_sha = make_runtime_snapshot(
                root, {"a.mjs": b"export const a=1;", "sub/nested/b.mjs": b"export const b=2;"}
            )
            parent = root / "runtime-parent"
            final = parent / tree_sha
            first = pop.provision_install_runtime(str(snap), str(parent))
            self.assertEqual(Path(first), final)
            (final / "sub").chmod(0o700)
            with self.assertRaises(pop.PixelOperatorError):
                pop.provision_install_runtime(str(snap), str(parent))
            # Fail closed: drift is neither accepted nor repaired, no staging residue.
            self.assertEqual(stat.S_IMODE((final / "sub").stat().st_mode), 0o700)
            self.assertEqual(list(parent.glob(".stage-*")), [])
            (final / "sub").chmod(0o755)
            again = pop.provision_install_runtime(str(snap), str(parent))
            self.assertEqual(Path(again), final)
            for dirpath, _dirnames, filenames in os.walk(final):
                self.assertEqual(stat.S_IMODE(os.stat(dirpath).st_mode), 0o755, dirpath)
                for name in filenames:
                    self.assertEqual(stat.S_IMODE((Path(dirpath) / name).stat().st_mode), 0o644, name)

    def test_build_final_config_exact_bytes_and_0600(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg, config = make_harness_config(root, "0" * 64)
            runtime_dir = root / "installed-runtime"
            runtime_dir.mkdir()
            stage = root / "final-stage.json"
            final_bytes = pop.provision_build_final_config(str(cfg), str(runtime_dir), str(stage))
            self.assertEqual(stage.read_bytes(), final_bytes)
            self.assertEqual(stat.S_IMODE(stage.stat().st_mode), 0o600)
            installed = json.loads(final_bytes.decode())
            self.assertEqual(installed["runtimePath"], str(runtime_dir.resolve()))
            # Stage bytes equal a copy made by install (exact byte equality).
            installed_copy = root / "installed.json"
            shutil.copyfile(stage, installed_copy)
            self.assertEqual(stage.read_bytes(), installed_copy.read_bytes())


class RebootCrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path, self.config = set_config(self.root)
        make_reboot_dirs(self.root)
        (self.root / "bundles").mkdir()
        (self.root / "bundles" / "goal").mkdir()
        (self.root / "inbox").mkdir()
        manifest = json.dumps({"serviceName": "pixel-work-x.service"}).encode()
        make_inbox_bundle(self.root, "goal", manifest)
        pop.secure_copy_bundle(self.config, "goal", sha(manifest))
        self.manifest_sha = sha(manifest)
        write_fake_systemctl(self.root, active=True, enabled=True)
        fake_reboot = self.root / "fake-reboot"
        fake_reboot.write_text("#!/bin/sh\nexit 0\n")
        fake_reboot.chmod(0o755)
        os.environ["PIXEL_OPERATOR_REBOOT"] = str(fake_reboot)
        self.real_boot_id = pop.read_boot_id()
        pop.service_status(self.config, "goal", self.manifest_sha)
        self.evidence_sha = list((self.root / "reboot" / "evidence").glob("*.json"))[0].name[:-5]

    def tearDown(self):
        self._tmp.cleanup()

    def _prepared_intent(self, grant):
        return {
            "schemaVersion": 1, "operation": "pixel-operator-reboot-execute",
            "kind": "goal", "units": ["pixel-work-x.service"], "grant": grant,
            "evidenceSha256": self.evidence_sha, "manifestSha256": self.manifest_sha,
            "bootIdBefore": self.real_boot_id, "state": "prepared", "preparedAt": int(time.time()),
        }

    def test_execute_writes_prepared_before_consume(self):
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        grant = prepared["grant"]
        intent_root = self.root / "reboot" / "intent"
        observed = {}
        real_consume = pop._consume_grant

        def spy(path, record):
            observed["prepared_before_consume"] = (intent_root / f"{grant}.prepared.json").exists()
            return real_consume(path, record)

        with mock.patch.object(pop, "_consume_grant", side_effect=spy):
            pop.reboot_execute(self.config, grant)
        self.assertTrue(observed["prepared_before_consume"])
        # After success the committed intent exists and no prepared file remains.
        self.assertTrue((intent_root / "current.json").exists())
        self.assertFalse((intent_root / f"{grant}.prepared.json").exists())

    def test_crash_after_prepare_before_consume_recovers_without_burning_authority(self):
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        grant = prepared["grant"]
        intent_root = self.root / "reboot" / "intent"
        (intent_root / f"{grant}.prepared.json").write_text(json.dumps(self._prepared_intent(grant)))
        pop.reboot_status(self.config)  # recovery
        self.assertFalse((intent_root / f"{grant}.prepared.json").exists())
        grant_path = self.root / "reboot" / "grants" / f"{grant}.json"
        record = json.loads(grant_path.read_text())
        self.assertIs(record["consumed"], False)
        # The grant can still be executed (no authority lost).
        executed = pop.reboot_execute(self.config, grant)
        self.assertEqual(executed["state"], "accepted-asynchronous")

    def test_crash_after_consume_before_commit_classified_pre_reboot_failure(self):
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        grant = prepared["grant"]
        intent_root = self.root / "reboot" / "intent"
        (intent_root / f"{grant}.prepared.json").write_text(json.dumps(self._prepared_intent(grant)))
        grant_path = self.root / "reboot" / "grants" / f"{grant}.json"
        record = json.loads(grant_path.read_text())
        record["consumed"] = True
        record["consumedAt"] = int(time.time())
        grant_path.write_text(json.dumps(record))
        # Recovery classifies this as a pre-reboot failure (never accepted/ambiguous) and
        # archives both the prepared intent and the consumed grant.
        status = pop.reboot_status(self.config)
        self.assertIsNone(status["intent"])
        self.assertFalse((intent_root / f"{grant}.prepared.json").exists())
        self.assertFalse((intent_root / "current.json").exists())
        archived_intent = list((intent_root / "archive").glob("pre-reboot-failed-*.json"))
        self.assertEqual(len(archived_intent), 1)
        archived = json.loads(archived_intent[0].read_text())
        self.assertEqual(archived["state"], "prepared")
        self.assertEqual(archived["grant"], grant)
        archived_grants = list((self.root / "reboot" / "grants" / "archive").glob("pre-reboot-failed-*.json"))
        self.assertEqual(len(archived_grants), 1)
        self.assertFalse(grant_path.exists())
        # A fresh grant can now be prepared.
        again = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        self.assertEqual(again["state"], "prepared")

    def test_recovery_fails_closed_on_malformed_grant(self):
        # A corrupt (malformed JSON) grant referenced by a prepared intent is ambiguous
        # authority state: recovery must fail closed and never delete/archive destructively.
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        grant = prepared["grant"]
        intent_root = self.root / "reboot" / "intent"
        grant_path = self.root / "reboot" / "grants" / f"{grant}.json"
        (intent_root / f"{grant}.prepared.json").write_text(json.dumps(self._prepared_intent(grant)))
        grant_path.write_text("{ not valid json")
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_status(self.config)
        self.assertTrue((intent_root / f"{grant}.prepared.json").exists())
        self.assertTrue(grant_path.exists())

    def test_recovery_fails_closed_on_partial_grant(self):
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        grant = prepared["grant"]
        intent_root = self.root / "reboot" / "intent"
        grant_path = self.root / "reboot" / "grants" / f"{grant}.json"
        (intent_root / f"{grant}.prepared.json").write_text(json.dumps(self._prepared_intent(grant)))
        # Truncated/partial grant JSON (a JSON object that never closes).
        grant_path.write_text('{"schemaVersion": 1, "consumed": ')
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_status(self.config)
        self.assertTrue((intent_root / f"{grant}.prepared.json").exists())
        self.assertTrue(grant_path.exists())

    def test_recovery_fails_closed_on_missing_consumed_grant(self):
        # A prepared intent whose grant is missing (ambiguous authority) must fail closed,
        # never be reclassified as unconsumed and deleted.
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        grant = prepared["grant"]
        intent_root = self.root / "reboot" / "intent"
        grant_path = self.root / "reboot" / "grants" / f"{grant}.json"
        (intent_root / f"{grant}.prepared.json").write_text(json.dumps(self._prepared_intent(grant)))
        grant_path.unlink()
        with self.assertRaises(pop.PixelOperatorError):
            pop.reboot_status(self.config)
        self.assertTrue((intent_root / f"{grant}.prepared.json").exists())
        self.assertFalse(grant_path.exists())

    def test_recovery_fails_closed_on_wrong_owner_grant(self):
        # Under production ownership enforcement (non-root caller owns the grant), recovery
        # must fail closed rather than treat the grant as unconsumed and delete the intent.
        prepared = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        grant = prepared["grant"]
        intent_root = self.root / "reboot" / "intent"
        (intent_root / f"{grant}.prepared.json").write_text(json.dumps(self._prepared_intent(grant)))
        with mock.patch.object(pop, "TESTING", False), \
             mock.patch.object(pop, "require_dir", side_effect=lambda *a, **k: None), \
             mock.patch.object(pop, "require_file", side_effect=lambda *a, **k: None):
            with self.assertRaises(pop.PixelOperatorError):
                pop._recover_prepared(self.config, int(time.time()))
        self.assertTrue((intent_root / f"{grant}.prepared.json").exists())

    def test_archive_bounded_no_unbounded_accumulation(self):
        archive = self.root / "reboot" / "grants" / "archive"
        for i in range(pop.ARCHIVE_MAX_KEEP + 5):
            (archive / f"consumed-{i}.json").write_text(json.dumps({"i": i}))
        extra = self.root / "extra.json"
        extra.write_text("{}")
        pop._archive_entry(extra, archive, "consumed", pop.ARCHIVE_MAX_KEEP)
        remaining = list(archive.glob("consumed-*.json"))
        self.assertLessEqual(len(remaining), pop.ARCHIVE_MAX_KEEP)
        # A stale unconsumed grant is removed by the sweep (no unbounded accumulation).
        stale = pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        stale_path = self.root / "reboot" / "grants" / f"{stale['grant']}.json"
        record = json.loads(stale_path.read_text())
        record["expiresAt"] = int(time.time()) - 10
        stale_path.write_text(json.dumps(record))
        pop.reboot_prepare(self.config, "goal", self.evidence_sha)
        self.assertFalse(stale_path.exists())

    def test_archive_entry_no_replace_on_destination_collision(self):
        # A deterministic archive destination collision must fail closed: neither the
        # source nor the pre-existing archive entry may be replaced or removed.
        archive = self.root / "reboot" / "grants" / "archive"
        source = self.root / "src.json"
        source.write_text("source-payload")
        existing = archive / "consumed-deadbeef.json"
        existing.write_text("existing-payload")
        with mock.patch.object(pop.secrets, "token_hex", return_value="deadbeef"):
            with self.assertRaises(FileExistsError):
                pop._archive_entry(source, archive, "consumed", pop.ARCHIVE_MAX_KEEP)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_text(), "source-payload")
        self.assertEqual(existing.read_text(), "existing-payload")


if __name__ == "__main__":
    unittest.main()
