#!/usr/bin/env python3
"""Doctor must not fail when Open WebUI is intentionally disabled.

These tests exercise the real shell probe (with a stubbed curl), the real
embedded report writer, and the real CLI display code. They are behavioral:
they run the extracted shell snippet and the embedded Python, not string
matches against the source.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCTOR_SH = Path(os.environ.get("ODS_DOCTOR_UNDER_TEST", REPO_ROOT / "scripts" / "ods-doctor.sh"))
ODS_CLI = Path(os.environ.get("ODS_CLI_UNDER_TEST", REPO_ROOT / "ods-cli"))


BASH = shutil.which("bash") or "/bin/bash"


def _extract_webui_probe_snippet() -> str:
    """Pull the real WebUI probe block out of ods-doctor.sh.

    The block is the `if curl ... _WEBUI_PORT ...` guarded by WEBUI_ENABLED.
    We extract it verbatim so the test runs the shipped code, not a copy.
    """
    text = DOCTOR_SH.read_text(encoding="utf-8")
    # Find the WEBUI_ENABLED default block.
    m = re.search(
        r'WEBUI_ENABLED="true"\nif \[\[ "\$\{ENABLE_OPEN_WEBUI:-\}" == "false" \]\]; then\n    WEBUI_ENABLED="false"\nfi',
        text,
    )
    if not m:
        raise AssertionError("could not locate WEBUI_ENABLED default block in ods-doctor.sh")
    enabled_block = m.group(0)

    # Find the probe block.
    m2 = re.search(
        r'if \[\[ "\$WEBUI_ENABLED" == "true" \]\] \\\n        && curl -sf --max-time 10 "http://127\.0\.0\.1:\$\{_WEBUI_PORT\}" >/dev/null 2>&1; then\n        WEBUI_HTTP="true"\n    fi',
        text,
    )
    if not m2:
        raise AssertionError("could not locate WebUI probe block in ods-doctor.sh")
    probe_block = m2.group(0)

    return enabled_block + "\n" + probe_block


def _run_probe(env_value, curl_should_succeed):
    """Run the real probe snippet with a stubbed curl.

    Returns (WEBUI_HTTP, WEBUI_ENABLED, curl_calls).
    """
    snippet = _extract_webui_probe_snippet()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        curl_log = tmp_path / "curl.log"
        curl_stub = tmp_path / "curl"
        exit_code = 0 if curl_should_succeed else 1
        curl_stub.write_text(
            "#!/bin/sh\n"
            f'echo "$@" >> "{curl_log}"\n'
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        curl_stub.chmod(0o755)

        script = textwrap.dedent(
            f"""
            set -euo pipefail
            _WEBUI_PORT=3000
            WEBUI_HTTP="false"
            {snippet}
            printf 'WEBUI_HTTP=%s\\n' "$WEBUI_HTTP"
            printf 'WEBUI_ENABLED=%s\\n' "$WEBUI_ENABLED"
            """
        )

        env = dict(os.environ)
        env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"
        if env_value is None:
            env.pop("ENABLE_OPEN_WEBUI", None)
        else:
            env["ENABLE_OPEN_WEBUI"] = env_value

        result = subprocess.run(
            [BASH, "-c", script],
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"probe snippet failed: rc={result.returncode}\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )

        webui_http = None
        webui_enabled = None
        for line in result.stdout.splitlines():
            if line.startswith("WEBUI_HTTP="):
                webui_http = line.split("=", 1)[1]
            elif line.startswith("WEBUI_ENABLED="):
                webui_enabled = line.split("=", 1)[1]

        curl_calls = curl_log.read_text(encoding="utf-8") if curl_log.exists() else ""
        return webui_http, webui_enabled, curl_calls


class WebUIProbeTests(unittest.TestCase):
    """The real shell probe must skip curl when WebUI is disabled."""

    def test_disabled_skips_curl_and_leaves_http_false(self):
        webui_http, webui_enabled, curl_calls = _run_probe("false", curl_should_succeed=True)
        self.assertEqual(webui_enabled, "false")
        self.assertEqual(webui_http, "false")
        self.assertEqual(curl_calls, "", "curl must not be invoked when WebUI is disabled")

    def test_enabled_probes_and_succeeds(self):
        webui_http, webui_enabled, curl_calls = _run_probe("true", curl_should_succeed=True)
        self.assertEqual(webui_enabled, "true")
        self.assertEqual(webui_http, "true")
        self.assertIn("127.0.0.1:3000", curl_calls)

    def test_unset_defaults_to_enabled_and_probes(self):
        webui_http, webui_enabled, curl_calls = _run_probe(None, curl_should_succeed=True)
        self.assertEqual(webui_enabled, "true")
        self.assertEqual(webui_http, "true")
        self.assertIn("127.0.0.1:3000", curl_calls)

    def test_enabled_but_down_reports_false(self):
        webui_http, webui_enabled, curl_calls = _run_probe("true", curl_should_succeed=False)
        self.assertEqual(webui_enabled, "true")
        self.assertEqual(webui_http, "false")
        self.assertIn("127.0.0.1:3000", curl_calls)

    def test_other_value_is_enabled(self):
        # Only the exact literal "false" disables; anything else keeps it on.
        webui_http, webui_enabled, curl_calls = _run_probe("False", curl_should_succeed=True)
        self.assertEqual(webui_enabled, "true")
        self.assertEqual(webui_http, "true")
        self.assertIn("127.0.0.1:3000", curl_calls)


def _extract_report_writer() -> str:
    """Extract the embedded Python report writer from ods-doctor.sh."""
    text = DOCTOR_SH.read_text(encoding="utf-8")
    # The writer is the heredoc that starts with `import json` after the
    # `"$PYTHON_CMD" - ... <<'PY'` invocation and ends with the closing PY.
    m = re.search(
        r'"\$PYTHON_CMD" - .*?<<\'PY\'\n(.*?)\nPY\n',
        text,
        re.DOTALL,
    )
    if not m:
        raise AssertionError("could not locate embedded report writer in ods-doctor.sh")
    return m.group(1)


def _run_report_writer(webui_enabled, webui_http, docker_daemon="true", compose_cli="true"):
    """Run the real embedded report writer with harmless temp inputs."""
    writer = _extract_report_writer()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cap_file = tmp_path / "cap.json"
        pre_file = tmp_path / "pre.json"
        report_file = tmp_path / "report.json"
        cap_file.write_text(json.dumps({"recommended_tier": "T1"}), encoding="utf-8")
        pre_file.write_text(
            json.dumps({"checks": [], "summary": {"blockers": 0, "warnings": 0}}),
            encoding="utf-8",
        )

        argv = [
            str(cap_file),
            str(pre_file),
            str(report_file),
            "true",  # docker_cli
            docker_daemon,
            compose_cli,
            "false",  # dashboard_http
            webui_http,
            "3001",  # dashboard_port
            "3000",  # webui_port
            "[]",  # ext_diagnostics_json
            "disabled",  # stt_cached
            "",  # stt_model_name
            "",  # stt_recovery
            "disabled",  # tts_http
            "",  # tts_port
            "false",  # dgx_spark_gpu
            "",  # dgx_spark_gpu_name
            "",  # dgx_spark_compute_cap
            "",  # llama_cuda_archs
            "unknown",  # dgx_spark_arch_status
            "",  # dgx_spark_arch_message
            "0",  # hermes_slash_worker_count
            "8",  # hermes_slash_worker_max_count
            "0",  # ods_managed_container_count
            "0",  # ods_running_container_count
            str(tmp_path),  # root_dir
            webui_enabled,
        ]

        env = dict(os.environ)
        env["LLM_STATUS"] = "ok"
        env["LLM_PROVIDER"] = "llama-server"
        env["LLM_URL"] = "http://127.0.0.1:11434"
        env["LLM_MODEL"] = "test"
        env["LLM_LOCAL_WARNING"] = "false"
        env["LLM_RECOVERY"] = ""
        env["LLM_FAILURE"] = ""
        env["ROOTLESS_DOCKER"] = "false"
        env["ROOTLESS_SUBID_STATUS"] = "skipped"
        env["ROOTLESS_SUBID_USER"] = ""
        env["ROOTLESS_SUBUID_TOTAL"] = "0"
        env["ROOTLESS_SUBGID_TOTAL"] = "0"

        result = subprocess.run(
            [sys.executable, "-", *argv],
            input=writer,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
        )
        if result.returncode != 0:
            raise AssertionError(
                f"report writer failed: rc={result.returncode}\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )
        return json.loads(report_file.read_text(encoding="utf-8"))


class ReportWriterTests(unittest.TestCase):
    """The real embedded writer must emit webui_enabled and gate the hint."""

    def test_disabled_emits_webui_enabled_false_and_no_hint(self):
        report = _run_report_writer(webui_enabled="false", webui_http="false")
        self.assertIs(report["runtime"]["webui_enabled"], False)
        self.assertIs(report["runtime"]["webui_http"], False)
        hints = report.get("autofix_hints", [])
        self.assertFalse(
            any("Open WebUI" in h for h in hints),
            f"disabled WebUI must not produce a fix hint, got: {hints}",
        )

    def test_enabled_down_emits_hint(self):
        report = _run_report_writer(webui_enabled="true", webui_http="false")
        self.assertIs(report["runtime"]["webui_enabled"], True)
        self.assertIs(report["runtime"]["webui_http"], False)
        hints = report.get("autofix_hints", [])
        self.assertTrue(
            any("Open WebUI" in h for h in hints),
            f"enabled-but-down WebUI must produce a fix hint, got: {hints}",
        )

    def test_enabled_up_no_hint(self):
        report = _run_report_writer(webui_enabled="true", webui_http="true")
        self.assertIs(report["runtime"]["webui_enabled"], True)
        self.assertIs(report["runtime"]["webui_http"], True)
        hints = report.get("autofix_hints", [])
        self.assertFalse(any("Open WebUI" in h for h in hints))

    def test_webui_http_field_preserved(self):
        # The existing webui_http field must still be present and boolean.
        report = _run_report_writer(webui_enabled="false", webui_http="false")
        self.assertIn("webui_http", report["runtime"])
        self.assertIsInstance(report["runtime"]["webui_http"], bool)


def _extract_cli_display() -> str:
    """Extract the embedded Python display block from ods-cli cmd_doctor."""
    text = ODS_CLI.read_text(encoding="utf-8")
    m = re.search(
        r'python3 - "\$report_file" <<\'PY\'\n(.*?)\nPY\n',
        text,
        re.DOTALL,
    )
    if not m:
        raise AssertionError("could not locate embedded CLI display in ods-cli")
    return m.group(1)


def _run_cli_display(report_dict):
    """Run the real CLI display code against a report dict."""
    display = _extract_cli_display()
    with tempfile.TemporaryDirectory() as tmp:
        report_file = Path(tmp) / "report.json"
        report_file.write_text(json.dumps(report_dict), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-", str(report_file)],
            input=display,
            capture_output=True,
            text=True,
        )
        result.stdout = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        return result


def _base_report(webui_enabled, webui_http):
    return {
        "version": "1",
        "runtime": {
            "docker_cli": True,
            "docker_daemon": True,
            "compose_cli": True,
            "dashboard_http": True,
            "webui_enabled": webui_enabled,
            "webui_http": webui_http,
            "llm_backend": {
                "status": "ok",
                "provider": "llama-server",
                "url": "http://127.0.0.1:11434",
                "model": "test",
                "local_running_warning": False,
                "recovery_hint": "",
            },
            "stt_model_cached": "disabled",
            "tts_http": "disabled",
        },
        "preflight": {"checks": []},
        "diagnoses": [],
        "autofix_hints": [],
        "summary": {
            "preflight_blockers": 0,
            "preflight_warnings": 0,
            "runtime_warnings": 0,
            "diagnoses_blockers": 0,
            "diagnoses_warnings": 0,
            "runtime_ready": True,
        },
    }


class CliDisplayTests(unittest.TestCase):
    """The real CLI display must not fail on a disabled WebUI."""

    def test_disabled_prints_not_installed_and_exits_zero(self):
        report = _base_report(webui_enabled=False, webui_http=False)
        result = _run_cli_display(report)
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr}")
        self.assertIn("WebUI HTTP (not installed)", result.stdout)
        self.assertNotIn("✗ WebUI HTTP", result.stdout)

    def test_enabled_down_exits_one(self):
        report = _base_report(webui_enabled=True, webui_http=False)
        result = _run_cli_display(report)
        self.assertEqual(result.returncode, 1)
        self.assertIn("✗ WebUI HTTP", result.stdout)

    def test_enabled_up_exits_zero(self):
        report = _base_report(webui_enabled=True, webui_http=True)
        result = _run_cli_display(report)
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr}")
        self.assertIn("✓ WebUI HTTP", result.stdout)

    def test_legacy_report_without_webui_enabled_treated_as_enabled(self):
        # Older reports have no webui_enabled field; the CLI must default to
        # enabled so a down WebUI still fails (no silent regression).
        report = _base_report(webui_enabled=True, webui_http=False)
        del report["runtime"]["webui_enabled"]
        result = _run_cli_display(report)
        self.assertEqual(result.returncode, 1)
        self.assertIn("✗ WebUI HTTP", result.stdout)

    def test_null_core_status_is_still_a_failure(self):
        report = _base_report(webui_enabled=False, webui_http=False)
        report["runtime"]["docker_daemon"] = None
        result = _run_cli_display(report)
        self.assertEqual(result.returncode, 1)
        self.assertIn("✗ Docker Daemon", result.stdout)

    def test_other_failure_still_exits_one(self):
        # A different failure (LLM down) must still exit 1 even with WebUI
        # disabled, so the fix does not mask unrelated problems.
        report = _base_report(webui_enabled=False, webui_http=False)
        report["runtime"]["llm_backend"]["status"] = "fail"
        report["runtime"]["llm_backend"]["recovery_hint"] = "ods restart llama-server"
        result = _run_cli_display(report)
        self.assertEqual(result.returncode, 1)
        self.assertIn("✗ LLM Backend", result.stdout)


if __name__ == "__main__":
    unittest.main()
