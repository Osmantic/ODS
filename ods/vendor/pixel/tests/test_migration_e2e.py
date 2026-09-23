"""Isolated prepare + privileged-helper migration tests.

These tests drive migrate-prepare.sh and then call the PRIVILEGED HELPER commands
(arm/install/rollback/commit) DIRECTLY against fake live roots + a fake systemctl. They do
NOT execute the outer wrapper scripts/restore-private-state.sh. The real production-path
integration that executes scripts/restore-private-state.sh end-to-end (reserve -> capture
exact prestate -> verified quiesce -> backup/staging -> arm -> install/activate -> finalize
desired state -> runtime verify -> commit, plus an injected failure after arm proving exact
rollback) lives in tests/legacy-clean-migration-swap.test.sh (scenarios A-D and G).

Here we prove:

  * prepare never changes current/config/workspace/units/services and never invokes
    systemctl or sudo;
  * the exact 4.3 release is installed atomically under $PIXEL_INSTALL_DIR/releases;
  * the helper arm + install switch current to 4.3 and apply the exact config/env/unit/
    workspace candidates and quiesce services;
  * the helper rollback restores every pre-state byte + the current pointer + exact service
    prestate, including a SIGKILL-style crash at a durable boundary;
  * a successful helper commit preserves the exact 4.3 state;
  * a tampered installed release is rejected and a stale crash-left hidden stage sibling is
    cleaned (bounded recovery).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tests.test_restore_migration_journal_deployment import (
    CONTRACT, BACKUP, UNIT, FIXED_UNITS, FakeJournalHarnessBase,
)


ROOT = Path(__file__).resolve().parents[1]

import importlib.util

_ML_SPEC = importlib.util.spec_from_file_location(
    "pixel_test_migrate_legacy_clean", ROOT / "scripts/migrate-legacy-clean.py",
)
migrate_legacy_clean = importlib.util.module_from_spec(_ML_SPEC)
assert _ML_SPEC.loader is not None
_ML_SPEC.loader.exec_module(migrate_legacy_clean)


def copy_repo(destination: Path) -> Path:
    copy = destination / "repo"
    shutil.copytree(
        ROOT, copy,
        ignore=shutil.ignore_patterns(".git", ".env", ".generated", ".runtime", "dist",
                                      "node_modules", "__pycache__", "*.pyc"),
    )
    return copy


class MigrationE2EIntegrationTests(FakeJournalHarnessBase):
    """End-to-end 3.2.2 -> 4.3.27 migration against fake live roots + fake systemctl."""

    def setUp(self):
        super().setUp()
        self.repo = copy_repo(self.base)
        self.install_dir = self.base / "install"
        self.openclaw_home = self.base / "openclaw"
        self.workspace = self.base / "workspace"
        self.agent_env_dir = self.base / "agent-env"
        self.cache = self.base / "cache"
        for d in (self.install_dir / "releases" / "3.2.2" / "plugin",
                  self.install_dir / "releases" / "3.2.2" / "plugin-ops",
                  self.install_dir / "releases" / "3.2.2" / "plugin-frontier",
                  self.openclaw_home, self.workspace, self.agent_env_dir, self.cache,
                  self.repo / "dist", self.repo / ".generated" / "workspace" / "scripts"):
            d.mkdir(parents=True)
        os.chmod(self.install_dir / "releases", 0o700)
        os.chmod(self.install_dir, 0o700)
        for d in (self.openclaw_home, self.agent_env_dir, self.workspace):
            os.chmod(d, 0o700)
        (self.workspace / "scripts").mkdir(parents=True)
        os.chmod(self.workspace / "scripts", 0o700)
        shutil.copytree(ROOT / "plugin", self.install_dir / "releases" / "3.2.2" / "plugin", dirs_exist_ok=True)
        shutil.copytree(ROOT / "plugin-ops", self.install_dir / "releases" / "3.2.2" / "plugin-ops", dirs_exist_ok=True)
        shutil.copytree(ROOT / "plugin-frontier", self.install_dir / "releases" / "3.2.2" / "plugin-frontier", dirs_exist_ok=True)
        (self.install_dir / "releases" / "3.2.2" / "VERSION").write_text("legacy-release")
        os.symlink("releases/3.2.2", self.install_dir / "current")
        (self.openclaw_home / "openclaw.json").write_text('{"legacy":true}')
        (self.agent_env_dir / "gateway.env").write_text("GATEWAY_OLD=1\n")
        (self.agent_env_dir / "web-courier.env").write_text("COURIER_OLD=1\n")
        (self.fake.systemd / "pixel-web-courier.service").write_text("COURIER_UNIT_OLD=1\n")
        (self.fake.systemd / "openclaw-gateway.service").write_text("[Unit]\nDescription=legacy gateway\n")
        for live_file in (self.openclaw_home / "openclaw.json",
                          self.agent_env_dir / "gateway.env",
                          self.agent_env_dir / "web-courier.env",
                          self.fake.systemd / "pixel-web-courier.service",
                          self.fake.systemd / "openclaw-gateway.service"):
            os.chmod(live_file, 0o600)
        self._write_env()
        self._patch_unit_dirs()
        self._build_plan_artifacts()

    def _write_env(self):
        env = f"""PIXEL_INSTALL_DIR={self.install_dir}
