#!/usr/bin/env python3
"""Exercise real Linux CLI failure propagation without invoking service managers."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TARGET = Path(os.environ.get("ODS_CLI_UNDER_TEST", Path(__file__).resolve().parents[1] / "ods-cli"))
PROBE = r'''
set -u
check_install() { :; }
load_env() { :; }
uname() { printf '%s\n' "$TEST_OS"; }
sleep() { :; }
success() { printf 'SUCCESS:%s\n' "$*"; }
warn() { printf 'WARN:%s\n' "$*"; }
log_error() { printf 'ERROR:%s\n' "$*" >&2; }
manager_action() {
    printf '%s\n' "$1" >> "$TEST_TRACE"
    [[ "$1" != "$TEST_FAIL" ]]
}
systemctl() {
    [[ "$1" == cat ]] && return 0
    manager_action "$1"
}
sudo() { "$@"; }

source <(awk '/^cmd_agent\(\) \{/{copy=1} copy{print} copy && /^}$/{exit}' "$1")
if [[ "$TEST_ACTION" == update-guard ]]; then
    cmd_agent restart || warn 'Host agent restart failed (non-fatal)'
    printf 'Update complete\n'
else
    cmd_agent "$TEST_ACTION"
fi
'''


class AgentManagerFailures(unittest.TestCase):
    def probe(self, manager, action, failure):
        with tempfile.TemporaryDirectory(prefix="ods-agent-manager-") as temp:
            fixture = Path(temp)
            trace = fixture / "calls"
            env = {**os.environ, "INSTALL_DIR": str(fixture),
                   "ODS_AGENT_FORCE_SESSION": "false", "TEST_TRACE": str(trace),
                   "TEST_OS": "Linux",
                   "TEST_ACTION": action, "TEST_FAIL": failure}
            result = subprocess.run(["bash", "-s", "--", str(TARGET)], input=PROBE,
                                    text=True, capture_output=True, env=env, timeout=10)
            calls = trace.read_text().splitlines() if trace.exists() else []
            return result, calls, result.stdout + result.stderr

    def test_start_and_stop_success(self):
        for manager in ("systemd",):
            for action in ("start", "stop"):
                with self.subTest(manager=manager, action=action):
                    result, calls, output = self.probe(manager, action, "none")
                    self.assertEqual(result.returncode, 0, output)
                    self.assertEqual(calls, [action])
                    self.assertIn("SUCCESS:Agent " + ("started" if action == "start" else "stopped"), output)

    def test_start_and_stop_failure(self):
        for manager in ("systemd",):
            for action in ("start", "stop"):
                with self.subTest(manager=manager, action=action):
                    result, calls, output = self.probe(manager, action, action)
                    self.assertNotEqual(result.returncode, 0, output)
                    self.assertEqual(calls, [action])
                    self.assertIn("ERROR:Agent " + action + " failed", output)
                    self.assertNotIn("SUCCESS:", output)
                    self.assertNotIn("Agent not running", output)

    def test_restart_short_circuits_failed_stop(self):
        for manager in ("systemd",):
            with self.subTest(manager=manager):
                result, calls, output = self.probe(manager, "restart", "stop")
                self.assertNotEqual(result.returncode, 0, output)
                self.assertEqual(calls, ["stop"])

    def test_restart_propagates_failed_start(self):
        for manager in ("systemd",):
            with self.subTest(manager=manager):
                result, calls, output = self.probe(manager, "restart", "start")
                self.assertNotEqual(result.returncode, 0, output)
                self.assertEqual(calls, ["stop", "start"])
                self.assertIn("ERROR:Agent start failed", output)

    def test_guarded_update_remains_nonfatal(self):
        for manager in ("systemd",):
            with self.subTest(manager=manager):
                result, calls, output = self.probe(manager, "update-guard", "stop")
                self.assertEqual(result.returncode, 0, output)
                self.assertEqual(calls, ["stop"])
                self.assertIn("Host agent restart failed (non-fatal)", output)
                self.assertIn("Update complete", output)


if __name__ == "__main__":
    unittest.main()
