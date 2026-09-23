"""Isolated unit tests for deploy/mesh/migrate_tower2_discord.py
(mesh-discord-handoff-0.1.0) — focused contract coverage.

These tests never use a real Discord, token, systemd, SSH, live profile, or
credential. They run the utility in a temporary home with mocked subprocess
(so `openclaw config validate` and `systemctl restart` never run for real).
All paths, tokens, and IDs are synthetic fixtures.
"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "mesh_migrate", Path(__file__).resolve().parents[1] / "deploy/mesh/migrate_discord.py"
)
mig = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = mig
SPEC.loader.exec_module(mig)

FAKE_TOKEN = "SYNTHETIC-DISCORD-TOKEN-FOR-UNIT-TESTS-ONLY-0001"


def build_fixture(home: Path) -> dict:
    """Create restricted + mesh states and a fake executable OpenClaw binary."""
    restricted_state = home / ".openclaw"
    mesh_state = home / ".openclaw-mesh"
    workspace = restricted_state / "workspace-pixel"
    workspace.mkdir(parents=True, exist_ok=True)
    mesh_state.mkdir(parents=True, exist_ok=True)
    npm_projects = restricted_state / "npm" / "projects"
    (npm_projects / "openclaw-discord-x" / "node_modules" / "@openclaw" / "discord").mkdir(parents=True, exist_ok=True)

    live = {
        "channels": {
            "telegram": {"enabled": True, "token": "telegram-synthetic"},
            "discord": {"enabled": True, "token": FAKE_TOKEN},
        },
        "plugins": {
            "allow": ["telegram"],
            "entries": {"discord": {"enabled": True}},
        },
        "messages": {"policy": "keep-me"},
        "tools": {"alsoAllow": ["custom-tool"], "web": {"enabled": True}},
        "agents": {"list": [{"id": "pixel", "name": "Restricted Pixel", "skills": ["audit"], "workspace": "x"}]},
    }
    mesh = {
        "channels": {},
        "plugins": {"allow": [], "load": {"paths": []}, "entries": {}},
        "agents": {"list": [{"id": "pixel", "name": "Mesh Pixel", "workspace": "mesh-ws", "heartbeat": {"every": "0m"}}]},
        "tools": {"exec": {"mode": "full"}},
    }
    (restricted_state / "openclaw.json").write_text(json.dumps(live, indent=2), encoding="utf-8")
    (mesh_state / "openclaw.json").write_text(json.dumps(mesh, indent=2), encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# Original instructions\n\nKeep me.\n", encoding="utf-8")
    binary = home / "bin" / "openclaw"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    return {
        "restricted_state": restricted_state,
        "mesh_state": mesh_state,
        "workspace": workspace,
        "binary": binary,
    }


def env_for(home: Path, backup_root: Path) -> dict:
    return {
        **os.environ,
        mig.HOME_ENV: str(home),
        mig.BIN_ENV: str(home / "bin" / "openclaw"),
        mig.RESTRICTED_STATE_ENV: str(home / ".openclaw"),
        mig.MESH_STATE_ENV: str(home / ".openclaw-mesh"),
        mig.BACKUP_ROOT_ENV: str(backup_root),
    }


def capture_run(home: Path, backup_root: Path, *argv: str) -> tuple[int, str, mock.Mock]:
    """Run mig.main capturing stdout and the mocked subprocess.run."""
    buffer = io.StringIO()
    with mock.patch.dict(os.environ, env_for(home, backup_root), clear=False):
        with mock.patch.object(mig.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, b"", b"")
            with contextlib.redirect_stdout(buffer):
                code = mig.main(list(argv))
    return code, buffer.getvalue().strip(), run


class DryRunTests(unittest.TestCase):
    def test_dry_run_mutates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fixture = build_fixture(home)
            backup_root = home / ".local" / "state" / "pixel-mesh" / "discord-migration"
            code, _, run = capture_run(home, backup_root)
            self.assertEqual(code, 0)
            self.assertFalse(backup_root.exists())
            live_after = json.loads((fixture["restricted_state"] / "openclaw.json").read_text())
            self.assertTrue(live_after["channels"]["discord"]["enabled"])
            mesh_after = json.loads((fixture["mesh_state"] / "openclaw.json").read_text())
            self.assertNotIn("discord", mesh_after.get("channels", {}))
            self.assertEqual((fixture["workspace"] / "AGENTS.md").read_text(), "# Original instructions\n\nKeep me.\n")
            self.assertEqual(run.call_count, 0)


class ApplyTests(unittest.TestCase):
    def test_apply_preserves_unrelated_config_and_toggles_only_discord(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fixture = build_fixture(home)
            backup_root = home / ".local" / "state" / "pixel-mesh" / "discord-migration"
            code, out, run = capture_run(home, backup_root, "--apply")
            self.assertEqual(code, 0)
            mesh = json.loads((fixture["mesh_state"] / "openclaw.json").read_text())
            live = json.loads((fixture["restricted_state"] / "openclaw.json").read_text())
            # Discord enabled only on mesh, disabled on restricted.
            self.assertTrue(mesh["channels"]["discord"]["enabled"])
            self.assertEqual(mesh["channels"]["discord"]["token"], FAKE_TOKEN)
            self.assertFalse(live["channels"]["discord"]["enabled"])
            # Unrelated data preserved on restricted (only Discord toggled).
            self.assertEqual(live["channels"]["telegram"]["token"], "telegram-synthetic")
            self.assertEqual(live["tools"]["alsoAllow"], ["custom-tool"])
            self.assertEqual(live["messages"], {"policy": "keep-me"})
            self.assertEqual(live["plugins"]["allow"], ["telegram"])
            # Mesh profile customizations are preserved while the established
            # restricted Pixel workspace carries persona/memory into the handoff.
            self.assertNotIn("messages", mesh)
            self.assertEqual(mesh["tools"]["exec"]["mode"], "full")
            self.assertEqual(mesh["agents"]["list"][0]["workspace"],
                             str(fixture["workspace"]))
            self.assertEqual(mesh["agents"]["list"][0]["name"], "Mesh Pixel")
            self.assertTrue(any("openclaw-discord" in p for p in mesh["plugins"]["load"]["paths"]))
            # Instructions got trusted override prepended once.
            text = (fixture["workspace"] / "AGENTS.md").read_text()
            self.assertTrue(text.startswith("# TRUSTED OWNER MESH OVERRIDE"))
            self.assertIn("Keep me.", text)
            payload = json.loads(out)
            self.assertTrue(payload["migrated"])
            # Mesh user service was restarted.
            self.assertTrue(any(a[0] and a[0][0] and a[0][0][0] == "systemctl" and "restart" in a[0][0] for a in run.call_args_list))

    def test_private_backup_dir_and_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            build_fixture(home)
            backup_root = home / ".local" / "state" / "pixel-mesh" / "discord-migration"
            self.assertEqual(capture_run(home, backup_root, "--apply")[0], 0)
            backup_dir = next(backup_root.iterdir())
            self.assertEqual(backup_dir.stat().st_mode & 0o777, 0o700)
            for name in ("restricted-openclaw.json", "mesh-openclaw.json", "AGENTS.md", "manifest.json"):
                self.assertEqual((backup_dir / name).stat().st_mode & 0o777, 0o600)
            manifest = json.loads((backup_dir / "manifest.json").read_text())
            self.assertEqual(manifest["schemaVersion"], mig.BACKUP_SCHEMA)

    def test_preflight_failure_blocks_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fixture = build_fixture(home)
            (fixture["restricted_state"] / "openclaw.json").unlink()
            backup_root = home / ".local" / "state" / "pixel-mesh" / "discord-migration"
            with mock.patch.dict(os.environ, env_for(home, backup_root), clear=False):
                with self.assertRaises(SystemExit):
                    mig.main(["--apply"])
            self.assertFalse(backup_root.exists())

    def test_validation_failure_no_live_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fixture = build_fixture(home)
            backup_root = home / ".local" / "state" / "pixel-mesh" / "discord-migration"
            live_before = (fixture["restricted_state"] / "openclaw.json").read_bytes()
            mesh_before = (fixture["mesh_state"] / "openclaw.json").read_bytes()
            agents_before = (fixture["workspace"] / "AGENTS.md").read_bytes()
            real_run = mig.subprocess.run

            def failing_run(argv, **kwargs):
                if kwargs.get("check"):
                    raise subprocess.CalledProcessError(1, argv)
                return real_run(argv, **kwargs)

            with mock.patch.dict(os.environ, env_for(home, backup_root), clear=False):
                with mock.patch.object(mig.subprocess, "run", side_effect=failing_run):
                    with self.assertRaises(subprocess.CalledProcessError):
                        mig.main(["--apply"])
            # Validation fails before any live write.
            self.assertEqual((fixture["restricted_state"] / "openclaw.json").read_bytes(), live_before)
            self.assertEqual((fixture["mesh_state"] / "openclaw.json").read_bytes(), mesh_before)
            self.assertEqual((fixture["workspace"] / "AGENTS.md").read_bytes(), agents_before)
            # Candidate validation fails before even private backup state is created.
            self.assertFalse(backup_root.exists())

    def test_post_write_failure_rolls_back_exact_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fixture = build_fixture(home)
            backup_root = home / ".local" / "state" / "pixel-mesh" / "discord-migration"
            live_before = (fixture["restricted_state"] / "openclaw.json").read_bytes()
            mesh_before = (fixture["mesh_state"] / "openclaw.json").read_bytes()
            agents_before = (fixture["workspace"] / "AGENTS.md").read_bytes()
            real_run = mig.subprocess.run

            def failing_restart(argv, **kwargs):
                if argv and argv[0] == "systemctl":
                    raise RuntimeError("restart failed")
                return real_run(argv, **kwargs)

            with mock.patch.dict(os.environ, env_for(home, backup_root), clear=False):
                with mock.patch.object(mig.subprocess, "run", side_effect=failing_restart):
                    with self.assertRaises(RuntimeError):
                        mig.main(["--apply"])
            # Exact backups restored after post-write failure.
            self.assertEqual((fixture["restricted_state"] / "openclaw.json").read_bytes(), live_before)
            self.assertEqual((fixture["mesh_state"] / "openclaw.json").read_bytes(), mesh_before)
            self.assertEqual((fixture["workspace"] / "AGENTS.md").read_bytes(), agents_before)

    def test_rollback_does_not_mask_original_failure_when_restart_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fixture = build_fixture(home)
            backup_root = home / ".local" / "state" / "pixel-mesh" / "discord-migration"
            real_run = mig.subprocess.run

            def fail_restart(argv, **kwargs):
                if argv and argv[0] == "systemctl":
                    raise RuntimeError("restart unavailable")
                return real_run(argv, **kwargs)
            with mock.patch.dict(os.environ, env_for(home, backup_root), clear=False):
                with mock.patch.object(mig.subprocess, "run", side_effect=fail_restart):
                    with self.assertRaises(RuntimeError) as ctx:
                        mig.main(["--apply"])
            # Original failure is preserved and both failures reported.
            message = str(ctx.exception)
            self.assertIn("original error", message)
            self.assertIn("rollback error", message)
            self.assertIn("restart unavailable", message)


class RestoreTests(unittest.TestCase):
    def _make_backup(self, home: Path, backup_root: Path) -> Path:
        self.assertEqual(capture_run(home, backup_root, "--apply")[0], 0)
        return next(backup_root.iterdir())

    def test_explicit_restore_validates_and_restores_all_three_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fixture = build_fixture(home)
            backup_root = home / ".local" / "state" / "pixel-mesh" / "discord-migration"
            backup_dir = self._make_backup(home, backup_root)
            # Corrupt live state, then restore.
            (fixture["restricted_state"] / "openclaw.json").write_text('{"channels":{}}', encoding="utf-8")
            (fixture["mesh_state"] / "openclaw.json").write_text('{"channels":{}}', encoding="utf-8")
            (fixture["workspace"] / "AGENTS.md").write_text("GONE\n", encoding="utf-8")
            code, out, run = capture_run(home, backup_root, "--restore", str(backup_dir))
            self.assertEqual(code, 0)
            live = json.loads((fixture["restricted_state"] / "openclaw.json").read_text())
            self.assertTrue(live["channels"]["discord"]["enabled"])
            mesh = json.loads((fixture["mesh_state"] / "openclaw.json").read_text())
            self.assertNotIn("discord", mesh.get("channels", {}))
            self.assertEqual((fixture["workspace"] / "AGENTS.md").read_text(), "# Original instructions\n\nKeep me.\n")
            # Mesh service restarted; restricted-gateway caveat reported.
            self.assertTrue(any(a[0] and a[0][0] and a[0][0][0] == "systemctl" and "restart" in a[0][0] for a in run.call_args_list))
            payload = json.loads(out)
            self.assertTrue(payload["restored"])
            self.assertIn("restrictedSystemGatewayRestart", payload)
            self.assertIn("separately authorized", payload["restrictedSystemGatewayRestart"])
            # No config contents or secret leaked into the report.
            self.assertNotIn(FAKE_TOKEN, out)

    def test_restore_rejects_backup_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            build_fixture(home)
            backup_root = home / ".local" / "state" / "pixel-mesh" / "discord-migration"
            outside = Path(tmp) / "elsewhere"
            outside.mkdir()
            with mock.patch.dict(os.environ, env_for(home, backup_root), clear=False):
                with self.assertRaises(SystemExit):
                    mig.main(["--restore", str(outside)])

    def test_restore_rejects_invalid_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            build_fixture(home)
            backup_root = home / ".local" / "state" / "pixel-mesh" / "discord-migration"
            bad = backup_root / "badbackup"
            bad.mkdir(parents=True, exist_ok=True)
            (bad / "manifest.json").write_text(json.dumps({"schemaVersion": 999}), encoding="utf-8")
            (bad / "restricted-openclaw.json").write_text("{}", encoding="utf-8")
            (bad / "mesh-openclaw.json").write_text("{}", encoding="utf-8")
            (bad / "AGENTS.md").write_text("x", encoding="utf-8")
            with mock.patch.dict(os.environ, env_for(home, backup_root), clear=False):
                with self.assertRaises(SystemExit):
                    mig.main(["--restore", str(bad)])

    def test_restore_validates_backup_configs_before_live_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fixture = build_fixture(home)
            backup_root = home / ".local" / "state" / "pixel-mesh" / "discord-migration"
            backup_dir = self._make_backup(home, backup_root)
            live_before = (fixture["restricted_state"] / "openclaw.json").read_bytes()
            with mock.patch.dict(os.environ, env_for(home, backup_root), clear=False):
                with mock.patch.object(mig.subprocess, "run",
                                       side_effect=subprocess.CalledProcessError(1, ["openclaw"])):
                    with self.assertRaises(subprocess.CalledProcessError):
                        mig.main(["--restore", str(backup_dir)])
            self.assertEqual((fixture["restricted_state"] / "openclaw.json").read_bytes(),
                             live_before)


class NoHardcodedDataTests(unittest.TestCase):
    def test_no_hardcoded_personal_or_credential_data(self):
        src = Path(__file__).resolve().parents[1] / "deploy/mesh/migrate_discord.py"
        text = src.read_text(encoding="utf-8")
        self.assertNotIn("Michael", text)
        self.assertNotIn("mfa.", text)
        self.assertNotIn("18400", text)
        self.assertNotIn("client_secret", text)
        self.assertNotIn("OPENCLAW_GATEWAY_TOKEN", text)
        self.assertNotIn("Discord bot token", text)


if __name__ == "__main__":
    unittest.main()
