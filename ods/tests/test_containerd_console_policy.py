#!/usr/bin/env python3
"""Unit tests for ods/scripts/prepare-containerd-console.py."""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_HERE, "..", "scripts", "prepare-containerd-console.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("ods_prepare_containerd_console", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


NVIDIA_ONLY_SHOW = (
    "ActiveState=active\n"
    "DevicePolicy=auto\n"
    "DeviceAllow=/dev/nvidia0 rwm\n"
    "DeviceAllow=/dev/nvidiactl rwm\n"
    "DeviceAllow=char-nvidia-caps rwm\n"
)

NVIDIA_WITH_CONSOLE_SHOW = NVIDIA_ONLY_SHOW + "DeviceAllow=/dev/console m\n"

CUSTOM_SHOW = (
    "ActiveState=active\n"
    "DevicePolicy=auto\n"
    "DeviceAllow=/dev/nvidia0 rwm\n"
    "DeviceAllow=/dev/dri/card0 rwm\n"
)

STRICT_SHOW = (
    "ActiveState=active\n"
    "DevicePolicy=closed\n"
    "DeviceAllow=/dev/nvidia0 rwm\n"
)

EMPTY_SHOW = (
    "ActiveState=active\n"
    "DevicePolicy=auto\n"
    "DeviceAllow=\n"
)

INACTIVE_SHOW = (
    "ActiveState=inactive\n"
    "DevicePolicy=auto\n"
    "DeviceAllow=/dev/nvidia0 rwm\n"
)


class ContainerdConsolePolicyTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dropin_dir = os.path.join(self.tmp.name, "containerd.service.d")
        self.dropin_path = os.path.join(self.dropin_dir, "90-ods-image-unpack.conf")
        self.mod.DROPIN_DIR = self.dropin_dir
        self.mod.DROPIN_PATH = self.dropin_path

    def _patch_show(self, stdout, returncode=0):
        return mock.patch.object(
            self.mod.subprocess, "run",
            return_value=_FakeProc(returncode=returncode, stdout=stdout),
        )

    def _patch_run_sequence(self, *procs):
        return mock.patch.object(
            self.mod.subprocess, "run",
            side_effect=list(procs),
        )

    def test_check_nvidia_only_needs_repair(self):
        with self._patch_show(NVIDIA_ONLY_SHOW):
            rc = self.mod.main(["--check"])
        self.assertEqual(rc, 10)
        self.assertFalse(os.path.exists(self.dropin_path))

    def test_check_already_has_console_m(self):
        with self._patch_show(NVIDIA_WITH_CONSOLE_SHOW):
            rc = self.mod.main(["--check"])
        self.assertEqual(rc, 0)

    def test_existing_broader_console_permission_is_not_rewritten(self):
        with self._patch_show(NVIDIA_ONLY_SHOW + "DeviceAllow=/dev/console rwm\n"):
            self.assertEqual(self.mod.main(["--apply"]), 0)
        self.assertFalse(os.path.exists(self.dropin_path))

    def test_systemctl_failure_is_not_reported_as_success(self):
        with mock.patch.object(self.mod.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "systemctl")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.mod.main(["--check"])

    def test_check_custom_policy_no_change(self):
        with self._patch_show(CUSTOM_SHOW):
            rc = self.mod.main(["--check"])
        self.assertEqual(rc, 0)

    def test_check_strict_policy_no_change(self):
        with self._patch_show(STRICT_SHOW):
            rc = self.mod.main(["--check"])
        self.assertEqual(rc, 0)

    def test_check_empty_allow_no_change(self):
        with self._patch_show(EMPTY_SHOW):
            rc = self.mod.main(["--check"])
        self.assertEqual(rc, 0)

    def test_check_inactive_no_change(self):
        with self._patch_show(INACTIVE_SHOW):
            rc = self.mod.main(["--check"])
        self.assertEqual(rc, 0)

    def test_check_no_writes(self):
        with self._patch_show(NVIDIA_ONLY_SHOW):
            self.mod.main(["--check"])
        self.assertFalse(os.path.exists(self.dropin_dir))

    def test_apply_creates_owned_dropin_with_m_only(self):
        with mock.patch.object(self.mod, "_is_root", return_value=True), \
             self._patch_run_sequence(
                 _FakeProc(stdout=NVIDIA_ONLY_SHOW),
                 _FakeProc(returncode=0),
                 _FakeProc(stdout=NVIDIA_WITH_CONSOLE_SHOW),
             ):
            rc = self.mod.main(["--apply"])
        self.assertEqual(rc, 0)
        with open(self.dropin_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("DeviceAllow=/dev/console m\n", content)
        self.assertNotIn("rwm", content)
        self.assertEqual(content, self.mod.DROPIN_CONTENT)
        self.assertEqual(os.stat(self.dropin_path).st_mode & 0o777, 0o644)

    def test_apply_requires_root(self):
        with mock.patch.object(self.mod, "_is_root", return_value=False), \
             self._patch_show(NVIDIA_ONLY_SHOW):
            rc = self.mod.main(["--apply"])
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(self.dropin_path))

    def test_apply_idempotent_when_already_repaired(self):
        with self._patch_show(NVIDIA_WITH_CONSOLE_SHOW):
            rc = self.mod.main(["--apply"])
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(self.dropin_path))

    def test_apply_refuses_foreign_file(self):
        os.makedirs(self.dropin_dir)
        with open(self.dropin_path, "w", encoding="utf-8") as f:
            f.write("[Service]\nDeviceAllow=/dev/console rwm\n")
        with mock.patch.object(self.mod, "_is_root", return_value=True), \
             self._patch_show(NVIDIA_ONLY_SHOW):
            rc = self.mod.main(["--apply"])
        self.assertEqual(rc, 1)
        with open(self.dropin_path, "r", encoding="utf-8") as f:
            self.assertIn("rwm", f.read())

    def test_apply_preserves_vendor_file(self):
        os.makedirs(self.dropin_dir)
        vendor = os.path.join(self.dropin_dir, "containerd-override.conf")
        original = "[Service]\nDeviceAllow=/dev/nvidia0\n"
        with open(vendor, "w", encoding="utf-8") as stream:
            stream.write(original)
        with mock.patch.object(self.mod, "_is_root", return_value=True), self._patch_run_sequence(
            _FakeProc(stdout=NVIDIA_ONLY_SHOW), _FakeProc(), _FakeProc(stdout=NVIDIA_WITH_CONSOLE_SHOW)
        ):
            self.assertEqual(self.mod.main(["--apply"]), 0)
        with open(vendor, encoding="utf-8") as stream:
            self.assertEqual(stream.read(), original)

    def test_apply_refuses_symlink(self):
        os.makedirs(self.dropin_dir)
        target = os.path.join(self.tmp.name, "target.conf")
        with open(target, "w", encoding="utf-8") as f:
            f.write("x")
        os.symlink(target, self.dropin_path)
        with mock.patch.object(self.mod, "_is_root", return_value=True), \
             self._patch_show(NVIDIA_ONLY_SHOW):
            rc = self.mod.main(["--apply"])
        self.assertEqual(rc, 1)
        self.assertTrue(os.path.islink(self.dropin_path))

    def test_apply_refuses_symlink_dir(self):
        real_dir = os.path.join(self.tmp.name, "real")
        os.makedirs(real_dir)
        os.symlink(real_dir, self.dropin_dir)
        with mock.patch.object(self.mod, "_is_root", return_value=True), \
             self._patch_show(NVIDIA_ONLY_SHOW):
            rc = self.mod.main(["--apply"])
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(os.path.join(real_dir, "90-ods-image-unpack.conf")))

    def test_apply_reload_failure_returns_error_and_retry_succeeds(self):
        with mock.patch.object(self.mod, "_is_root", return_value=True), \
             self._patch_run_sequence(
                 _FakeProc(stdout=NVIDIA_ONLY_SHOW),
                 _FakeProc(returncode=1, stderr="boom"),
             ):
            rc = self.mod.main(["--apply"])
        self.assertEqual(rc, 1)
        self.assertTrue(os.path.exists(self.dropin_path))

        # Retry: owned file exists, daemon-reload succeeds, verify passes.
        with mock.patch.object(self.mod, "_is_root", return_value=True), \
             self._patch_run_sequence(
                 _FakeProc(stdout=NVIDIA_ONLY_SHOW),
                 _FakeProc(returncode=0),
                 _FakeProc(stdout=NVIDIA_WITH_CONSOLE_SHOW),
             ):
            rc = self.mod.main(["--apply"])
        self.assertEqual(rc, 0)

    def test_apply_verify_fails_if_console_missing(self):
        with mock.patch.object(self.mod, "_is_root", return_value=True), \
             self._patch_run_sequence(
                 _FakeProc(stdout=NVIDIA_ONLY_SHOW),
                 _FakeProc(returncode=0),
                 _FakeProc(stdout=NVIDIA_ONLY_SHOW),
             ):
            rc = self.mod.main(["--apply"])
        self.assertEqual(rc, 1)

    def test_apply_verify_fails_if_old_entry_lost(self):
        lost = (
            "ActiveState=active\n"
            "DevicePolicy=auto\n"
            "DeviceAllow=/dev/console m\n"
        )
        with mock.patch.object(self.mod, "_is_root", return_value=True), \
             self._patch_run_sequence(
                 _FakeProc(stdout=NVIDIA_ONLY_SHOW),
                 _FakeProc(returncode=0),
                 _FakeProc(stdout=lost),
             ):
            rc = self.mod.main(["--apply"])
        self.assertEqual(rc, 1)

    def test_apply_never_calls_restart_or_stop(self):
        calls = []
        state = {"shows": 0}

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if cmd[:2] == ["systemctl", "show"]:
                state["shows"] += 1
                return _FakeProc(stdout=NVIDIA_ONLY_SHOW if state["shows"] == 1 else NVIDIA_WITH_CONSOLE_SHOW)
            return _FakeProc(returncode=0)

        with mock.patch.object(self.mod, "_is_root", return_value=True), \
             mock.patch.object(self.mod.subprocess, "run", side_effect=fake_run):
            rc = self.mod.main(["--apply"])
        self.assertEqual(rc, 0)
        for c in calls:
            self.assertNotIn("restart", c)
            self.assertNotIn("stop", c)
            self.assertNotIn("start", c)

    def test_parse_device_allow_repeated_keys(self):
        entries = self.mod._parse_device_allow([
            "/dev/nvidia0 rwm",
            "/dev/nvidiactl rwm",
            "char-nvidia-caps rwm",
        ])
        self.assertEqual(entries, [
            ("/dev/nvidia0", "rwm"),
            ("/dev/nvidiactl", "rwm"),
            ("char-nvidia-caps", "rwm"),
        ])

    def test_nvidia_patterns(self):
        self.assertTrue(self.mod._NVIDIA_DEV_RE.match("/dev/nvidia0"))
        self.assertTrue(self.mod._NVIDIA_DEV_RE.match("/dev/nvidiactl"))
        self.assertTrue(self.mod._NVIDIA_DEV_RE.match("/dev/nvidia-caps/nvidia-cap1"))
        self.assertFalse(self.mod._NVIDIA_DEV_RE.match("/dev/dri/card0"))
        self.assertFalse(self.mod._NVIDIA_DEV_RE.match("/dev/nvidia0/../dri"))


