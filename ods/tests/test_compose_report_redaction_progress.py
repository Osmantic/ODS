"""The public failure-report command must finish for literal secret values."""

import os
import signal
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "installers/lib/compose-failure-report.sh"


@pytest.mark.parametrize("values", [
    ["[REDACTED]"],
    ["REDACTED"],
    ["DACT"],
    ["REDACTED", "[REDACTED]"],
    ["a+b.*[x]"],
    ["ordinary-value"],
])
def test_report_finishes_without_rescanning_redaction_markers(tmp_path, values):
    install = tmp_path / "installation with spaces"
    install.mkdir()
    (install / ".env").write_text(
        "".join(f"APP_{i}_KEY='{value}'\n" for i, value in enumerate(values)),
        encoding="utf-8",
    )
    config = tmp_path / "compose-output"
    # Untagged occurrences exercise value matching; tagged fields exercise
    # the field-name redactor that also generates the replacement marker.
    config.write_text(
        "description: before " + " / ".join(values) + " after\n"
        + "  APP_KEY: " + values[0] + "\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    commands = {
        "docker": '#!/bin/sh\ncase "$*" in *" config") cat "$REPORT_CONFIG";; *) echo "fixture Docker";; esac\n',
        "lsof": "#!/bin/sh\nexit 0\n",
    }
    for name, content in commands.items():
        executable = bin_dir / name
        executable.write_text(content)
        executable.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REPORT_CONFIG": str(config)}
    process = subprocess.Popen(
        ["bash", "-c", 'source "$1"; write_compose_failure_report "$2" test "docker compose up" "" test "End of fixture"',
         "report-test", str(LIB), str(install)],
        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        pytest.fail("failure-report writer hung while redacting a marker-valued secret")
    assert process.returncode == 0, stderr
    report = Path(stdout.splitlines()[-1]).read_text()
    assert "description: before " + " / ".join("[REDACTED]" for _ in values) + " after" in report
    assert "APP_KEY: [REDACTED]\n" in report
    assert "installer log unavailable" in report
    if values[0] not in "[REDACTED]":
        assert values[0] not in report
