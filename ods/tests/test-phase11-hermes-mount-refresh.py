#!/usr/bin/env python3
"""Retained running Hermes file binds must follow installed source publication."""
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hermes_mounts", ROOT / "scripts/refresh-hermes-mounts.py")
mounts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mounts)


class DockerFixture:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.identity = "a" * 64
        self.live = b"owner live config: preserve exactly\n"
        self.other_identity = "b" * 64
        self.keep_stale = False
        self.change_live = False
        self.read_error = False
        self.ids = None
        self.project = "ods"
        self.labels = {
            "com.docker.compose.project": self.project,
            "com.docker.compose.service": "hermes",
            "com.docker.compose.project.working_dir": str(root),
            "com.docker.compose.oneoff": "False",
        }
        self.running = True
        self.health = "healthy"
        self.startup_health = []
        self.volumes = []
        for destination, relative in {**mounts.FILES, "/opt/data": "data/hermes"}.items():
            source = root / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            if destination == "/opt/data":
                source.mkdir()
                (source / "config.yaml").write_bytes(self.live)
            else:
                source.write_bytes(("original " + destination).encode())
            self.volumes.append(dict(type="bind", source=str(source), target=destination,
                                     read_only=destination != "/opt/data"))
        self.mounted = {destination: (root / relative).read_bytes() for destination, relative in mounts.FILES.items()}
        self.inspected_mounts = [dict(Type="bind", Source=v["source"], Destination=v["target"], RW=not v["read_only"])
                                 for v in self.volumes]
        self.selected = True

    def replace(self, destination):
        path = self.root / mounts.FILES[destination]
        temporary = path.with_name(path.name + ".new")
        temporary.write_bytes(b"reviewed replacement bytes")
        os.replace(temporary, path)

    def run(self, argv, cwd, timeout=30):
        assert cwd == self.root
        self.calls.append(argv)
        if argv[0] == "compose":
            args = argv[1:]
            if args == ["config", "--format", "json"]:
                return json.dumps(dict(name=self.project, services={"hermes": {"volumes": self.volumes}} if self.selected else {}))
            if args == ["ps", "--all", "--quiet", "hermes"]:
                return self.ids if self.ids is not None else self.identity + "\n"
            if args == ["up", "-d", "--no-deps", "--force-recreate", "--no-build", "--pull", "never", "hermes"]:
                assert timeout == 120
                self.identity = "c" * 64
                if not self.keep_stale:
                    self.mounted = {p: (self.root / relative).read_bytes() for p, relative in mounts.FILES.items()}
                if self.change_live:
                    self.live = b"unexpected upstream config change"
                return ""
        if argv[:2] == ["docker", "inspect"]:
            if self.identity == "c" * 64 and self.startup_health:
                self.health = self.startup_health.pop(0)
            return json.dumps([dict(Id=self.identity, Config={"Labels": self.labels},
                                    State={"Running": self.running, "Health": {"Status": self.health}},
                                    Mounts=self.inspected_mounts)])
        if argv[:2] == ["docker", "exec"]:
            if self.read_error:
                raise mounts.Refused("unreadable container file")
            assert argv[2] == self.identity and argv[3:6] == ["python3", "-c", mounts.CONTAINER_DIGESTS]
            return json.dumps({**{p: hashlib.sha256(raw).hexdigest() for p, raw in self.mounted.items()},
                               mounts.LIVE_CONFIG: hashlib.sha256(self.live).hexdigest()})
        raise AssertionError("Unexpected Docker command: " + repr(argv[:4]))

    def refresh(self):
        return mounts.refresh(self.root, ["docker"], ["compose"], self.run)

    def mutations(self):
        return [call for call in self.calls if call[:2] == ["compose", "up"]]


class HermesMountTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.docker = DockerFixture(self.root)

    def test_each_reviewed_replaced_file_rebinds_once_without_touching_live_config_or_other_services(self):
        for destination in mounts.FILES:
            with self.subTest(destination=destination):
                self.docker.calls.clear()
                self.docker.identity = "a" * 64
                # Change each file again, retaining the old mounted bytes.
                path = self.root / mounts.FILES[destination]
                replacement = path.with_name(path.name + ".new")
                replacement.write_bytes(path.read_bytes() + b" updated")
                os.replace(replacement, path)
                live = (self.root / "data/hermes/config.yaml").read_bytes()
                self.assertEqual(self.docker.refresh(), "refreshed")
                self.assertEqual(len(self.docker.mutations()), 1)
                self.assertEqual(self.docker.mounted[destination], path.read_bytes())
                self.assertEqual((self.root / "data/hermes/config.yaml").read_bytes(), live)
                self.assertEqual(self.docker.live, live)
                self.assertEqual(self.docker.other_identity, "b" * 64)

    def test_current_mounts_and_unselected_hermes_do_not_restart(self):
        self.assertEqual(self.docker.refresh(), "current")
        self.docker.selected = False
        self.assertEqual(self.docker.refresh(), "not-selected")
        self.assertFalse(self.docker.mutations())

    def test_unknown_owner_or_mount_is_not_recreated(self):
        for key in self.docker.labels:
            with self.subTest(label=key):
                original = self.docker.labels[key]
                self.docker.labels[key] = "foreign"
                with self.assertRaises(mounts.Refused):
                    self.docker.refresh()
                self.docker.labels[key] = original
        self.docker.volumes[0]["source"] = "/foreign/config"
        with self.assertRaises(mounts.Refused):
            self.docker.refresh()
        self.assertFalse(self.docker.mutations())

    def test_unreadable_mount_or_ambiguous_container_fails_without_recreation(self):
        self.docker.read_error = True
        with self.assertRaises(mounts.Refused):
            self.docker.refresh()
        self.docker.read_error = False
        for ids in ("", "short-id", "a" * 64 + "\n" + "b" * 64):
            self.docker.ids = ids
            with self.assertRaises(mounts.Refused):
                self.docker.refresh()
        self.assertFalse(self.docker.mutations())

    def test_foreign_live_data_and_unexplained_file_sources_never_recreate(self):
        self.docker.replace(next(iter(mounts.FILES)))
        for mounted in self.docker.inspected_mounts:
            with self.subTest(destination=mounted["Destination"]):
                original = mounted["Source"]
                mounted["Source"] = "/foreign/owner-data"
                with self.assertRaisesRegex(mounts.Refused, "unexplained source"):
                    self.docker.refresh()
                mounted["Source"] = original
        self.assertFalse(self.docker.mutations())

    def test_desktop_file_proxy_is_allowed_only_for_readonly_files(self):
        proxy = "/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/" + "d" * 64
        self.docker.inspected_mounts[0]["Source"] = proxy
        self.docker.replace(next(iter(mounts.FILES)))
        self.assertEqual(self.docker.refresh(), "refreshed")
        self.docker.inspected_mounts[-1]["Source"] = proxy
        with self.assertRaisesRegex(mounts.Refused, "unexplained source"):
            self.docker.refresh()
        self.assertEqual(len(self.docker.mutations()), 1)

    def test_postcheck_waits_for_healthy_startup_without_another_recreation(self):
        self.docker.replace(next(iter(mounts.FILES)))
        self.docker.startup_health = ["starting", "starting", "healthy"]
        with patch.object(mounts.time, "sleep") as sleep:
            self.assertEqual(self.docker.refresh(), "refreshed")
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(len(self.docker.mutations()), 1)

    def test_desktop_directory_proxy_requires_identity_and_preserves_live_config(self):
        proxy_root = self.root / "desktop-proxies"
        visible = proxy_root / "Ubuntu-24.04" / ("e" * 64)
        visible.mkdir(parents=True)
        source = "/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/" + "e" * 64
        self.docker.inspected_mounts[-1]["Source"] = source
        self.docker.replace(next(iter(mounts.FILES)))
        live = self.root / "data/hermes"
        original_config = (live / "config.yaml").read_bytes()
        original_stat = os.stat
        live_stat = original_stat(live)

        # A foreign directory is not accepted even with the expected proxy name.
        with patch.object(mounts, "DESKTOP_VISIBLE_ROOT", proxy_root, create=True):
            with self.assertRaisesRegex(mounts.Refused, "unexplained source"):
                self.docker.refresh()
            self.assertFalse(self.docker.mutations())

            # Unprivileged tests cannot create bind mounts. Model only the
            # kernel's inode alias; path validation and the refresh stay real.
            def bind_stat(path, *args, **kwargs):
                if Path(path) == visible:
                    return live_stat
                return original_stat(path, *args, **kwargs)

            with patch.object(mounts.os, "stat", side_effect=bind_stat):
                self.assertEqual(self.docker.refresh(), "refreshed")
        self.assertEqual(len(self.docker.mutations()), 1)
        self.assertEqual((live / "config.yaml").read_bytes(), original_config)
        self.assertEqual(self.docker.live, original_config)
        self.assertEqual(self.docker.other_identity, "b" * 64)

    def test_desktop_directory_proxy_rejects_missing_and_malformed_paths(self):
        self.docker.replace(next(iter(mounts.FILES)))
        prefix = "/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/"
        for source in [prefix + "Ubuntu-24.04/" + "f" * 64,
                       prefix + "../" + "f" * 64, prefix + "./" + "f" * 64,
                       prefix + "Ubuntu-24.04/not-a-digest", "/foreign/data", None]:
            with self.subTest(source=source):
                self.docker.inspected_mounts[-1]["Source"] = source
                with patch.object(mounts, "DESKTOP_VISIBLE_ROOT", self.root / "missing", create=True):
                    with self.assertRaisesRegex(mounts.Refused, "unexplained source"):
                        self.docker.refresh()
        self.assertFalse(self.docker.mutations())

    @unittest.skipIf(os.name == "nt", "Symlink creation needs Windows privileges")
    def test_desktop_directory_proxy_rejects_symlink_and_symlink_ancestor(self):
        proxy_root = self.root / "desktop-proxies"
        visible = proxy_root / "Ubuntu-24.04" / ("e" * 64)
        visible.parent.mkdir(parents=True)
        live = self.root / "data/hermes"
        visible.symlink_to(live)
        source = "/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/" + "e" * 64
        self.docker.inspected_mounts[-1]["Source"] = source
        self.docker.replace(next(iter(mounts.FILES)))
        with patch.object(mounts, "DESKTOP_VISIBLE_ROOT", proxy_root, create=True):
            with self.assertRaisesRegex(mounts.Refused, "unexplained source"):
                self.docker.refresh()
            visible.unlink()
            visible.mkdir()
            real_parent = proxy_root / "real-distro"
            visible.parent.rename(real_parent)
            visible.parent.symlink_to(real_parent)
            original_stat = os.stat
            live_stat = original_stat(live)

            def bind_stat(path, *args, **kwargs):
                return live_stat if Path(path) == visible else original_stat(path, *args, **kwargs)

            with patch.object(mounts.os, "stat", side_effect=bind_stat):
                with self.assertRaisesRegex(mounts.Refused, "unexplained source"):
                    self.docker.refresh()
        self.assertFalse(self.docker.mutations())

    def test_unhealthy_or_timed_out_startup_fails_without_retry(self):
        self.docker.replace(next(iter(mounts.FILES)))
        self.docker.startup_health = ["unhealthy"]
        with self.assertRaisesRegex(mounts.Refused, "did not become healthy"):
            self.docker.refresh()
        self.assertEqual(len(self.docker.mutations()), 1)
        self.docker.identity = "a" * 64
        self.docker.replace(next(iter(mounts.FILES)))
        self.docker.mounted[next(iter(mounts.FILES))] = b"stale again"
        self.docker.startup_health = ["starting"]
        with patch.object(mounts.time, "monotonic", side_effect=[0, 181]):
            with self.assertRaisesRegex(mounts.Refused, "did not become healthy"):
                self.docker.refresh()
        self.assertEqual(len(self.docker.mutations()), 2)

    def test_source_symlink_and_nonrunning_container_fail_without_recreation(self):
        self.docker.running = False
        with self.assertRaises(mounts.Refused):
            self.docker.refresh()
        self.docker.running = True
        path = self.root / next(iter(mounts.FILES.values()))
        path.unlink()
        path.symlink_to(self.root / "data/hermes/config.yaml")
        with self.assertRaises(mounts.Refused):
            self.docker.refresh()
        self.assertFalse(self.docker.mutations())

    def test_failed_postcheck_never_retries(self):
        self.docker.replace(next(iter(mounts.FILES)))
        self.docker.keep_stale = True
        with self.assertRaises(mounts.Refused):
            self.docker.refresh()
        self.assertEqual(len(self.docker.mutations()), 1)

    def test_live_configuration_change_is_reported(self):
        self.docker.replace(next(iter(mounts.FILES)))
        self.docker.change_live = True
        with self.assertRaisesRegex(mounts.Refused, "live configuration changed"):
            self.docker.refresh()
        self.assertEqual(len(self.docker.mutations()), 1)

    def test_source_change_during_check_prevents_recreation(self):
        self.docker.replace(next(iter(mounts.FILES)))
        original = self.docker.run
        def changing(argv, cwd, timeout=30):
            result = original(argv, cwd, timeout)
            if argv[:2] == ["docker", "exec"]:
                (self.root / next(iter(mounts.FILES.values()))).write_bytes(b"concurrent edit")
            return result
        self.docker.run = changing
        with self.assertRaisesRegex(mounts.Refused, "installation changed"):
            self.docker.refresh()
        self.assertFalse(self.docker.mutations())

    def test_installer_orders_check_after_final_render_before_persona_copy(self):
        phase = (ROOT / "installers/phases/11-services.sh").read_text()
        complete = phase.index("if ! ods_pixel_install_default_agent; then")
        patch = phase.index('_phase11_apply_hermes_template "${_phase11_hermes_template_route[@]}"', complete)
        render = phase.index('"$_python_cmd" "$INSTALL_DIR/scripts/build-installation-context.py"', patch)
        check = phase.index("if ! _phase11_refresh_hermes_mounts; then", render)
        copy_persona = phase.index("$DOCKER_CMD exec ods-hermes cp", render)
        self.assertLess(complete, patch)
        self.assertLess(render, check)
        self.assertLess(check, copy_persona)
        self.assertIn('exit 1', phase[check:copy_persona])


if __name__ == "__main__":
    unittest.main()