class InstallerRuntimeGateTests(unittest.TestCase):
    def run_gate(self, **overrides):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, GPU_BACKEND="nvidia", GPU_COUNT="2", DRY_RUN="false",
                       PODMAN="1", DESKTOP="1", ENDPOINT="unix:///var/run/docker.sock",
                       SECURITY='["name=apparmor"]', CHECK_RC="10", APPLY_RC="0",
                       CALLS=os.path.join(directory, "calls"), DOCKER_HOST="", DOCKER_CONTEXT="")
            env.update(overrides)
            wrapper = os.path.abspath(os.path.join(_HERE, "..", "installers", "lib", "containerd-console.sh"))
            script = r'''
set -euo pipefail
source "$1"
SCRIPT_DIR=/reviewed-source
uname() { printf 'Linux\n'; }
_runtime_is_podman() { return "$PODMAN"; }
_docker_is_desktop_engine() { return "$DESKTOP"; }
log() { :; }
ai() { :; }
ai_ok() { :; }
docker_run() {
    if [[ "$1" == context ]]; then printf '%s\n' "$ENDPOINT";
    else printf '%s\n' "$SECURITY"; fi
}
python3() { printf 'check\n' >> "$CALLS"; return "$CHECK_RC"; }
ods_sudo() { printf 'apply\n' >> "$CALLS"; return "$APPLY_RC"; }
ods_prepare_containerd_console
'''
            result = subprocess.run(["bash", "-c", script, "_", wrapper], env=env, capture_output=True, text=True)
            calls = []
            if os.path.exists(env["CALLS"]):
                with open(env["CALLS"], encoding="utf-8") as stream:
                    calls = stream.read().splitlines()
            return result.returncode, calls

    def test_local_nvidia_requires_check_before_apply(self):
        self.assertEqual(self.run_gate(), (0, ["check", "apply"]))

    def test_unsupported_runtime_never_applies_host_change(self):
        for values in [dict(GPU_BACKEND="cpu"), dict(DRY_RUN="true"), dict(PODMAN="0"),
                       dict(DESKTOP="0"), dict(ENDPOINT="ssh://remote"),
                       dict(DOCKER_HOST="tcp://remote:2375"),
                       dict(ENDPOINT="unix:///run/user/1000/docker.sock"),
                       dict(SECURITY='["name=rootless"]')]:
            with self.subTest(values=values):
                self.assertEqual(self.run_gate(**values), (0, []))

    def test_failed_check_or_apply_fails_phase(self):
        self.assertEqual(self.run_gate(CHECK_RC="2"), (2, ["check"]))
        self.assertEqual(self.run_gate(APPLY_RC="1"), (1, ["check", "apply"]))

    def test_already_ready_does_not_request_sudo(self):
        self.assertEqual(self.run_gate(CHECK_RC="0"), (0, ["check"]))

    def test_explicit_context_takes_precedence_over_docker_host(self):
        self.assertEqual(self.run_gate(DOCKER_HOST="unix:///var/run/docker.sock",
                                      DOCKER_CONTEXT="remote", ENDPOINT="ssh://remote"), (0, []))
        self.assertEqual(self.run_gate(DOCKER_HOST="tcp://ignored:2375", DOCKER_CONTEXT="default"),
                         (0, ["check", "apply"]))


if __name__ == "__main__":
    unittest.main()
