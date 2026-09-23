"""Unit tests for the deploy/mesh Pixel mesh profile installer (mesh-0.1.0).

These tests never use real SSH, systemd, Discord, credentials, live profiles,
or live runtime state. The installer is exercised in a temporary home with a
fake OpenClaw binary that only validates and reports; mutating steps that would
touch real systemd are patched out.
"""

import importlib.util
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "mesh_install", Path(__file__).resolve().parents[1] / "deploy/mesh/install.py"
)
install = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(install)


class InstallerContractTests(unittest.TestCase):
    def test_node_allowlist_is_fixed(self):
        self.assertEqual(install.NODES, {"tower1": 18789, "tower2": 18790, "tower3": 18789})

    def test_dry_run_is_safe_and_never_mutates_home(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as tools:
            openclaw_bin = Path(tools) / "openclaw"
            openclaw_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            openclaw_bin.chmod(0o755)
            env = {**os.environ, install.HOME_ENV: home,
                   install.OPENCLAW_BIN_ENV: str(openclaw_bin)}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(install.subprocess, "run") as run:
                    run.return_value = mock.Mock(returncode=0)
                    code = install.main(["--node", "Tower2", "--dry-run"])
            self.assertEqual(code, 0)
            self.assertEqual(sorted(os.listdir(home)), [])

    def test_dry_run_reports_expected_plan(self):
        import io
        import contextlib
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as tools:
            openclaw_bin = Path(tools) / "openclaw"
            openclaw_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            openclaw_bin.chmod(0o755)
            env = {**os.environ, install.HOME_ENV: home,
                   install.OPENCLAW_BIN_ENV: str(openclaw_bin)}
            with mock.patch.dict(os.environ, env, clear=False):
                buffer = io.StringIO()
                with mock.patch.object(install.subprocess, "run") as run:
                    run.return_value = mock.Mock(returncode=0)
                    with contextlib.redirect_stdout(buffer):
                        code = install.main(["--node", "Tower1", "--dry-run"])
                payload = json.loads(buffer.getvalue().strip())
            self.assertEqual(code, 0)
            self.assertEqual(payload["node"], "Tower1")
            self.assertEqual(payload["port"], 18789)
            self.assertEqual(payload["service"], "pixel-mesh-gateway.service")
            self.assertEqual(payload["preflight"], "passed")
            self.assertTrue(str(home) in payload["state"])

    def test_fails_closed_when_openclaw_missing_before_mutation(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, install.HOME_ENV: home,
                   install.OPENCLAW_BIN_ENV: str(Path(home) / "no-such-openclaw")}
            with mock.patch.dict(os.environ, env, clear=False):
                with self.assertRaises(SystemExit):
                    install.main(["--node", "Tower3"])
            # No state directory may have been created.
            self.assertFalse((Path(home) / ".openclaw-mesh").exists())

    def test_fails_closed_when_openclaw_is_not_executable(self):
        with tempfile.TemporaryDirectory() as home:
            openclaw_bin = Path(home) / "openclaw"
            openclaw_bin.write_text("not executable\n", encoding="utf-8")
            env = {**os.environ, install.HOME_ENV: home,
                   install.OPENCLAW_BIN_ENV: str(openclaw_bin)}
            with mock.patch.dict(os.environ, env, clear=False):
                with self.assertRaises(SystemExit):
                    install.main(["--node", "Tower1"])
            self.assertFalse((Path(home) / ".openclaw-mesh").exists())

    def test_fails_closed_for_a_crlf_mesh_peer_before_mutation(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as source:
            source_path = Path(source)
            openclaw_bin = source_path / "openclaw"
            openclaw_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            openclaw_bin.chmod(0o755)
            installer = source_path / "install.py"
            installer.write_text("placeholder\n", encoding="utf-8")
            (source_path / "pixel_mesh_peer.py").write_bytes(
                b"#!/usr/bin/env python3\r\nprint('broken executable')\r\n"
            )
            env = {**os.environ, install.HOME_ENV: home,
                   install.OPENCLAW_BIN_ENV: str(openclaw_bin)}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(install, "__file__", str(installer)):
                    with self.assertRaisesRegex(SystemExit, "LF-terminated Python shebang"):
                        install.main(["--node", "Tower2"])
            self.assertEqual(sorted(os.listdir(home)), [])

    def test_temp_home_install_writes_release_state_and_never_uses_shell(self):
        with tempfile.TemporaryDirectory() as home:
            openclaw_bin = Path(home) / "openclaw"
            openclaw_bin.write_text("#!/bin/sh\necho validated\n", encoding="utf-8")
            openclaw_bin.chmod(0o755)
            env = {**os.environ, install.HOME_ENV: home,
                   install.OPENCLAW_BIN_ENV: str(openclaw_bin)}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(install.subprocess, "run") as run:
                    run.return_value = mock.Mock(returncode=0)
                    code = install.main(["--node", "Tower2"])
            self.assertEqual(code, 0)
            # All subprocess calls use argv lists, never shell=True.
            for call in run.call_args_list:
                self.assertNotIn("shell", call.kwargs)
            state = Path(home) / ".openclaw-mesh"
            self.assertTrue((state / "openclaw.json").exists())
            self.assertTrue((state / "exec-approvals.json").exists())
            self.assertTrue((state / ".env").exists())
            self.assertTrue((state / "workspace-pixel" / "AGENTS.md").exists())
            env_path = Path(home) / ".config" / "pixel-mesh" / "gateway.env"
            self.assertTrue(env_path.exists())
            self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)
            releases = Path(home) / ".local" / "share" / "pixel-mesh" / "releases"
            release = next(releases.iterdir())
            self.assertTrue(release.name.startswith(f"{install.VERSION}-"))
            self.assertTrue((release / "pixel-mesh-peer").exists())
            self.assertTrue((release / "exec-shell" / "bash").exists())
            self.assertEqual((release / "exec-shell" / "bash").stat().st_mode & 0o777, 0o700)
            self.assertTrue((Path(home) / ".local" / "share" / "pixel-mesh" / "current").is_symlink())
            # The unit is written inside the isolated temp home, never the live systemd tree.
            unit = Path(home) / ".config" / "systemd" / "user" / "pixel-mesh-gateway.service"
            self.assertTrue(unit.exists())
            self.assertIn(
                f"Environment=SHELL={Path(home) / '.local/share/pixel-mesh/current/exec-shell/bash'}",
                unit.read_text(encoding="utf-8"),
            )

    def test_fails_closed_for_invalid_exec_shell_before_mutation(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as source:
            source_path = Path(source)
            openclaw_bin = source_path / "openclaw"
            openclaw_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            openclaw_bin.chmod(0o755)
            installer = source_path / "install.py"
            installer.write_text("placeholder\n", encoding="utf-8")
            (source_path / "pixel_mesh_peer.py").write_bytes(
                b"#!/usr/bin/env python3\nprint('valid executable')\n"
            )
            (source_path / install.EXEC_SHELL_SOURCE_NAME).write_bytes(
                b"#!/bin/sh\r\nexit 0\r\n"
            )
            env = {**os.environ, install.HOME_ENV: home,
                   install.OPENCLAW_BIN_ENV: str(openclaw_bin)}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(install, "__file__", str(installer)):
                    with self.assertRaisesRegex(SystemExit, "bounded LF-terminated"):
                        install.main(["--node", "Tower2"])
            self.assertEqual(sorted(os.listdir(home)), [])

    def test_content_addressed_release_mismatch_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home) / "pixel-mesh"
            peer_bytes = b"#!/usr/bin/env python3\nprint('peer')\n"
            shell_bytes = b"#!/bin/sh\nexit 0\n"
            target = install.materialize_release(root, peer_bytes, shell_bytes)
            peer = root / target / "pixel-mesh-peer"
            peer.write_bytes(b"tampered\n")
            with self.assertRaisesRegex(SystemExit, "artifact mismatch"):
                install.materialize_release(root, peer_bytes, shell_bytes)
            self.assertEqual(peer.read_bytes(), b"tampered\n")

            peer.write_bytes(peer_bytes)
            peer.chmod(0o600)
            with self.assertRaisesRegex(SystemExit, "mode 0700"):
                install.materialize_release(root, peer_bytes, shell_bytes)

    @unittest.skipUnless(os.name == "posix", "symlink release validation is POSIX-only")
    def test_content_addressed_release_directory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as outside:
            root = Path(home) / "pixel-mesh"
            peer_bytes = b"#!/usr/bin/env python3\nprint('peer')\n"
            shell_bytes = b"#!/bin/sh\nexit 0\n"
            release_id = install.release_id_for(peer_bytes, shell_bytes)
            releases = root / "releases"
            releases.mkdir(parents=True)
            (releases / release_id).symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(SystemExit, "owner-private directory"):
                install.materialize_release(root, peer_bytes, shell_bytes)

    @unittest.skipUnless(os.name == "posix" and Path("/bin/bash").exists(),
                         "exec shell contract is Linux-only")
    def test_exec_shell_preserves_openclaw_shapes_and_enforces_pipefail(self):
        shell = Path(__file__).resolve().parents[1] / "deploy/mesh" / install.EXEC_SHELL_SOURCE_NAME

        reported = subprocess.run(
            ["/bin/sh", str(shell), "--noprofile", "--norc", "-c",
             "/bin/bash -c 'exit 7' | tail -n 1; printf 'status=%s\\n' \"$?\""],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(reported.stdout, "status=7\n")

        failed = subprocess.run(
            ["/bin/sh", str(shell), "--noprofile", "--norc", "-c",
             "/bin/bash -c 'exit 7' | tail -n 1"],
            capture_output=True, text=True,
        )
        self.assertEqual(failed.returncode, 7)

        succeeded = subprocess.run(
            ["/bin/sh", str(shell), "--noprofile", "--norc", "-c", "printf x | wc -c"],
            capture_output=True, text=True,
        )
        self.assertEqual(succeeded.returncode, 0)
        self.assertEqual(succeeded.stdout.strip(), "1")

        sigpipe = subprocess.run(
            ["/bin/sh", str(shell), "--noprofile", "--norc", "-c",
             "yes x | head -n 1 >/dev/null"],
            capture_output=True, text=True,
        )
        self.assertEqual(sigpipe.returncode, 141)

        explicit_sh = subprocess.run(
            ["/bin/sh", str(shell), "--noprofile", "--norc", "-c",
             "/bin/sh -c 'false | true'"],
            capture_output=True, text=True,
        )
        self.assertEqual(explicit_sh.returncode, 0)

    @unittest.skipUnless(os.name == "posix" and Path("/bin/bash").exists(),
                         "exec shell contract is Linux-only")
    def test_exec_shell_preserves_snapshot_startup_and_rejects_other_argv(self):
        shell = Path(__file__).resolve().parents[1] / "deploy/mesh" / install.EXEC_SHELL_SOURCE_NAME
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            (home_path / ".bashrc").write_text(
                "alias pixel_snapshot_alias='printf alias-ok'\n"
                "pixel_snapshot_function() { printf function-ok; }\n",
                encoding="utf-8",
            )
            snapshot = subprocess.run(
                ["/bin/sh", str(shell), "-i", "-c",
                 "alias pixel_snapshot_alias; pixel_snapshot_function"],
                env={**os.environ, "HOME": home}, capture_output=True, text=True,
            )
            self.assertEqual(snapshot.returncode, 0)
            self.assertIn("pixel_snapshot_alias", snapshot.stdout)
            self.assertIn("function-ok", snapshot.stdout)

            normal = subprocess.run(
                ["/bin/sh", str(shell), "--noprofile", "--norc", "-c",
                 "type pixel_snapshot_function"],
                env={**os.environ, "HOME": home}, capture_output=True, text=True,
            )
            self.assertNotEqual(normal.returncode, 0)

            guard = home_path / "unexpected-argv-ran"
            rejected = subprocess.run(
                ["/bin/sh", str(shell), "-c", f"touch {guard}"],
                capture_output=True, text=True,
            )
            self.assertEqual(rejected.returncode, 64)
            self.assertIn("unsupported invocation", rejected.stderr)
            self.assertFalse(guard.exists())

    def test_config_generated_is_valid_json_and_ports_correct(self):
        with tempfile.TemporaryDirectory() as home:
            openclaw_bin = Path(home) / "openclaw"
            openclaw_bin.write_text("#!/bin/sh\necho validated\n", encoding="utf-8")
            openclaw_bin.chmod(0o755)
            env = {**os.environ, install.HOME_ENV: home,
                   install.OPENCLAW_BIN_ENV: str(openclaw_bin)}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(install.subprocess, "run") as run:
                    run.return_value = mock.Mock(returncode=0)
                    install.main(["--node", "Tower2"])
            config = json.loads((Path(home) / ".openclaw-mesh" / "openclaw.json").read_text(encoding="utf-8"))
            self.assertEqual(config["gateway"]["port"], 18790)
            self.assertEqual(config["gateway"]["bind"], "loopback")
            self.assertEqual(config["gateway"]["auth"]["mode"], "token")
            self.assertEqual(config["agents"]["list"][0]["id"], "pixel")
            model = config["models"]["providers"]["tower"]["models"][0]
            self.assertEqual(model["contextWindow"], install.AGENT_CONTEXT_WINDOW)
            self.assertEqual(model["contextWindow"], 262_144)
            self.assertEqual(model["name"], "Dream Fleet local-first agent")
            defaults = config["agents"]["defaults"]
            self.assertEqual(defaults["bootstrapMaxChars"], 32_000)
            self.assertEqual(defaults["bootstrapTotalMaxChars"], 96_000)
            loop = config["tools"]["loopDetection"]
            self.assertTrue(loop["enabled"])
            self.assertLess(loop["warningThreshold"], loop["criticalThreshold"])
            self.assertLess(loop["criticalThreshold"], loop["globalCircuitBreakerThreshold"])
            self.assertEqual(loop["detectors"], {
                "genericRepeat": True, "knownPollNoProgress": True, "pingPong": True,
            })

    @unittest.skipUnless(os.name == "posix", "owner-private profile mode is Linux-only")
    def test_exact_legacy_model_profile_migrates_without_touching_owner_fields(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            state = root / ".openclaw-mesh"
            state.mkdir()
            openclaw = root / "openclaw"
            openclaw.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            openclaw.chmod(0o755)
            path = state / "openclaw.json"
            profile = {
                "models": {"providers": {"tower": {"models": [{
                    "id": "dream-fleet-agent",
                    "name": install.LEGACY_MODEL_PROFILE_NAME,
                    "contextWindow": install.LEGACY_AGENT_CONTEXT_WINDOW,
                    "maxTokens": 16384,
                }]}}},
                "channels": {"discord": {"token": "preserved-owner-secret"}},
            }
            path.write_text(json.dumps(profile) + "\n", encoding="utf-8")
            path.chmod(0o600)
            with mock.patch.object(install, "validate_config") as validate:
                self.assertTrue(install.migrate_managed_model_profile(path, openclaw, state))
                validate.assert_called_once()
            migrated = json.loads(path.read_text(encoding="utf-8"))
            model = migrated["models"]["providers"]["tower"]["models"][0]
            self.assertEqual(model["name"], install.MODEL_PROFILE_NAME)
            self.assertEqual(model["contextWindow"], install.AGENT_CONTEXT_WINDOW)
            self.assertEqual(migrated["channels"], profile["channels"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with mock.patch.object(install, "validate_config") as validate:
                self.assertFalse(install.migrate_managed_model_profile(path, openclaw, state))
                validate.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "owner-private profile mode is Linux-only")
    def test_custom_model_context_is_not_overwritten_by_managed_migration(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            state = root / ".openclaw-mesh"
            state.mkdir()
            openclaw = root / "openclaw"
            openclaw.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            openclaw.chmod(0o755)
            path = state / "openclaw.json"
            profile = {"models": {"providers": {"tower": {"models": [{
                "id": "dream-fleet-agent", "name": "owner model", "contextWindow": 131072,
            }]}}}}
            path.write_text(json.dumps(profile) + "\n", encoding="utf-8")
            path.chmod(0o600)
            with mock.patch.object(install, "validate_config") as validate:
                self.assertFalse(install.migrate_managed_model_profile(path, openclaw, state))
                validate.assert_not_called()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), profile)

    @unittest.skipUnless(os.name == "posix", "owner-private profile mode is Linux-only")
    def test_partially_legacy_model_profile_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            state = root / ".openclaw-mesh"
            state.mkdir()
            openclaw = root / "openclaw"
            openclaw.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            openclaw.chmod(0o755)
            path = state / "openclaw.json"
            profiles = (
                {"id": "dream-fleet-agent", "name": "owner model",
                 "contextWindow": install.LEGACY_AGENT_CONTEXT_WINDOW},
                {"id": "dream-fleet-agent", "name": install.LEGACY_MODEL_PROFILE_NAME,
                 "contextWindow": 131072},
            )
            for model in profiles:
                profile = {"models": {"providers": {"tower": {"models": [model]}}}}
                path.write_text(json.dumps(profile) + "\n", encoding="utf-8")
                path.chmod(0o600)
                with mock.patch.object(install, "validate_config") as validate:
                    self.assertFalse(install.migrate_managed_model_profile(path, openclaw, state))
                    validate.assert_not_called()
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), profile)

    def test_repeat_install_preserves_profile_workspace_and_token(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            openclaw_bin = home_path / "openclaw"
            openclaw_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            openclaw_bin.chmod(0o755)
            env = {**os.environ, install.HOME_ENV: home,
                   install.OPENCLAW_BIN_ENV: str(openclaw_bin)}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(install.subprocess, "run") as run:
                    run.return_value = mock.Mock(returncode=0)
                    install.main(["--node", "Tower2"])
                    config_path = home_path / ".openclaw-mesh" / "openclaw.json"
                    config = json.loads(config_path.read_text(encoding="utf-8"))
                    config["channels"] = {"discord": {"enabled": True, "token": "preserved-in-test"}}
                    config_path.write_text(json.dumps(config) + "\n", encoding="utf-8")
                    agents_path = home_path / ".openclaw-mesh" / "workspace-pixel" / "AGENTS.md"
                    agents_path.write_text("owner customization\n", encoding="utf-8")
                    token_path = home_path / ".config" / "pixel-mesh" / "gateway.env"
                    original_token = token_path.read_text(encoding="utf-8")
                    install.main(["--node", "Tower2"])
            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["channels"]["discord"]["token"],
                             "preserved-in-test")
            self.assertEqual(agents_path.read_text(encoding="utf-8"), "owner customization\n")
            self.assertEqual(token_path.read_text(encoding="utf-8"), original_token)

    def test_first_hardened_install_retains_a_hardened_legacy_rollback(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            openclaw_bin = home_path / "openclaw"
            openclaw_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            openclaw_bin.chmod(0o755)
            release_root = home_path / ".local" / "share" / "pixel-mesh"
            releases = release_root / "releases"
            legacy_current = releases / "mesh-0.1.0-legacy-current"
            legacy_previous = releases / "mesh-0.1.0-legacy-previous"
            for release, marker in ((legacy_current, "current"), (legacy_previous, "previous")):
                release.mkdir(parents=True)
                peer = release / "pixel-mesh-peer"
                peer.write_text(f"#!/usr/bin/env python3\nprint('{marker}')\n", encoding="utf-8")
                peer.chmod(0o700)
            install.replace_symlink(release_root / "current", Path("releases") / legacy_current.name)
            install.replace_symlink(release_root / "previous", Path("releases") / legacy_previous.name)

            env = {**os.environ, install.HOME_ENV: home,
                   install.OPENCLAW_BIN_ENV: str(openclaw_bin)}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(install.subprocess, "run") as run:
                    run.return_value = mock.Mock(returncode=0)
                    install.main(["--node", "Tower2"])

            current_target = Path(os.readlink(release_root / "current"))
            previous_target = Path(os.readlink(release_root / "previous"))
            self.assertNotEqual(current_target, previous_target)
            self.assertTrue((release_root / current_target / "exec-shell" / "bash").is_file())
            self.assertTrue((release_root / previous_target / "exec-shell" / "bash").is_file())
            self.assertFalse((legacy_current / "exec-shell").exists())
            self.assertFalse((legacy_previous / "exec-shell").exists())

            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(install.subprocess, "run") as run:
                    run.return_value = mock.Mock(returncode=0)
                    install.main(["--rollback"])
            self.assertEqual(Path(os.readlink(release_root / "current")), previous_target)
            self.assertEqual(Path(os.readlink(release_root / "previous")), current_target)

    def test_identical_legacy_targets_do_not_leave_an_unhardened_previous_link(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            openclaw_bin = home_path / "openclaw"
            openclaw_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            openclaw_bin.chmod(0o755)
            release_root = home_path / ".local" / "share" / "pixel-mesh"
            releases = release_root / "releases"
            source_peer = Path(install.__file__).with_name("pixel_mesh_peer.py").read_bytes()
            for name in ("legacy-current", "legacy-previous"):
                release = releases / name
                release.mkdir(parents=True)
                peer = release / "pixel-mesh-peer"
                peer.write_bytes(source_peer)
                peer.chmod(0o700)
            install.replace_symlink(release_root / "current", Path("releases/legacy-current"))
            install.replace_symlink(release_root / "previous", Path("releases/legacy-previous"))
            env = {**os.environ, install.HOME_ENV: home,
                   install.OPENCLAW_BIN_ENV: str(openclaw_bin)}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(install.subprocess, "run") as run:
                    run.return_value = mock.Mock(returncode=0)
                    install.main(["--node", "Tower2"])
            self.assertEqual(os.readlink(release_root / "current"), os.readlink(release_root / "previous"))
            target = Path(os.readlink(release_root / "previous"))
            self.assertTrue((release_root / target / "exec-shell" / "bash").is_file())

    def test_rollback_rejects_mixed_exec_shell_hardening(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            releases = home_path / ".local" / "share" / "pixel-mesh" / "releases"
            current_release = releases / "mesh-0.1.0-current"
            previous_release = releases / "mesh-0.1.0-previous"
            for release in (current_release, previous_release):
                release.mkdir(parents=True)
                peer = release / "pixel-mesh-peer"
                peer.write_text("peer\n", encoding="utf-8")
                peer.chmod(0o700)
            shell = current_release / "exec-shell" / "bash"
            shell.parent.mkdir()
            shell.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            shell.chmod(0o700)
            root = releases.parent
            install.replace_symlink(root / "current", Path("releases") / current_release.name)
            install.replace_symlink(root / "previous", Path("releases") / previous_release.name)
            env = {**os.environ, install.HOME_ENV: home}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(install.subprocess, "run") as run:
                    with self.assertRaisesRegex(SystemExit, "agree on exec shell hardening"):
                        install.main(["--rollback"])
                    run.assert_not_called()

    def test_rollback_swaps_valid_content_addressed_releases_and_restarts(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            releases = home_path / ".local" / "share" / "pixel-mesh" / "releases"
            first = releases / "mesh-0.1.0-first"
            second = releases / "mesh-0.1.0-second"
            for release in (first, second):
                release.mkdir(parents=True)
                (release / "pixel-mesh-peer").write_text("peer\n", encoding="utf-8")
            current = releases.parent / "current"
            previous = releases.parent / "previous"
            install.replace_symlink(current, Path("releases") / second.name)
            install.replace_symlink(previous, Path("releases") / first.name)
            env = {**os.environ, install.HOME_ENV: home}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(install.subprocess, "run") as run:
                    run.return_value = mock.Mock(returncode=0)
                    code = install.main(["--rollback"])
            self.assertEqual(code, 0)
            self.assertEqual(os.readlink(current), f"releases/{first.name}")
            self.assertEqual(os.readlink(previous), f"releases/{second.name}")
            self.assertEqual(run.call_args.args[0],
                             ["systemctl", "--user", "restart", "pixel-mesh-gateway.service"])

    def test_external_model_url_is_rejected(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, install.HOME_ENV: home,
                   install.MODEL_BASE_URL_ENV: "https://models.example.com/v1"}
            with mock.patch.dict(os.environ, env, clear=False):
                with self.assertRaises(SystemExit):
                    install.main(["--node", "Tower2", "--dry-run"])


@unittest.skipUnless(os.name == "posix", "mesh reconciliation transactions are POSIX-only")
class ReconciliationContractTests(unittest.TestCase):
    SOURCE = {
        "path": "/srv/pixel-exact-source",
        "commit": "1" * 40,
        "tree": "2" * 40,
        "version": "4.3.27",
    }
    ACTIVE = {
        "version": "4.3.27",
        "target": "releases/4.3.27",
        "identitySha256": "3" * 64,
        "sourceCommit": "1" * 40,
        "sourceTree": "2" * 40,
    }

    def make_home(self, root: Path):
        release_root = root / ".local" / "share" / "pixel-mesh"
        releases = release_root / "releases"
        releases.mkdir(parents=True)
        (root / ".local").chmod(0o700)
        release_root.chmod(0o700)
        releases.chmod(0o700)
        shell = b"#!/bin/sh\nexec /bin/bash \"$@\"\n"
        current = install.materialize_release(
            release_root, b"#!/usr/bin/env python3\nprint('legacy-current')\n", shell,
        )
        previous = install.materialize_release(
            release_root, b"#!/usr/bin/env python3\nprint('legacy-previous')\n", shell,
        )
        install.replace_symlink(release_root / "current", current)
        install.replace_symlink(release_root / "previous", previous)
        local_bin = root / ".local" / "bin"
        local_bin.mkdir(parents=True, exist_ok=True)
        install.replace_symlink(
            local_bin / "pixel-mesh-peer", release_root / "current" / "pixel-mesh-peer",
        )
        return release_root, current.as_posix(), previous.as_posix()

    def source_tuple(self):
        source_path = Path(install.__file__).resolve().parent
        return (
            dict(self.SOURCE),
            (source_path / "pixel_mesh_peer.py").read_bytes(),
            (source_path / install.EXEC_SHELL_SOURCE_NAME).read_bytes(),
        )

    def make_git_source(self, root: Path):
        source = root / "source"
        mesh = source / "deploy" / "mesh"
        mesh.mkdir(parents=True)
        actual_mesh = Path(install.__file__).resolve().parent
        (source / "VERSION").write_bytes(b"4.3.27\n")
        (mesh / "install.py").write_bytes(Path(install.__file__).read_bytes())
        (mesh / "pixel_mesh_peer.py").write_bytes(
            (actual_mesh / "pixel_mesh_peer.py").read_bytes()
        )
        (mesh / install.EXEC_SHELL_SOURCE_NAME).write_bytes(
            (actual_mesh / install.EXEC_SHELL_SOURCE_NAME).read_bytes()
        )
        subprocess.run(["git", "-C", str(source), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(source), "add", "--all"], check=True)
        subprocess.run([
            "git", "-C", str(source), "-c", "user.name=Pixel Tests",
            "-c", "user.email=pixel-tests@example.invalid", "commit", "-qm", "exact source",
        ], check=True)
        commit = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"], check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD^{tree}"], check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        return source, {
            "version": "4.3.27",
            "target": "releases/4.3.27",
            "identitySha256": "3" * 64,
            "sourceCommit": commit,
            "sourceTree": tree,
        }

    def assert_hidden_source_flag_fails_before_candidate(self, flag: str):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / "home"
            home.mkdir()
            release_root, _current, _previous = self.make_home(home)
            source, active = self.make_git_source(base)
            peer = source / "deploy" / "mesh" / "pixel_mesh_peer.py"
            if flag == "--assume-unchanged":
                peer.write_bytes(peer.read_bytes() + b"# hidden assume-unchanged drift\n")
                subprocess.run(
                    ["git", "-C", str(source), "update-index", flag, "--",
                     "deploy/mesh/pixel_mesh_peer.py"],
                    check=True,
                )
            else:
                subprocess.run(
                    ["git", "-C", str(source), "update-index", flag, "--",
                     "deploy/mesh/pixel_mesh_peer.py"],
                    check=True,
                )
                peer.write_bytes(peer.read_bytes() + b"# hidden skip-worktree drift\n")
            status = subprocess.run(
                ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(status.stdout, "")
            releases_before = sorted(path.name for path in (release_root / "releases").iterdir())
            fake_installer = source / "deploy" / "mesh" / "install.py"
            with mock.patch.object(install, "__file__", str(fake_installer)), \
                    mock.patch.object(install, "active_pixel_identity", return_value=active), \
                    mock.patch.object(install.socket, "gethostname", return_value="Tower2"), \
                    mock.patch.object(install, "candidate_mesh_evidence", wraps=install.candidate_mesh_evidence) as candidate:
                with self.assertRaisesRegex(SystemExit, "hidden or noncanonical Git index flags"):
                    install.reconcile_preview("Tower2", home)
            candidate.assert_not_called()
            self.assertEqual(
                sorted(path.name for path in (release_root / "releases").iterdir()),
                releases_before,
            )
            self.assertFalse(install.reconciliation_state_root(home).exists())

    def test_preview_rejects_assume_unchanged_source_before_candidate_or_receipts(self):
        self.assert_hidden_source_flag_fails_before_candidate("--assume-unchanged")

    def test_preview_rejects_skip_worktree_source_before_candidate_or_receipts(self):
        self.assert_hidden_source_flag_fails_before_candidate("--skip-worktree")

    def patches(self, runtime=None):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(
            install, "collect_source_identity", return_value=self.source_tuple(),
        ))
        stack.enter_context(mock.patch.object(install, "active_pixel_identity", return_value=dict(self.ACTIVE)))
        stack.enter_context(mock.patch.object(install.socket, "gethostname", return_value="Tower2"))
        stack.enter_context(mock.patch.object(install, "mesh_service_state", return_value="active"))
        stack.enter_context(mock.patch.object(
            install, "restart_and_verify_mesh", return_value="4" * 64,
        ) if runtime is None else mock.patch.object(
            install, "restart_and_verify_mesh", side_effect=runtime,
        ))
        return stack

    def test_preview_is_deterministic_and_has_no_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, current, previous = self.make_home(home)
            with self.patches():
                first, first_hash = install.reconcile_preview("Tower2", home)
                second, second_hash = install.reconcile_preview("Tower2", home)
            self.assertEqual(first, second)
            self.assertEqual(first_hash, second_hash)
            self.assertEqual(first_hash, install.intent_hash({
                key: value for key, value in first.items()
                if key not in {"status", "reconcileHash", "confirmationRequired"}
            }))
            self.assertEqual(os.readlink(root / "current"), current)
            self.assertEqual(os.readlink(root / "previous"), previous)
            self.assertFalse(install.reconciliation_state_root(home).exists())

    def test_wrong_confirmation_hash_fails_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, current, previous = self.make_home(home)
            with self.patches():
                with self.assertRaisesRegex(SystemExit, "does not match fresh state"):
                    install.execute_reconcile("Tower2", home, "0" * 64)
            self.assertEqual(os.readlink(root / "current"), current)
            self.assertEqual(os.readlink(root / "previous"), previous)
            self.assertFalse(install.reconciliation_state_root(home).exists())

    def test_confirmed_reconcile_hardens_legacy_owned_release_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, _old_current, _old_previous = self.make_home(home)
            root.chmod(0o775)
            (root / "releases").chmod(0o775)
            with self.patches():
                preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                self.assertTrue(preview["effects"]["releaseDirectoryModesWillBeHardened"])
                self.assertEqual(preview["mesh"]["layout"]["root"]["mode"], "0775")
                result = install.execute_reconcile("Tower2", home, reconcile_hash)
            self.assertEqual(result["releaseDirectoryModeAfter"], "0700")
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / "releases").stat().st_mode & 0o777, 0o700)

    def test_world_writable_release_root_is_never_accepted_for_hardening(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, _old_current, _old_previous = self.make_home(home)
            root.chmod(0o777)
            with self.patches():
                with self.assertRaisesRegex(SystemExit, "must not be world-writable"):
                    install.reconcile_preview("Tower2", home)

    def test_exact_reconcile_and_bound_rollback_write_immutable_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, old_current, old_previous = self.make_home(home)
            with self.patches():
                preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                result = install.execute_reconcile("Tower2", home, reconcile_hash)
                candidate = preview["mesh"]["candidate"]["target"]
                self.assertEqual(result["status"], "reconciled")
                self.assertEqual(os.readlink(root / "current"), candidate)
                self.assertEqual(os.readlink(root / "previous"), old_current)
                result_path = Path(result["receiptPath"])
                claim_path = install.reconciliation_state_root(home) / "claims" / f"{reconcile_hash}.json"
                self.assertEqual(result_path.stat().st_mode & 0o777, 0o400)
                self.assertEqual(claim_path.stat().st_mode & 0o777, 0o400)

                rollback_preview, rollback_hash = install.reconcile_rollback_preview(
                    "Tower2", home, reconcile_hash,
                )
                self.assertEqual(rollback_preview["forward"]["resultSha256"], result["receiptSha256"])
                rollback = install.execute_reconcile_rollback(
                    "Tower2", home, reconcile_hash, rollback_hash,
                )
                self.assertEqual(rollback["status"], "rolled-back")
                self.assertEqual(os.readlink(root / "current"), old_current)
                self.assertEqual(os.readlink(root / "previous"), candidate)
                self.assertNotEqual(os.readlink(root / "previous"), old_previous)
                self.assertEqual(Path(rollback["receiptPath"]).stat().st_mode & 0o777, 0o400)
                rollback_claim = (
                    install.reconciliation_state_root(home) / "rollback-claims" / f"{rollback_hash}.json"
                )
                self.assertEqual(rollback_claim.stat().st_mode & 0o777, 0o400)

    def test_reconcile_failure_restores_exact_links_and_records_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, old_current, old_previous = self.make_home(home)
            with self.patches(runtime=[SystemExit("runtime failed"), "5" * 64]):
                _preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                with self.assertRaisesRegex(SystemExit, "runtime failed"):
                    install.execute_reconcile("Tower2", home, reconcile_hash)
            self.assertEqual(os.readlink(root / "current"), old_current)
            self.assertEqual(os.readlink(root / "previous"), old_previous)
            result_path = (
                install.reconciliation_state_root(home) / "reconciliations" / f"{reconcile_hash}.json"
            )
            failure = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(failure["status"], "failed-restored")
            self.assertTrue(failure["activeDeploymentRestored"])
            self.assertEqual(result_path.stat().st_mode & 0o777, 0o400)

    def test_restored_failure_binds_a_new_retry_hash_without_overwriting_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, old_current, old_previous = self.make_home(home)
            with self.patches(runtime=[SystemExit("runtime failed"), "5" * 64, "6" * 64]):
                first_preview, first_hash = install.reconcile_preview("Tower2", home)
                with self.assertRaisesRegex(SystemExit, "runtime failed"):
                    install.execute_reconcile("Tower2", home, first_hash)
                second_preview, second_hash = install.reconcile_preview("Tower2", home)
                self.assertNotEqual(second_hash, first_hash)
                self.assertEqual(second_preview["retry"]["sequence"], 1)
                self.assertEqual(second_preview["retry"]["previousReconcileHash"], first_hash)
                result = install.execute_reconcile("Tower2", home, second_hash)
            self.assertEqual(result["status"], "reconciled")
            self.assertEqual(os.readlink(root / "current"), first_preview["mesh"]["candidate"]["target"])
            self.assertEqual(os.readlink(root / "previous"), old_current)
            self.assertNotEqual(os.readlink(root / "previous"), old_previous)
            receipts = install.reconciliation_state_root(home) / "reconciliations"
            self.assertEqual(len(list(receipts.glob("*.json"))), 2)

    def test_sigkill_at_each_forward_mutation_boundary_recovers_with_one_forward_claim(self):
        checkpoints = (
            "release-directories-hardened",
            "candidate-materialized",
            "previous-link-switched",
            "current-link-switched",
            "helper-link-rebound",
            "runtime-verified",
        )
        for checkpoint in checkpoints:
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                root, old_current, _old_previous = self.make_home(home)
                with self.patches():
                    preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                    child = os.fork()
                    if child == 0:
                        def crash(phase):
                            if phase == checkpoint:
                                os._exit(91)
                        with mock.patch.object(install, "reconciliation_mutation_checkpoint", crash):
                            install.execute_reconcile("Tower2", home, reconcile_hash)
                        os._exit(92)
                    _pid, status = os.waitpid(child, 0)
                    self.assertEqual(os.waitstatus_to_exitcode(status), 91)
                    recovery_preview, recovery_hash = install.reconcile_recovery_preview(
                        "Tower2", home, reconcile_hash,
                    )
                    self.assertEqual(recovery_preview["action"], "resume-forward")
                    recovered = install.execute_reconcile_recovery(
                        "Tower2", home, reconcile_hash, recovery_hash,
                    )
                    self.assertEqual(recovered["status"], "reconciled")
                    with self.assertRaisesRegex(SystemExit, "terminal result"):
                        install.execute_reconcile_recovery(
                            "Tower2", home, reconcile_hash, recovery_hash,
                        )
                    rollback_preview, _rollback_hash = install.reconcile_rollback_preview(
                        "Tower2", home, reconcile_hash,
                    )
                self.assertEqual(rollback_preview["forward"]["resultSha256"], recovered["receiptSha256"])
                self.assertEqual(os.readlink(root / "current"), preview["mesh"]["candidate"]["target"])
                self.assertEqual(os.readlink(root / "previous"), old_current)
                claims = install.reconciliation_state_root(home) / "claims"
                self.assertEqual([path.name for path in claims.glob("*.json")], [f"{reconcile_hash}.json"])

    def test_successful_forward_confirmation_cannot_be_replayed(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, _old_current, _old_previous = self.make_home(home)
            with self.patches():
                preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                install.execute_reconcile("Tower2", home, reconcile_hash)
                before = (os.readlink(root / "current"), os.readlink(root / "previous"))
                with self.assertRaisesRegex(SystemExit, "already reconciled"):
                    install.execute_reconcile("Tower2", home, reconcile_hash)
            self.assertEqual((os.readlink(root / "current"), os.readlink(root / "previous")), before)
            self.assertEqual(before[0], preview["mesh"]["candidate"]["target"])

    def test_recovery_rejects_post_crash_artifact_hardlink_and_mode_drift(self):
        for drift in ("hardlink", "mode"):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                root, _old_current, _old_previous = self.make_home(home)
                with self.patches():
                    preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                    child = os.fork()
                    if child == 0:
                        def crash(phase):
                            if phase == "candidate-materialized":
                                os._exit(91)
                        with mock.patch.object(install, "reconciliation_mutation_checkpoint", crash):
                            install.execute_reconcile("Tower2", home, reconcile_hash)
                        os._exit(92)
                    os.waitpid(child, 0)
                    peer = root / preview["mesh"]["candidate"]["target"] / "pixel-mesh-peer"
                    if drift == "hardlink":
                        os.link(peer, home / "linked-peer")
                        expected = "single-link regular file"
                    else:
                        peer.chmod(0o755)
                        expected = "mode 0700"
                    before = (os.readlink(root / "current"), os.readlink(root / "previous"))
                    with self.assertRaisesRegex(SystemExit, expected):
                        install.reconcile_recovery_preview("Tower2", home, reconcile_hash)
                self.assertEqual(
                    (os.readlink(root / "current"), os.readlink(root / "previous")), before,
                )

    def test_claim_publication_sigkill_never_leaves_a_partial_final_record(self):
        phases = (
            "anonymous-inode-opened",
            "content-written",
            "mode-fixed",
            "content-synced",
            "record-published",
            "directory-synced",
        )
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                root, old_current, old_previous = self.make_home(home)
                with self.patches():
                    _preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                    child = os.fork()
                    if child == 0:
                        def crash(observed):
                            if observed == f"claims:{phase}":
                                os._exit(91)
                        with mock.patch.object(install, "immutable_json_checkpoint", crash):
                            install.execute_reconcile("Tower2", home, reconcile_hash)
                        os._exit(92)
                    _pid, status = os.waitpid(child, 0)
                    self.assertEqual(os.waitstatus_to_exitcode(status), 91)
                    claim_path = (
                        install.reconciliation_state_root(home) / "claims" / f"{reconcile_hash}.json"
                    )
                    if phase in {"record-published", "directory-synced"}:
                        claim, _sha = install.read_immutable_json(
                            claim_path, "hard-crash forward claim",
                        )
                        self.assertEqual(claim["reconcileHash"], reconcile_hash)
                        _recovery, recovery_hash = install.reconcile_recovery_preview(
                            "Tower2", home, reconcile_hash,
                        )
                        result = install.execute_reconcile_recovery(
                            "Tower2", home, reconcile_hash, recovery_hash,
                        )
                    else:
                        self.assertFalse(claim_path.exists() or claim_path.is_symlink())
                        retry, retry_hash = install.reconcile_preview("Tower2", home)
                        self.assertEqual(retry_hash, reconcile_hash)
                        result = install.execute_reconcile("Tower2", home, retry_hash)
                self.assertEqual(result["status"], "reconciled")
                self.assertNotEqual(os.readlink(root / "current"), old_current)
                self.assertEqual(os.readlink(root / "previous"), old_current)
                self.assertNotEqual(os.readlink(root / "previous"), old_previous)

    def test_terminal_result_publication_sigkill_is_absent_or_fully_valid(self):
        for phase in ("content-written", "mode-fixed", "record-published", "directory-synced"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                root, old_current, _old_previous = self.make_home(home)
                with self.patches():
                    _preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                    child = os.fork()
                    if child == 0:
                        def crash(observed):
                            if observed == f"reconciliations:{phase}":
                                os._exit(91)
                        with mock.patch.object(install, "immutable_json_checkpoint", crash):
                            install.execute_reconcile("Tower2", home, reconcile_hash)
                        os._exit(92)
                    _pid, status = os.waitpid(child, 0)
                    self.assertEqual(os.waitstatus_to_exitcode(status), 91)
                    result_path = (
                        install.reconciliation_state_root(home)
                        / "reconciliations" / f"{reconcile_hash}.json"
                    )
                    if phase in {"record-published", "directory-synced"}:
                        result, _sha = install.read_immutable_json(
                            result_path, "hard-crash terminal forward result",
                        )
                        self.assertEqual(result["status"], "reconciled")
                        install.successful_reconcile_evidence("Tower2", home, reconcile_hash)
                    else:
                        self.assertFalse(result_path.exists() or result_path.is_symlink())
                        _recovery, recovery_hash = install.reconcile_recovery_preview(
                            "Tower2", home, reconcile_hash,
                        )
                        result = install.execute_reconcile_recovery(
                            "Tower2", home, reconcile_hash, recovery_hash,
                        )
                self.assertEqual(result["status"], "reconciled")
                self.assertEqual(os.readlink(root / "previous"), old_current)

    def test_recovery_result_durable_before_primary_result_is_reconciled_without_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, old_current, _old_previous = self.make_home(home)
            with self.patches():
                _preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                first_child = os.fork()
                if first_child == 0:
                    def forward_crash(phase):
                        if phase == "previous-link-switched":
                            os._exit(91)
                    with mock.patch.object(install, "reconciliation_mutation_checkpoint", forward_crash):
                        install.execute_reconcile("Tower2", home, reconcile_hash)
                    os._exit(92)
                os.waitpid(first_child, 0)
                _first_recovery, first_recovery_hash = install.reconcile_recovery_preview(
                    "Tower2", home, reconcile_hash,
                )
                second_child = os.fork()
                if second_child == 0:
                    def receipt_crash(phase):
                        if phase == "recoveries:directory-synced":
                            os._exit(93)
                    with mock.patch.object(install, "immutable_json_checkpoint", receipt_crash):
                        install.execute_reconcile_recovery(
                            "Tower2", home, reconcile_hash, first_recovery_hash,
                        )
                    os._exit(94)
                _pid, status = os.waitpid(second_child, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 93)
                primary = (
                    install.reconciliation_state_root(home)
                    / "reconciliations" / f"{reconcile_hash}.json"
                )
                self.assertFalse(primary.exists() or primary.is_symlink())
                first_result = (
                    install.reconciliation_state_root(home)
                    / "recoveries" / f"{first_recovery_hash}.json"
                )
                recovered_attempt, _sha = install.read_immutable_json(
                    first_result, "durable predecessor recovery result",
                )
                self.assertEqual(recovered_attempt["status"], "recovered")
                second_preview, second_hash = install.reconcile_recovery_preview(
                    "Tower2", home, reconcile_hash,
                )
                self.assertEqual(second_preview["recoveryAttempt"]["sequence"], 2)
                final = install.execute_reconcile_recovery(
                    "Tower2", home, reconcile_hash, second_hash,
                )
            self.assertEqual(final["status"], "reconciled")
            self.assertEqual(os.readlink(root / "previous"), old_current)

    def test_recovery_result_publication_sigkill_leaves_no_partial_final_record(self):
        for phase in ("content-written", "mode-fixed"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                root, old_current, _old_previous = self.make_home(home)
                with self.patches():
                    _preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                    first_child = os.fork()
                    if first_child == 0:
                        def forward_crash(observed):
                            if observed == "previous-link-switched":
                                os._exit(91)
                        with mock.patch.object(
                                install, "reconciliation_mutation_checkpoint", forward_crash,
                        ):
                            install.execute_reconcile("Tower2", home, reconcile_hash)
                        os._exit(92)
                    os.waitpid(first_child, 0)
                    _first_recovery, first_recovery_hash = install.reconcile_recovery_preview(
                        "Tower2", home, reconcile_hash,
                    )
                    second_child = os.fork()
                    if second_child == 0:
                        def receipt_crash(observed):
                            if observed == f"recoveries:{phase}":
                                os._exit(93)
                        with mock.patch.object(install, "immutable_json_checkpoint", receipt_crash):
                            install.execute_reconcile_recovery(
                                "Tower2", home, reconcile_hash, first_recovery_hash,
                            )
                        os._exit(94)
                    _pid, status = os.waitpid(second_child, 0)
                    self.assertEqual(os.waitstatus_to_exitcode(status), 93)
                    first_result = (
                        install.reconciliation_state_root(home)
                        / "recoveries" / f"{first_recovery_hash}.json"
                    )
                    self.assertFalse(first_result.exists() or first_result.is_symlink())
                    second_preview, second_hash = install.reconcile_recovery_preview(
                        "Tower2", home, reconcile_hash,
                    )
                    self.assertEqual(second_preview["recoveryAttempt"]["sequence"], 2)
                    final = install.execute_reconcile_recovery(
                        "Tower2", home, reconcile_hash, second_hash,
                    )
                self.assertEqual(final["status"], "reconciled")
                self.assertEqual(os.readlink(root / "previous"), old_current)

    def test_candidate_publication_sigkill_never_leaves_a_partial_final_release(self):
        cases = (
            ("release", "staging-directory-created"),
            ("release", "peer-artifact-written"),
            ("artifact", "bash:content-written"),
            ("release", "release-published"),
        )
        for seam, phase in cases:
            with self.subTest(seam=seam, phase=phase), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                root, old_current, _old_previous = self.make_home(home)
                with self.patches():
                    preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                    candidate = root / preview["mesh"]["candidate"]["target"]
                    child = os.fork()
                    if child == 0:
                        def crash(observed):
                            if observed == phase:
                                os._exit(91)
                        target = (
                            "release_materialization_checkpoint"
                            if seam == "release" else "durable_artifact_checkpoint"
                        )
                        with mock.patch.object(install, target, crash):
                            install.execute_reconcile("Tower2", home, reconcile_hash)
                        os._exit(92)
                    _pid, status = os.waitpid(child, 0)
                    self.assertEqual(os.waitstatus_to_exitcode(status), 91)
                    if phase == "release-published":
                        evidence = install.mesh_release_evidence(
                            root, preview["mesh"]["candidate"]["target"], "hard-crash candidate",
                        )
                        self.assertEqual(evidence, preview["mesh"]["candidate"])
                    else:
                        self.assertFalse(candidate.exists() or candidate.is_symlink())
                    _recovery, recovery_hash = install.reconcile_recovery_preview(
                        "Tower2", home, reconcile_hash,
                    )
                    result = install.execute_reconcile_recovery(
                        "Tower2", home, reconcile_hash, recovery_hash,
                    )
                self.assertEqual(result["status"], "reconciled")
                self.assertEqual(os.readlink(root / "current"), preview["mesh"]["candidate"]["target"])
                self.assertEqual(os.readlink(root / "previous"), old_current)

    def test_lock_symlink_and_hardlink_are_refused_without_touching_target(self):
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                root, old_current, old_previous = self.make_home(home)
                state_root = install.ensure_reconciliation_state_root(home)
                target = home / "unrelated-lock-target"
                target.write_bytes(b"owner data\n")
                target.chmod(0o640)
                lock = state_root / ".lock"
                if kind == "symlink":
                    lock.symlink_to(target)
                else:
                    os.link(target, lock)
                before = (target.read_bytes(), target.stat().st_mode & 0o777, target.stat().st_nlink)
                with self.patches():
                    _preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                    with self.assertRaisesRegex(SystemExit, "lock"):
                        install.execute_reconcile("Tower2", home, reconcile_hash)
                self.assertEqual(
                    (target.read_bytes(), target.stat().st_mode & 0o777, target.stat().st_nlink), before,
                )
                self.assertEqual(os.readlink(root / "current"), old_current)
                self.assertEqual(os.readlink(root / "previous"), old_previous)

    def test_recovery_crash_gets_a_fresh_chained_confirmation_and_no_second_forward_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, old_current, _old_previous = self.make_home(home)
            with self.patches():
                preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                first_child = os.fork()
                if first_child == 0:
                    def first_crash(phase):
                        if phase == "previous-link-switched":
                            os._exit(91)
                    with mock.patch.object(install, "reconciliation_mutation_checkpoint", first_crash):
                        install.execute_reconcile("Tower2", home, reconcile_hash)
                    os._exit(92)
                os.waitpid(first_child, 0)
                first_recovery, first_recovery_hash = install.reconcile_recovery_preview(
                    "Tower2", home, reconcile_hash,
                )
                second_child = os.fork()
                if second_child == 0:
                    def second_crash(phase):
                        if phase == "recovery-current-link-switched":
                            os._exit(93)
                    with mock.patch.object(install, "reconciliation_mutation_checkpoint", second_crash):
                        install.execute_reconcile_recovery(
                            "Tower2", home, reconcile_hash, first_recovery_hash,
                        )
                    os._exit(94)
                _pid, status = os.waitpid(second_child, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 93)
                second_recovery, second_recovery_hash = install.reconcile_recovery_preview(
                    "Tower2", home, reconcile_hash,
                )
                self.assertNotEqual(second_recovery_hash, first_recovery_hash)
                self.assertEqual(second_recovery["recoveryAttempt"]["sequence"], 2)
                self.assertEqual(
                    second_recovery["recoveryAttempt"]["previousRecoveryHash"],
                    first_recovery_hash,
                )
                recovered = install.execute_reconcile_recovery(
                    "Tower2", home, reconcile_hash, second_recovery_hash,
                )
            self.assertEqual(recovered["status"], "reconciled")
            self.assertEqual(os.readlink(root / "current"), preview["mesh"]["candidate"]["target"])
            self.assertEqual(os.readlink(root / "previous"), old_current)
            forward_claims = install.reconciliation_state_root(home) / "claims"
            self.assertEqual(len(list(forward_claims.glob("*.json"))), 1)
            recovery_claims = install.reconciliation_state_root(home) / "recovery-claims"
            self.assertEqual(len(list(recovery_claims.glob("*.json"))), 2)

    def test_recovery_rejects_ambiguous_mixed_links_without_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, _old_current, old_previous = self.make_home(home)
            with self.patches():
                _preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                child = os.fork()
                if child == 0:
                    def crash(phase):
                        if phase == "previous-link-switched":
                            os._exit(91)
                    with mock.patch.object(install, "reconciliation_mutation_checkpoint", crash):
                        install.execute_reconcile("Tower2", home, reconcile_hash)
                    os._exit(92)
                os.waitpid(child, 0)
                install.replace_symlink(root / "current", Path(old_previous))
                before = (os.readlink(root / "current"), os.readlink(root / "previous"))
                with self.assertRaisesRegex(SystemExit, "ambiguous mixed"):
                    install.reconcile_recovery_preview("Tower2", home, reconcile_hash)
            self.assertEqual(
                (os.readlink(root / "current"), os.readlink(root / "previous")), before,
            )

    def test_sigkill_at_each_rollback_mutation_boundary_has_bound_recovery(self):
        checkpoints = (
            "rollback-current-link-switched",
            "rollback-previous-link-switched",
            "rollback-helper-link-rebound",
            "rollback-runtime-verified",
        )
        for checkpoint in checkpoints:
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                root, old_current, _old_previous = self.make_home(home)
                with self.patches():
                    preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                    forward = install.execute_reconcile("Tower2", home, reconcile_hash)
                    rollback_preview, rollback_hash = install.reconcile_rollback_preview(
                        "Tower2", home, reconcile_hash,
                    )
                    child = os.fork()
                    if child == 0:
                        def crash(phase):
                            if phase == checkpoint:
                                os._exit(91)
                        with mock.patch.object(install, "reconciliation_mutation_checkpoint", crash):
                            install.execute_reconcile_rollback(
                                "Tower2", home, reconcile_hash, rollback_hash,
                            )
                        os._exit(92)
                    _pid, status = os.waitpid(child, 0)
                    self.assertEqual(os.waitstatus_to_exitcode(status), 91)
                    recovery_preview, recovery_hash = install.reconcile_rollback_recovery_preview(
                        "Tower2", home, rollback_hash,
                    )
                    self.assertEqual(recovery_preview["action"], "resume-rollback")
                    recovered = install.execute_reconcile_rollback_recovery(
                        "Tower2", home, rollback_hash, recovery_hash,
                    )
                    with self.assertRaisesRegex(SystemExit, "terminal result"):
                        install.execute_reconcile_rollback_recovery(
                            "Tower2", home, rollback_hash, recovery_hash,
                        )
                self.assertEqual(recovered["status"], "rolled-back")
                self.assertEqual(recovered["reconcileHash"], reconcile_hash)
                self.assertEqual(os.readlink(root / "current"), old_current)
                self.assertEqual(os.readlink(root / "previous"), preview["mesh"]["candidate"]["target"])
                self.assertEqual(rollback_preview["forward"]["resultSha256"], forward["receiptSha256"])
                rollback_claims = install.reconciliation_state_root(home) / "rollback-claims"
                self.assertEqual(
                    [path.name for path in rollback_claims.glob("*.json")],
                    [f"{rollback_hash}.json"],
                )

    def test_rollback_recovery_crash_gets_a_fresh_predecessor_bound_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, old_current, _old_previous = self.make_home(home)
            with self.patches():
                preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                install.execute_reconcile("Tower2", home, reconcile_hash)
                _rollback_preview, rollback_hash = install.reconcile_rollback_preview(
                    "Tower2", home, reconcile_hash,
                )
                first_child = os.fork()
                if first_child == 0:
                    def rollback_crash(phase):
                        if phase == "rollback-current-link-switched":
                            os._exit(91)
                    with mock.patch.object(install, "reconciliation_mutation_checkpoint", rollback_crash):
                        install.execute_reconcile_rollback(
                            "Tower2", home, reconcile_hash, rollback_hash,
                        )
                    os._exit(92)
                os.waitpid(first_child, 0)
                _first_recovery, first_recovery_hash = install.reconcile_rollback_recovery_preview(
                    "Tower2", home, rollback_hash,
                )
                second_child = os.fork()
                if second_child == 0:
                    def recovery_crash(phase):
                        if phase == "rollback-recovery-previous-link-switched":
                            os._exit(93)
                    with mock.patch.object(install, "reconciliation_mutation_checkpoint", recovery_crash):
                        install.execute_reconcile_rollback_recovery(
                            "Tower2", home, rollback_hash, first_recovery_hash,
                        )
                    os._exit(94)
                _pid, status = os.waitpid(second_child, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 93)
                second_recovery, second_recovery_hash = install.reconcile_rollback_recovery_preview(
                    "Tower2", home, rollback_hash,
                )
                self.assertEqual(second_recovery["recoveryAttempt"]["sequence"], 2)
                self.assertEqual(
                    second_recovery["recoveryAttempt"]["previousRecoveryHash"],
                    first_recovery_hash,
                )
                recovered = install.execute_reconcile_rollback_recovery(
                    "Tower2", home, rollback_hash, second_recovery_hash,
                )
            self.assertEqual(recovered["status"], "rolled-back")
            self.assertEqual(os.readlink(root / "current"), old_current)
            self.assertEqual(os.readlink(root / "previous"), preview["mesh"]["candidate"]["target"])
            rollback_claims = install.reconciliation_state_root(home) / "rollback-claims"
            self.assertEqual(len(list(rollback_claims.glob("*.json"))), 1)
            recovery_claims = install.reconciliation_state_root(home) / "rollback-recovery-claims"
            self.assertEqual(len(list(recovery_claims.glob("*.json"))), 2)

    def test_symlinked_state_ancestor_is_rejected_before_mesh_mutation(self):
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            home = Path(temporary)
            root, old_current, old_previous = self.make_home(home)
            state_parent = home / ".local" / "state"
            state_parent.symlink_to(outside, target_is_directory=True)
            with self.patches():
                _preview, reconcile_hash = install.reconcile_preview("Tower2", home)
                with self.assertRaisesRegex(SystemExit, "must not contain symlinks"):
                    install.execute_reconcile("Tower2", home, reconcile_hash)
            self.assertEqual(os.readlink(root / "current"), old_current)
            self.assertEqual(os.readlink(root / "previous"), old_previous)

    def test_active_pixel_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.make_home(home)
            active = {**self.ACTIVE, "sourceCommit": "9" * 40}
            with mock.patch.object(
                    install, "collect_source_identity", return_value=self.source_tuple()), \
                    mock.patch.object(install, "active_pixel_identity", return_value=active), \
                    mock.patch.object(install.socket, "gethostname", return_value="Tower2"):
                with self.assertRaisesRegex(SystemExit, "does not match the active signed Pixel"):
                    install.reconcile_preview("Tower2", home)

    def test_cli_requires_separate_preview_and_full_hash_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.make_home(home)
            with mock.patch.dict(os.environ, {install.HOME_ENV: temporary}), self.patches(), \
                    mock.patch("sys.stdout"):
                self.assertEqual(install.main(["reconcile", "--node", "Tower2", "--preview"]), 0)
                with self.assertRaises(SystemExit):
                    install.main(["reconcile", "--node", "Tower2", "--confirm"])

    def test_signed_pixel_rejects_standalone_update_and_rollback_bypasses(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root, old_current, old_previous = self.make_home(home)
            pixel_current = home / ".local" / "share" / "pixel" / "current"
            install.replace_symlink(pixel_current, Path("releases/4.3.27"))
            with mock.patch.dict(os.environ, {install.HOME_ENV: temporary}), \
                    mock.patch.object(install.subprocess, "run") as run:
                for argv in (
                    ["--node", "Tower2", "--dry-run"],
                    ["--rollback", "--dry-run"],
                    ["--rollback"],
                ):
                    with self.assertRaisesRegex(SystemExit, "version-bound reconcile"):
                        install.main(argv)
                run.assert_not_called()
            self.assertEqual(os.readlink(root / "current"), old_current)
            self.assertEqual(os.readlink(root / "previous"), old_previous)


if __name__ == "__main__":
    unittest.main()
