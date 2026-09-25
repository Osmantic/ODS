import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "deploy/release-operator"
PROVISION = PKG / "provision-pixel-operator.sh"
BUILDER = PKG / "build-runtime-snapshot.mjs"

REQUIRED = [
    "managed.py", "dispatch.py", "client.py",
    "pixel_operator.py", "pixel_release_grammar.py",
]


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def build_snapshot():
    out = Path(tempfile.mkdtemp(prefix="pixel-harness-snapshot-"))
    tree = subprocess.run(
        ["node", str(BUILDER), str(ROOT), str(out)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if tree.returncode != 0:
        raise RuntimeError(f"snapshot build failed: {tree.stderr.decode()}")
    tree_sha = tree.stdout.decode().strip()
    return out, tree_sha


def make_runtime(root, files):
    """Build a minimal valid runtime snapshot with a distinct manifest tree SHA."""
    runtime = root / f"snap-{os.urandom(4).hex()}"
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


class ProvisioningHarnessTests(unittest.TestCase):
    """Rootless, production-byte-faithful provisioning harness.

    Executes the actual provision-pixel-operator.sh against redirected prefixes as a
    non-root user with TESTING set, then proves the exact final config runtimePath, the
    installed managed routing, the owner-side client grammar, atomic retry, retained prior
    runtime, staged-source swap protection, and no sudoers grant before validations pass.
    """

    @classmethod
    def setUpClass(cls):
        cls.snapshot, cls.tree_sha = build_snapshot()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.staging = self.root / "staging"
        self.staging.mkdir()
        for f in REQUIRED:
            shutil.copyfile(PKG / f, self.staging / f)
        self.runtime = self.snapshot
        self.state_dir = self.root / "var/lib/pixel-release-operator"
        self.state_dir.mkdir(parents=True)
        self.libexec = self.root / "usr/local/libexec"
        self.config_dir = self.root / "etc/pixel-release-operator"
        self.runtime_parent = self.root / "opt/pixel-release-runtime"
        self.owner_dir = self.libexec / "pixel-release-owner"
        self.sudoers_file = self.root / "etc/sudoers.d/pixel-release-operator-bundle"
        self.fake_visudo = self.root / "fake-visudo"
        self.fake_visudo.write_text(
            "#!/bin/sh\n"
            'if [ "${VISUDO_FAIL:-}" = 1 ]; then echo "reject" >&2; exit 1; fi\n'
            'if [ "$1" = "-cf" ]; then file=$2; else file=$1; fi\n'
            'grep -q NOPASSWD "$file" || { echo "no NOPASSWD" >&2; exit 1; }\n'
            "exit 0\n"
        )
        self.fake_visudo.chmod(0o755)
        self._write_config(self.tree_sha)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_config(self, tree_sha):
        config = {
            "schemaVersion": 1, "ownerUid": 1000, "pixelWorkUid": 1001, "pixelWorkGid": 1001,
            "inboxRoot": str(self.root / "inbox"), "bundleRoot": str(self.root / "bundles"),
            "runtimePath": "/initial", "runtimeTreeSha256": tree_sha,
            "nodePath": "/usr/bin/node", "systemctlPath": "/usr/bin/systemctl",
            "systemdAnalyzePath": "/usr/bin/systemd-analyze",
            "receiptRoot": str(self.root / "receipts"), "rebootRoot": str(self.root / "reboot"),
            "brokerBytes": {
                "backupRoot": str(self.root / "broker-bytes"),
                "limbs": {
                    "source": {"enabled": True, "installDir": str(self.root / "source-broker"), "directPathEnabled": False},
                    "ops": {"enabled": True, "installDir": str(self.root / "ops-broker")},
                    "frontier": {"enabled": False, "installDir": str(self.root / "frontier-broker")},
                },
            },
            "readersEnabled": False,
        }
        (self.staging / "pixel-config.json").write_text(json.dumps(config))

    def _env(self, **extra):
        env = {
            **os.environ,
            "PIXEL_PROVISION_TESTING": "1",
            "PIXEL_RELEASE_MANAGED_TESTING": "1",
            "PIXEL_PROVISION_STATE_DIR": str(self.state_dir),
            "PIXEL_PROVISION_LIBEXEC": str(self.libexec),
            "PIXEL_PROVISION_CONFIG_DIR": str(self.config_dir),
            "PIXEL_PROVISION_RUNTIME_PARENT": str(self.runtime_parent),
            "PIXEL_PROVISION_SUDOERS_FILE": str(self.sudoers_file),
            "PIXEL_PROVISION_OWNER_DIR": str(self.owner_dir),
            "PIXEL_PROVISION_VISUDO": str(self.fake_visudo),
            "PIXEL_OPERATOR_CONFIG": str(self.config_dir / "pixel.json"),
            "PIXEL_OPERATOR_CONFIG_DIR": str(self.config_dir),
            "PIXEL_OPERATOR_LIBEXEC": str(self.libexec),
            "PIXEL_OPERATOR_RUNTIME_PARENT": str(self.runtime_parent),
        }
        env.update(extra)
        return env

    def _run(self, env=None, staging=None, runtime=None):
        return subprocess.run(
            ["bash", str(PROVISION), "pixel-release-transport", str(staging or self.staging), str(runtime or self.runtime)],
            env=env or self._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def _read_installed_config(self):
        path = self.config_dir / "pixel.json"
        self.assertTrue(path.is_file(), "final config was not installed")
        return json.loads(path.read_text())

    # --- defect 1: exact final config runtimePath from the shell data flow ---
    def test_exact_final_config_runtime_path(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        installed = self._read_installed_config()
        expected = (self.runtime_parent / self.tree_sha).resolve()
        self.assertEqual(installed["runtimePath"], str(expected))
        # The installed runtime dir is the exact content-addressed path.
        self.assertTrue(Path(installed["runtimePath"]).is_dir())

    # --- defect 2: installed managed routing + owner-side client grammar ---
    def test_installed_managed_routing_and_owner_grammar(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        # The updated managed helper (routing bundle/service/reboot/broker-bytes) is installed byte-exact.
        self.assertEqual(
            (self.libexec / "pixel-release-managed").read_bytes(),
            (PKG / "managed.py").read_bytes(),
        )
        # Dispatcher and operator/grammar are installed byte-exact.
        self.assertEqual((self.libexec / "pixel-release-dispatch").read_bytes(), (PKG / "dispatch.py").read_bytes())
        self.assertEqual((self.libexec / "pixel_operator.py").read_bytes(), (PKG / "pixel_operator.py").read_bytes())
        self.assertEqual((self.libexec / "pixel_release_grammar.py").read_bytes(), (PKG / "pixel_release_grammar.py").read_bytes())
        # Owner-side client + adjacent grammar surface accept the new verbs.
        self.assertEqual((self.owner_dir / "client.py").read_bytes(), (PKG / "client.py").read_bytes())
        self.assertEqual((self.owner_dir / "pixel_release_grammar.py").read_bytes(), (PKG / "pixel_release_grammar.py").read_bytes())
        self.assertEqual(stat.S_IMODE((self.config_dir / "pixel.json").stat().st_mode), 0o600)

    # --- atomic retry (idempotent) ---
    def test_atomic_retry_is_idempotent(self):
        first = self._run()
        self.assertEqual(first.returncode, 0, first.stderr.decode())
        first_config = self._read_installed_config()
        second = self._run()
        self.assertEqual(second.returncode, 0, second.stderr.decode())
        second_config = self._read_installed_config()
        self.assertEqual(first_config, second_config)
        self.assertEqual(list(self.state_dir.glob("provision-stage.*")), [])

    # --- retained prior runtime + switch to a second snapshot ---
    def test_retained_prior_runtime(self):
        first = self._run()
        self.assertEqual(first.returncode, 0, first.stderr.decode())
        self.assertTrue((self.runtime_parent / self.tree_sha).is_dir())
        # Build a second, distinct runtime snapshot and re-provision against it.
        snap2, sha2 = make_runtime(self.root, {"deploy/work-controller/a.mjs": b"export const a=2;"})
        try:
            self.assertNotEqual(self.tree_sha, sha2)
            self._write_config(sha2)
            result = self._run(runtime=snap2)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            installed = self._read_installed_config()
            self.assertEqual(installed["runtimePath"], str((self.runtime_parent / sha2).resolve()))
            # Both versions are retained under the runtime parent.
            self.assertTrue((self.runtime_parent / self.tree_sha).is_dir())
            self.assertTrue((self.runtime_parent / sha2).is_dir())
        finally:
            shutil.rmtree(snap2, ignore_errors=True)

    # --- defect 3: deterministic staged-source swap uses the authorized snapshot ---
    def test_staged_source_swap_uses_authorized_snapshot(self):
        authorized = (PKG / "pixel_operator.py").read_bytes()
        malicious = self.root / "malicious.py"
        malicious.write_bytes(b"#!/usr/bin/python3\nraise SystemExit('pwned')\n")
        result = self._run(env=self._env(
            PIXEL_PROVISION_SWAP_AFTER_COPY="pixel_operator.py",
            PIXEL_PROVISION_SWAP_SOURCE=str(malicious),
        ))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        # The installed byte equals the authorized root-private snapshot, not the swapped-in
        # caller-controlled content.
        installed = (self.libexec / "pixel_operator.py").read_bytes()
        self.assertEqual(installed, authorized)
        self.assertNotEqual(installed, malicious.read_bytes())

    # --- defect 3: invalid source (single-link violation) fails before authority ---
    def test_single_link_violation_fails_before_authority(self):
        os.link(self.staging / "pixel-config.json", self.staging / "pixel-config-alias.json")
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.sudoers_file.exists())
        self.assertFalse((self.config_dir / "pixel.json").exists())

    # --- defect 9: no sudoers grant before all validations pass ---
    def test_no_sudoers_grant_when_staged_validation_fails(self):
        self._write_config("f" * 64)  # runtimeTreeSha256 does not match the snapshot
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.sudoers_file.exists())
        self.assertFalse((self.config_dir / "pixel.json").exists())

    # --- defect 9: sudoers interruption leaves no invalid/truncated authorization file ---
    def test_sudoers_visudo_failure_leaves_no_grant(self):
        result = self._run(env=self._env(VISUDO_FAIL="1"))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.sudoers_file.exists())
        self.assertFalse(list((self.root / "etc/sudoers.d").glob(".provision-sudoers.*")))

    # --- defect 9: successful provisioning installs a valid sudoers grant ---
    def test_successful_provisioning_installs_valid_sudoers(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertTrue(self.sudoers_file.exists())
        content = self.sudoers_file.read_text()
        self.assertIn("pixel-release-transport ALL=(root) NOPASSWD:", content)
        self.assertIn("pixel-release-managed", content)
        self.assertEqual(stat.S_IMODE(self.sudoers_file.stat().st_mode), 0o440)


if __name__ == "__main__":
    unittest.main()