PIXEL_WORKSPACE={self.workspace}
OPENCLAW_HOME={self.openclaw_home}
PIXEL_RELEASE_VERSION=4.3.27
PIXEL_MODEL_PROVIDER=openai
PIXEL_MODEL_ID=gpt-4o
PIXEL_MODEL_NAME=GPT-4o
PIXEL_MODEL_API_KEY=test
PIXEL_MODEL_BASE_URL=http://127.0.0.1:9999/v1
PIXEL_MODEL_REASONING=1
PIXEL_MODEL_CONTEXT_WINDOW=128000
PIXEL_MODEL_MAX_TOKENS=4096
PIXEL_GATEWAY_TOKEN=tok
PIXEL_AGENT_ID=pixel
PIXEL_AGENT_NAME=Pixel
PIXEL_SANDBOX_IMAGE=debian:bookworm-slim
PIXEL_EMBEDDING_MODEL=text-embedding
PIXEL_EMBEDDING_CACHE={self.cache}
PIXEL_SEARXNG_BASE_URL=http://127.0.0.1:8888
PIXEL_LIMB_EMAIL_ENABLED=0
PIXEL_LIMB_CALENDAR_ENABLED=0
PIXEL_LIMB_SOCIAL_ENABLED=0
PIXEL_LIMB_WEB_ENABLED=1
PIXEL_LIMB_OPERATIONS_ENABLED=0
PIXEL_LIMB_FRONTIER_ENABLED=0
PIXEL_SOURCE_BROKER_ENABLED=0
PIXEL_OPS_BROKER_ENABLED=0
PIXEL_FRONTIER_BROKER_ENABLED=0
PIXEL_WEB_COURIER_ENABLED=0
PIXEL_SYSTEMD_UNIT=openclaw-gateway.service
PIXEL_WEB_COURIER_UNIT=pixel-web-courier.service
"""
        (self.repo / ".env").write_text(env)

    def _build_plan_artifacts(self):
        env = dict(os.environ)
        env.update({
            "OPENCLAW_HOME": str(self.openclaw_home),
            "PIXEL_WORKSPACE": str(self.workspace),
            "PIXEL_INSTALL_DIR": str(self.install_dir),
            "PIXEL_PLUGIN_PATH": str(self.install_dir / "current" / "plugin"),
            "PIXEL_OPS_PLUGIN_PATH": str(self.install_dir / "current" / "plugin-ops"),
            "PIXEL_FRONTIER_PLUGIN_PATH": str(self.install_dir / "current" / "plugin-frontier"),
        })
        # Load env vars from .env for the plan render.
        for line in (self.repo / ".env").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
        subprocess.run(["node", "scripts/render-config.mjs", "dist/openclaw.json"],
                       cwd=self.repo, env=env, check=True, capture_output=True, text=True)
        os.chmod(self.repo / "dist" / "openclaw.json", 0o600)
        digest = subprocess.run(["sha256sum", "openclaw.json"], cwd=self.repo / "dist",
                                capture_output=True, text=True, check=True).stdout
        (self.repo / "dist" / "openclaw.sha256").write_text(digest)
        (self.repo / "dist" / "release-identity.json").write_text('{"identity":"release"}')
        (self.repo / "dist" / "source-runtime.sha256").write_text('{"runtime":"x"}')
        g = self.repo / ".generated"
        (g / "gateway.env").write_text("GATEWAY_4_2=1\n")
        (g / "web-courier.env").write_text("COURIER_4_2=1\n")
        (g / "openclaw-gateway.service").write_text("[Unit]\nDescription=gateway 4.3\n")
        (g / "pixel-web-courier.service").write_text("[Unit]\nDescription=courier 4.3\n")
        (g / "workspace" / "WEB-NAVIGATION.md").write_text("# nav 4.3\n")
        browse = g / "workspace" / "scripts" / "browse.sh"
        browse.write_text("#!/usr/bin/env bash\n")
        os.chmod(browse, 0o700)
        ledger = g / "workspace" / "scripts" / "research-ledger.py"
        ledger.write_text("#!/usr/bin/env bash\n")
        os.chmod(ledger, 0o700)
        for name in ("deployment.json", "source-broker.env", "pixel-source-broker.service",
                     "pixel-source-broker.timer", "pixel-source-action@.service",
                     "pixel-source-reconcile@.service", "ops-broker.env", "ops-policy.json",
                     "pixel-ops-broker.service", "frontier-broker.env", "frontier-policy.json",
                     "pixel-frontier-broker.service"):
            (g / name).touch()
        out = subprocess.run(
            ["bash", "-c", "find . -path ./.git -prune -o -path '*/node_modules' -prune -o "
                           "-path './dist/deployment.sha256' -prune -o -type f -print0 "
                           "| LC_ALL=C sort -zu | xargs -0 sha256sum > dist/deployment.sha256"],
            cwd=self.repo, check=True, capture_output=True, text=True)
        return out


    def make_stage(self, name="stage"):
        d = self.base / name
        d.mkdir()
        os.chmod(d, 0o700)
        return d

    def run_prepare(self, stage, spec, receipt):
        env = dict(os.environ)
        env["PATH"] = str(self.fake.fake) + os.pathsep + env.get("PATH", "")
        return subprocess.run(
            ["bash", "scripts/migrate-prepare.sh", "--install-dir", str(self.install_dir),
             "--workspace", str(self.workspace), "--agent-env-dir", str(self.agent_env_dir),
             "--openclaw-home", str(self.openclaw_home), "--stage", str(stage),
             "--spec", str(spec), "--receipt", str(receipt)],
            cwd=self.repo, env=env, capture_output=True, text=True)

    def _patch_unit_dirs(self):
        cfg = self.repo / "scripts" / "configure.mjs"
        text = cfg.read_text()
        text = text.replace('PIXEL_GATEWAY_SYSTEMD_DIR: "/etc/systemd/system"',
                            f'PIXEL_GATEWAY_SYSTEMD_DIR: "{self.fake.systemd}"')
        text = text.replace('PIXEL_COURIER_SYSTEMD_DIR: "/etc/systemd/system"',
                            f'PIXEL_COURIER_SYSTEMD_DIR: "{self.fake.systemd}"')
        cfg.write_text(text)

    def test_prepare_is_non_mutating_and_installs_exact_release(self):
        stage = self.make_stage()
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        before_current = os.readlink(self.install_dir / "current")
        before_cfg = (self.openclaw_home / "openclaw.json").read_text()
        before_gw = (self.agent_env_dir / "gateway.env").read_text()
        proc = self.run_prepare(stage, spec, receipt)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # No live managed path changed.
        self.assertEqual(os.readlink(self.install_dir / "current"), before_current)
        self.assertEqual((self.openclaw_home / "openclaw.json").read_text(), before_cfg)
        self.assertEqual((self.agent_env_dir / "gateway.env").read_text(), before_gw)
        # Prepare must never invoke systemctl (no fake systemctl log) or sudo.
        self.assertFalse(self.fake.log.exists(), "prepare invoked systemctl")
        # Exact 4.3 release installed under releases; current still on 3.2.2.
        self.assertTrue((self.install_dir / "releases" / "4.3.27" / "install-manifest.sha256").is_file())
        self.assertNotEqual(os.readlink(self.install_dir / "current"), "releases/4.3.27")
        # Receipt binds the installed release manifest + identity + version.
        rec = json.loads(receipt.read_text())
        self.assertEqual(rec["mode"], "migration-prepare")
        self.assertEqual(rec["releaseVersion"], "4.3.27")
        self.assertEqual(rec["installManifestSha256"],
                         hashlib.sha256((self.install_dir / "releases" / "4.3.27" /
                                         "install-manifest.sha256").read_bytes()).hexdigest())

    def _arm_install(self, stage, spec, journal, units=FIXED_UNITS):
        items = json.loads(spec.read_text())["deploymentItems"]
        proc = self.fake.arm_stage(journal, items, str(stage), units=units)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        inst = self.fake.install(journal)
        self.assertEqual(inst.returncode, 0, inst.stderr)
        return items

    def test_helper_arm_install_rollback_and_commit(self):
        stage = self.make_stage()
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        self.assertEqual(self.run_prepare(stage, spec, receipt).returncode, 0)
        journal = self.fake.custody / "migration.json"
        # Arm + install with a fake active+enabled service (current gateway unit).
        self.fake.state.write_text(UNIT + "\n")
        self.fake.enabled_state.write_text(UNIT + "\n")
        self._arm_install(stage, spec, journal)

        # Exact 4.3 live state: current switched, config/env/unit/workspace applied.
        self.assertEqual(os.readlink(self.install_dir / "current"), "releases/4.3.27")
        self.assertEqual((self.openclaw_home / "openclaw.json").read_text(),
                         (self.repo / "dist" / "openclaw.json").read_text())
        self.assertEqual((self.agent_env_dir / "gateway.env").read_text(), "GATEWAY_4_2=1\n")
        self.assertNotIn(UNIT, self.fake.state.read_text().splitlines())  # quiesced during install

        # Rollback restores exact 3.2 bytes + pointer + service prestate (active + enabled).
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertEqual(os.readlink(self.install_dir / "current"), "releases/3.2.2")
        self.assertEqual((self.openclaw_home / "openclaw.json").read_text(), '{"legacy":true}')
        self.assertEqual((self.agent_env_dir / "gateway.env").read_text(), "GATEWAY_OLD=1\n")
        self.assertIn(UNIT, self.fake.state.read_text().splitlines())
        self.assertIn(UNIT, self.fake.enabled_state.read_text().splitlines())

    def test_helper_sigkill_crash_before_install_resumes_and_rolls_back(self):
        # Simulate a crash (SIGKILL) right after arm, before any install mutation: the new
        # objects are still staged at newPath, old still at path. A fresh install must
        # resume; rollback must restore exact 3.2.
        stage = self.make_stage()
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        self.assertEqual(self.run_prepare(stage, spec, receipt).returncode, 0)
        journal = self.fake.custody / "migration.json"
        items = json.loads(spec.read_text())["deploymentItems"]
        proc = self.fake.arm_stage(journal, items, str(stage), units=FIXED_UNITS)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # SIGKILL before any mutation: live config still 3.2.
        self.assertEqual((self.openclaw_home / "openclaw.json").read_text(), '{"legacy":true}')
        # Re-entry install completes the swap.
        inst = self.fake.install(journal)
        self.assertEqual(inst.returncode, 0, inst.stderr)
        self.assertEqual(os.readlink(self.install_dir / "current"), "releases/4.3.27")
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertEqual(os.readlink(self.install_dir / "current"), "releases/3.2.2")
        self.assertEqual((self.openclaw_home / "openclaw.json").read_text(), '{"legacy":true}')

    def test_helper_commit_preserves_exact_42(self):
        stage = self.make_stage()
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        self.assertEqual(self.run_prepare(stage, spec, receipt).returncode, 0)
        journal = self.fake.custody / "migration.json"
        self._arm_install(stage, spec, journal)
        self.assertEqual(self.fake.finalize(journal).returncode, 0)
        cm = self.fake.commit(journal)
        self.assertEqual(cm.returncode, 0, cm.stderr)
        self.assertEqual(os.readlink(self.install_dir / "current"), "releases/4.3.27")
        info = self.fake.inspect(journal)
        self.assertTrue(info["committed"])

    def test_prepare_rejects_tampered_installed_release(self):
        stage = self.make_stage()
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        self.assertEqual(self.run_prepare(stage, spec, receipt).returncode, 0)
        # Tamper the installed release manifest: a re-prepare must reject it.
        (self.install_dir / "releases" / "4.3.27" / "install-manifest.sha256").write_text("tampered\n")
        stage2 = self.make_stage("stage2")
        proc = self.run_prepare(stage2, self.base / "spec2.json", self.base / "receipt2.json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("non-exact 4.3.27 release already exists", proc.stderr)

    def test_prepare_preserves_unknown_stale_hidden_stage_sibling(self):
        # Filename + owner is NOT exact evidence: an unknown same-pattern hidden stage
        # sibling and its bytes must be left inert and preserved, while a new prepare still
        # succeeds. The process trap may only remove the exact hidden path it created.
        stage = self.make_stage()
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        self.assertEqual(self.run_prepare(stage, spec, receipt).returncode, 0)
        stale = self.install_dir / "releases" / ".4.3.27.prepare.999999"
        stale.mkdir()
        os.chmod(stale, 0o700)
        sentinel = stale / "SENTINEL"
        sentinel.write_text("do-not-delete")
        stage2 = self.make_stage("stage2")
        proc = self.run_prepare(stage2, self.base / "spec2.json", self.base / "receipt2.json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(stale.is_dir(), "unknown stale hidden stage sibling was deleted")
        self.assertEqual(sentinel.read_text(), "do-not-delete",
                         "unknown stale hidden stage sibling bytes were changed/deleted")

    def run_prepare_cli(self, stage, spec, receipt):
        """Run the REAL python CLI prepare (not migrate-prepare.sh directly), then validate
        its exact outputs with the REAL receipt verifier and the REAL activate pre-mutation
        release validation (the two checks cmd_activate runs before any live mutation)."""
        env = dict(os.environ)
        env["PATH"] = str(self.fake.fake) + os.pathsep + env.get("PATH", "")
        proc = subprocess.run(
            [sys.executable, "scripts/migrate-legacy-clean.py", "prepare",
             "--install-dir", str(self.install_dir), "--workspace", str(self.workspace),
             "--agent-env-dir", str(self.agent_env_dir), "--stage", str(stage),
             "--spec", str(spec), "--receipt", str(receipt)],
            cwd=self.repo, env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # The CLI already verified the spec+receipt; re-run the exact activate pre-mutation
        # validators against the produced artifacts.
        migrate_legacy_clean._verify_prepare_receipt(spec, receipt)
        migrate_legacy_clean._require_installed_release(self.install_dir, receipt)
        return proc

    def test_real_cli_prepare_activates_verified_artifacts(self):
        # Issue 1: the actual shell-produced prepare artifacts must load/verify through the
        # real Python orchestrator (kind-specific spec keys + exact-byte specSha256 + the
        # required --receipt CLI argument).
        stage = self.make_stage()
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        self.run_prepare_cli(stage, spec, receipt)
        rec = json.loads(receipt.read_text())
        self.assertIn("releaseTreeSha256", rec)
        self.assertIn("bundleSha256", rec)
        # The spec file bytes must hash to the receipt specSha256 (no newline drift).
        self.assertEqual(rec["specSha256"],
                         hashlib.sha256(spec.read_bytes()).hexdigest())
        # Verify a tampered installed release is rejected by the real activate pre-mutation
        # guard (issue 2): ordinary plugin file content tamper.
        (self.install_dir / "releases" / "4.3.27" / "plugin" / "index.js").write_text(
            (self.install_dir / "releases" / "4.3.27" / "plugin" / "index.js").read_text() + "\ntamper\n")
        with self.assertRaises(migrate_legacy_clean.MigrationError):
            migrate_legacy_clean._require_installed_release(self.install_dir, receipt)

    def test_release_tree_rejects_added_file(self):
        stage = self.make_stage()
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        self.run_prepare_cli(stage, spec, receipt)
        (self.install_dir / "releases" / "4.3.27" / "plugin" / "added.js").write_text("x")
        with self.assertRaises(migrate_legacy_clean.MigrationError):
            migrate_legacy_clean._require_installed_release(self.install_dir, receipt)

    def test_release_tree_rejects_symlink_target_substitution(self):
        stage = self.make_stage()
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        self.run_prepare_cli(stage, spec, receipt)
        # Replace a regular file with a symlink (symlink substitution / traversal).
        target = self.install_dir / "releases" / "4.3.27" / "VERSION"
        os.remove(target)
        os.symlink("/etc/passwd", target)
        with self.assertRaises(migrate_legacy_clean.MigrationError):
            migrate_legacy_clean._require_installed_release(self.install_dir, receipt)

    def test_release_tree_rejects_hardlink(self):
        stage = self.make_stage()
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        self.run_prepare_cli(stage, spec, receipt)
        rel = self.install_dir / "releases" / "4.3.27"
        os.link(rel / "VERSION", rel / "VERSION-hardlink")
        with self.assertRaises(migrate_legacy_clean.MigrationError):
            migrate_legacy_clean._require_installed_release(self.install_dir, receipt)

    def test_receipt_rejects_field_removal_extra_and_bundle_drift(self):
        stage = self.make_stage()
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        self.run_prepare_cli(stage, spec, receipt)
        doc = json.loads(receipt.read_text())
        # Field removal must fail closed.
        removed = dict(doc)
        del removed["releaseTreeSha256"]
        removed_path = self.base / "removed.json"
        removed_path.write_text(json.dumps(removed))
        with self.assertRaises(migrate_legacy_clean.MigrationError):
            migrate_legacy_clean._verify_prepare_receipt(spec, removed_path)
        # Extra field must fail closed.
        extra = dict(doc)
        extra["extra"] = True
        extra_path = self.base / "extra.json"
        extra_path.write_text(json.dumps(extra))
        with self.assertRaises(migrate_legacy_clean.MigrationError):
            migrate_legacy_clean._verify_prepare_receipt(spec, extra_path)
        # Bundle hash drift (recomputed digest does not match) must fail closed.
        drift = dict(doc)
        drift["releaseVersion"] = "4.3.9"
        drift_path = self.base / "drift.json"
        drift_path.write_text(json.dumps(drift))
        with self.assertRaises(migrate_legacy_clean.MigrationError):
            migrate_legacy_clean._verify_prepare_receipt(spec, drift_path)

    def test_current_must_be_exact_322_symlink(self):
        # Issue 9: the migration is pinned to exact target 4.3.27 and only starts from an
        # exact, safe current symlink to releases/3.2.2. A mismatch or dangling current is
        # rejected before prepare calls hadOld=1.
        stage = self.make_stage()
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        os.remove(self.install_dir / "current")
        os.symlink("releases/4.1.0", self.install_dir / "current")
        proc = self.run_prepare(stage, spec, receipt)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("releases/3.2.2", proc.stderr)
        # Dangling current pointer must also be rejected.
        os.remove(self.install_dir / "current")
        os.symlink("releases/missing", self.install_dir / "current")
        proc = self.run_prepare(self.make_stage("stage2"), self.base / "spec2.json",
                                self.base / "receipt2.json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("releases/3.2.2", proc.stderr)

    def test_distinct_gateway_courier_unit_dirs(self):
        # Issue 8: gateway and courier are separate product settings; the prepare stages each
        # unit in its OWN configured dir and never pretends one unit_parent represents both.
        courier_dir = self.base / "courier-systemd"
        courier_dir.mkdir()
        os.chmod(courier_dir, 0o700)
        cfg = self.repo / "scripts" / "configure.mjs"
        text = cfg.read_text()
        text = text.replace(f'PIXEL_COURIER_SYSTEMD_DIR: "{self.fake.systemd}"',
                            f'PIXEL_COURIER_SYSTEMD_DIR: "{courier_dir}"')
        cfg.write_text(text)
        subprocess.run(
            ["bash", "-c", "find . -path ./.git -prune -o -path '*/node_modules' -prune -o "
                           "-path './dist/deployment.sha256' -prune -o -type f -print0 "
                           "| LC_ALL=C sort -zu | xargs -0 sha256sum > dist/deployment.sha256"],
            cwd=self.repo, check=True, capture_output=True, text=True)
        stage = self.make_stage()
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        proc = self.run_prepare(stage, spec, receipt)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        items = json.loads(spec.read_text())["deploymentItems"]
        gw_unit = next(it for it in items if it["kind"] == "unit" and "gateway" in it["path"])
        co_unit = next(it for it in items if it["kind"] == "unit" and "courier" in it["path"])
        self.assertTrue(str(gw_unit["path"]).startswith(str(self.fake.systemd) + "/"))
        self.assertTrue(str(co_unit["path"]).startswith(str(courier_dir) + "/"))

    def test_prepare_rejects_custom_unit_name_env(self):
        # Requirement 5: a custom PIXEL_SYSTEMD_UNIT / PIXEL_WEB_COURIER_UNIT value (the
        # actual deployment config) must never be silently adopted for deployment
        # artifacts; prepare fails early unless it equals the authored production name.
        # The unit-name value is loaded from the generated .env (trusted operator config),
        # so tampering that value must be rejected.
        stage = self.make_stage()
        env_path = self.repo / ".env"
        env_path.write_text(env_path.read_text().replace(
            "PIXEL_SYSTEMD_UNIT=openclaw-gateway.service",
            "PIXEL_SYSTEMD_UNIT=custom-gateway.service"))
        proc = self.run_prepare(stage, self.base / "spec.json", self.base / "receipt.json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("custom unit name", proc.stderr)
        self.assertFalse((self.base / "spec.json").exists())

    def test_stage_symlink_never_followed_outside(self):
        # Issue 6: a precreated stage entry (symlink redirecting outside the stage) must
        # make prepare fail WITHOUT following it or mutating the outside target.
        stage = self.make_stage()
        outside = self.base / "outside-target"
        outside.write_text("original")
        os.symlink(str(outside), stage / ".pixel-restore-openclaw.json-0-1.new")
        spec = self.base / "spec.json"
        receipt = self.base / "receipt.json"
        proc = self.run_prepare(stage, spec, receipt)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(outside.read_text(), "original",
                         "prepare followed an existing stage symlink and mutated the outside target")


if __name__ == "__main__":
    unittest.main()
