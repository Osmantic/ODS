import errno
import hashlib
import json
import os
from pathlib import Path
import re
import select
import subprocess
import tempfile
import time
import unittest


if os.name == "posix":
    import fcntl
    import pty
    import termios


ROOT = Path(__file__).resolve().parents[1]
PROMPT_RE = re.compile(rb"Type exactly: (APPROVE [a-f0-9]{12} [a-f0-9]{16})")


@unittest.skipUnless(os.name == "posix", "human approval requires a POSIX controlling terminal")
class HumanApprovalBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.log = self.root / "sudo.log"
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self._write_executable(
            self.fake_bin / "sudo",
            """#!/usr/bin/env bash
set -euo pipefail
{
  printf 'sudo'
  printf '\\t%s' "$@"
  printf '\\n'
} >>"$PIXEL_APPROVAL_TEST_LOG"
case "${1:-}" in
  -K) exit 0 ;;
  -n)
    [[ ${2:-} == -v ]] || exit 2
    [[ ${PIXEL_TEST_PASSWORDLESS:-0} == 1 ]] && exit 0
    exit 1
    ;;
  -v) exit 0 ;;
  -u) shift 2 ;;
esac
if [[ ${1:-} == */systemctl ]]; then exit 0; fi
exec "$@"
""",
        )
        self._write_executable(self.fake_bin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
        self.harness_common = self.root / "common.sh"
        self._write_harness_common(self.harness_common)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _write_executable(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        path.chmod(0o755)

    @staticmethod
    def _write_env(path, values):
        def quote(value):
            return "'" + str(value).replace("'", "'\\''") + "'"

        path.write_text(
            "\n".join(f"{name}={quote(value)}" for name, value in values.items()) + "\n",
            encoding="utf-8",
        )

    def _environment(self, *, passwordless=False):
        environment = os.environ.copy()
        environment["PATH"] = f"{self.fake_bin}{os.pathsep}{environment['PATH']}"
        environment["PIXEL_APPROVAL_TEST_LOG"] = str(self.log)
        environment["PIXEL_TEST_PASSWORDLESS"] = "1" if passwordless else "0"
        return environment

    def _write_harness_common(self, destination):
        value = (ROOT / "scripts/lib/common.sh").read_text(encoding="utf-8")
        value = value.replace(
            "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
            f"PATH={self.fake_bin}:/usr/sbin:/usr/bin:/sbin:/bin",
        )
        value = value.replace(
            "PIXEL_HUMAN_APPROVAL_SUDO=/usr/bin/sudo",
            f"PIXEL_HUMAN_APPROVAL_SUDO={self.fake_bin / 'sudo'}",
        )
        value = value.replace(
            '[[ $EUID -ne 0 ]] || pixel_die "Human approval must run as the non-root deployment owner"',
            '[[ 1 -eq 1 ]] || pixel_die "Human approval must run as the non-root deployment owner"',
        )
        destination.write_text(value, encoding="utf-8")

    def _copy_wrapper(self, name, destination):
        scripts = destination / "scripts"
        (scripts / "lib").mkdir(parents=True)
        self._write_harness_common(scripts / "lib/common.sh")
        value = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        if name in {"approve-source-action.sh", "reconcile-source-action.sh"}:
            value = value.replace("/usr/bin/systemctl", str(self.fake_bin / "systemctl"))
        if name == "reconcile-source-action.sh":
            value = value.replace("/usr/bin/sudo", str(self.fake_bin / "sudo"))
        (scripts / name).write_text(value, encoding="utf-8")

    def _run_pty(self, arguments, cwd, environment, *, response="exact", timeout=10):
        master, slave = pty.openpty()

        def establish_controlling_terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        process = subprocess.Popen(
            arguments,
            cwd=cwd,
            env=environment,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            preexec_fn=establish_controlling_terminal,
        )
        os.close(slave)
        output = bytearray()
        responded = False
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.05)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    output.extend(chunk)
                    match = PROMPT_RE.search(output)
                    if match and not responded and response != "none":
                        reply = match.group(1) if response == "exact" else b"DECLINE"
                        os.write(master, reply + b"\n")
                        responded = True
                elif process.poll() is not None:
                    break
            if process.poll() is None:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                    self.fail(f"approval command did not finish; output={output.decode(errors='replace')}")
            return process.returncode, bytes(output)
        finally:
            os.close(master)

    def _prepare_broker_wrapper(self, kind):
        layout = self.root / kind
        wrapper = f"approve-{kind}-job.sh"
        self._copy_wrapper(wrapper, layout)
        state = layout / "state"
        (state / "plans").mkdir(parents=True)
        (state / "results").mkdir()
        broker_install = layout / "broker"
        job_id = f"{kind}-1700000000000-aabbccddeeff"
        plan_hash = "a" * 64 if kind == "frontier" else "b" * 64
        (state / "plans" / f"{job_id}.json").write_text(
            json.dumps({
                "jobId": job_id,
                "planHash": plan_hash,
                "reviewMarker": f"{kind}-protected-plan",
                "terminalSpoof": "\u202e\u001b[2J",
            }) + "\n",
            encoding="utf-8",
        )
        self._write_executable(
            broker_install / "broker.py",
            """#!/usr/bin/env bash
printf 'broker\\t%s\\n' "$*" >>"$PIXEL_APPROVAL_TEST_LOG"
""",
        )
        prefix = "PIXEL_FRONTIER" if kind == "frontier" else "PIXEL_OPS"
        self._write_env(layout / ".env", {
            "OPENCLAW_HOME": layout / "openclaw",
            f"{prefix}_BROKER_ENABLED": 1,
            f"{prefix}_BROKER_STATE_DIR": state,
            f"{prefix}_RESULT_DIR": state / "results",
            f"{prefix}_BROKER_INSTALL_DIR": broker_install,
            f"{prefix}_BROKER_USER": f"pixel-{kind}-broker",
            f"{prefix}_POLICY_PATH": layout / "policy.json",
        })
        return (
            ["bash", f"scripts/{wrapper}", job_id, plan_hash, "--confirm"],
            layout,
            f"{kind}-protected-plan".encode(),
            b"broker\t",
        )

    def _prepare_source_wrapper(self):
        layout = self.root / "source"
        self._copy_wrapper("approve-source-action.sh", layout)
        results = layout / "results"
        results.mkdir(parents=True)
        proposal = "calendar-1700000000000-aabbccdd"
        approved = results / f"{proposal}.approved.json"
        approved.write_text(
            json.dumps({"proposalId": proposal, "reviewMarker": "source-protected-plan"}) + "\n",
            encoding="utf-8",
        )
        proposal_hash = hashlib.sha256(approved.read_bytes()).hexdigest()
        (results / f"{proposal}.json").write_text(
            json.dumps({
                "proposalSha256": proposal_hash,
                "approvalBinding": "sha256-protected-proposal-snapshot",
            }) + "\n",
            encoding="utf-8",
        )
        self._write_env(layout / ".env", {
            "OPENCLAW_HOME": layout / "openclaw",
            "PIXEL_ACTION_RESULT_DIR": results,
            "PIXEL_SOURCE_BROKER_UNIT": "pixel-source-broker.service",
        })
        return (
            ["bash", "scripts/approve-source-action.sh", proposal, proposal_hash, "--confirm"],
            layout,
            b"source-protected-plan",
            f"sudo\t{self.fake_bin / 'systemctl'}\tstart".encode(),
        )

    def test_calendar_reconciliation_uses_the_read_only_unit_and_requires_exact_result(self):
        layout = self.root / "source-reconcile"
        self._copy_wrapper("reconcile-source-action.sh", layout)
        results = layout / "results"
        results.mkdir(parents=True)
        proposal = "calendar-1700000000000-aabbccdd"
        (results / f"{proposal}.json").write_text(json.dumps({
            "schemaVersion": 1,
            "proposalId": proposal,
            "proposalSha256": "a" * 64,
            "status": "applied",
            "action": "create",
            "affectedEventId": "p1e1" + "b" * 64,
            "eventPrecondition": "deterministic-provider-event-id",
            "reconciliation": "provider-get-after-indeterminate-write",
        }) + "\n", encoding="utf-8")
        self._write_env(layout / ".env", {
            "OPENCLAW_HOME": layout / "openclaw",
            "PIXEL_ACTION_RESULT_DIR": results,
            "PIXEL_SOURCE_RECONCILE_UNIT": "pixel-source-reconcile@.service",
        })
        result = subprocess.run(
            ["bash", "scripts/reconcile-source-action.sh", proposal],
            cwd=layout,
            env=self._environment(),
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b'"provider-get-after-indeterminate-write"', result.stdout)
        self.assertIn(
            f"sudo\t{self.fake_bin / 'systemctl'}\tstart\tpixel-source-reconcile@{proposal}.service".encode(),
            self.log.read_bytes(),
        )

    def test_calendar_update_reconciliation_accepts_only_a_content_free_terminal_classification(self):
        layout = self.root / "source-reconcile-update"
        self._copy_wrapper("reconcile-source-action.sh", layout)
        results = layout / "results"
        results.mkdir(parents=True)
        proposal = "calendar-1700000000000-aabbccdd"
        (results / f"{proposal}.json").write_text(json.dumps({
            "schemaVersion": 1,
            "proposalId": proposal,
            "proposalSha256": "a" * 64,
            "legacyProcessingClaimSha256": "b" * 64,
            "providerObservationSha256": "c" * 64,
            "status": "not-applied",
            "action": "update",
            "affectedEventId": "event-1",
            "eventPrecondition": "if-match",
            "retryAllowed": False,
            "nextAction": "fresh-proposal-required",
            "causationAsserted": False,
            "reconciliation": "legacy-provider-etag-proves-not-applied",
        }) + "\n", encoding="utf-8")
        self._write_env(layout / ".env", {
            "OPENCLAW_HOME": layout / "openclaw",
            "PIXEL_ACTION_RESULT_DIR": results,
            "PIXEL_SOURCE_RECONCILE_UNIT": "pixel-source-reconcile@.service",
        })
        result = subprocess.run(
            ["bash", "scripts/reconcile-source-action.sh", proposal],
            cwd=layout,
            env=self._environment(),
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b'"legacy-provider-etag-proves-not-applied"', result.stdout)
        self.assertIn(
            f"sudo\t{self.fake_bin / 'systemctl'}\tstart\tpixel-source-reconcile@{proposal}.service".encode(),
            self.log.read_bytes(),
        )

    def test_non_terminal_invocation_fails_before_requesting_administrator_access(self):
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; pixel_begin_human_approval_session "test approval"', "bash", str(self.harness_common)],
            env=self._environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"real interactive terminal", result.stderr)
        self.assertFalse(self.log.exists())

    def test_passwordless_sudo_fails_closed_instead_of_claiming_human_authentication(self):
        result, output = self._run_pty(
            ["bash", "-c", 'source "$1"; pixel_begin_human_approval_session "test approval"', "bash", str(self.harness_common)],
            ROOT,
            self._environment(passwordless=True),
            response="none",
        )
        self.assertNotEqual(result, 0)
        self.assertIn(b"Passwordless administrator access cannot establish", output)
        self.assertNotIn(b"Type exactly:", output)
        self.assertTrue(self.log.read_bytes().endswith(b"sudo\t-K\n"))

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "root-only refusal probe")
    def test_root_invocation_fails_before_administrator_validation(self):
        result, output = self._run_pty(
            [
                "bash", "-c",
                'source "$1"; pixel_begin_human_approval_session "test approval"',
                "bash", str(ROOT / "scripts/lib/common.sh"),
            ],
            ROOT,
            self._environment(),
            response="none",
        )
        self.assertNotEqual(result, 0)
        self.assertIn(b"must run as the non-root deployment owner", output)

    def test_production_boundary_pins_the_system_path_and_administrator_binary(self):
        common = (ROOT / "scripts/lib/common.sh").read_text(encoding="utf-8")
        self.assertIn("PIXEL_HUMAN_APPROVAL_SUDO=/usr/bin/sudo", common)
        self.assertIn("[[ $EUID -ne 0 ]]", common)
        self.assertIn("PATH=/usr/sbin:/usr/bin:/sbin:/bin", common)
        self.assertNotIn("PIXEL_HUMAN_APPROVAL_SUDO=${", common)
        source_wrapper = (ROOT / "scripts/approve-source-action.sh").read_text(encoding="utf-8")
        self.assertIn('"$PIXEL_HUMAN_APPROVAL_SUDO" /usr/bin/systemctl', source_wrapper)

    def test_wrong_one_time_phrase_never_reaches_frontier_broker(self):
        arguments, cwd, marker, _ = self._prepare_broker_wrapper("frontier")
        result, output = self._run_pty(arguments, cwd, self._environment(), response="wrong")
        self.assertNotEqual(result, 0)
        self.assertIn(marker, output)
        self.assertIn(b"nothing was approved", output)
        self.assertNotIn("broker\t", self.log.read_text(encoding="utf-8"))

    def test_mismatched_protected_plan_fails_before_terminal_confirmation(self):
        arguments, cwd, marker, _ = self._prepare_broker_wrapper("frontier")
        plan_path = cwd / "state/plans" / f"{arguments[2]}.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["planHash"] = "c" * 64
        plan_path.write_text(json.dumps(plan) + "\n", encoding="utf-8")
        result, output = self._run_pty(arguments, cwd, self._environment(), response="none")
        self.assertNotEqual(result, 0)
        self.assertNotIn(marker, output)
        self.assertNotIn(b"Type exactly:", output)
        self.assertIn(b"does not match the supplied job and hash", output)
        self.assertNotIn("broker\t", self.log.read_text(encoding="utf-8"))

    def test_each_private_plan_wrapper_authenticates_displays_confirms_then_executes(self):
        cases = [
            self._prepare_broker_wrapper("frontier"),
            self._prepare_broker_wrapper("ops"),
            self._prepare_source_wrapper(),
        ]
        for index, (arguments, cwd, marker, execution_marker) in enumerate(cases):
            with self.subTest(arguments=arguments):
                case_log = self.root / f"case-{index}.log"
                environment = self._environment()
                environment["PIXEL_APPROVAL_TEST_LOG"] = str(case_log)
                result, output = self._run_pty(arguments, cwd, environment)
                self.assertEqual(result, 0, output.decode(errors="replace"))
                self.assertIn(marker, output)
                self.assertRegex(output, PROMPT_RE)
                if b"protected-plan" in marker and b"source" not in marker:
                    self.assertIn(b"\\u202e\\u001b[2J", output)
                    self.assertNotIn("\u202e".encode(), output)
                    self.assertNotIn(b"\x1b[2J", output)
                log = case_log.read_bytes()
                self.assertTrue(log.startswith(b"sudo\t-K\nsudo\t-n\t-v\nsudo\t-v\n"), log)
                display_command = b"sudo\t/usr/bin/jq\t"
                self.assertLess(log.index(display_command), log.index(execution_marker))
                self.assertTrue(log.endswith(b"sudo\t-K\n"), log)


if __name__ == "__main__":
    unittest.main()
