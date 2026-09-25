"""Support collection must finish when a read-only diagnostic stops answering."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess

import pytest


@pytest.mark.parametrize("stalled,label,path", [
    ("version", "docker-version", "docker/version.txt"),
    ("ps", "docker-container-names", "docker/container-names.txt"),
    ("logs", "docker-logs:ods-fixture", "logs/ods-fixture.log"),
    ("doctor", "ods-doctor", "diagnostics/ods-doctor.log"),
    ("resolver", "resolve-compose-stack", "validation/compose-flags.err"),
])
def test_bundle_retains_partial_output_and_continues_after_timeout(tmp_path, stalled, label, path):
    scripts = tmp_path / "install with spaces/scripts"
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "scripts/ods-support-bundle.sh"
    script = scripts / source.name
    shutil.copyfile(source, script)
    command = tmp_path / "diagnostic-fixture"
    command.write_text("""#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "$ODS_TEST_STALLED" && ( "$1" != ps || "$*" == 'ps --format {{.Names}}' ) ]]; then
    printf 'partial diagnostic output\\n'
    printf '%s\\n' "$$" >> "$ODS_TEST_PIDS"
    sleep 60
fi
case "$1" in
    ps) printf 'ods-fixture\\n' ;;
    doctor) printf '{}' > "$2" ;;
    resolver) printf '%s\\n' '-f docker-compose.base.yml' ;;
    *) printf 'diagnostic finished\\n' ;;
esac
""")
    command.chmod(0o755)
    for filename, action in [("ods-doctor.sh", "doctor"), ("resolve-compose-stack.sh", "resolver")]:
        target = scripts / filename
        target.write_text('#!/usr/bin/env bash\nexec "$ODS_TEST_COMMAND" ' + action + ' "$@"\n')
        target.chmod(0o755)
    pids = tmp_path / "pids"
    environment = dict(os.environ, ODS_SUPPORT_BUNDLE_DOCKER=str(command),
                       ODS_SUPPORT_COMMAND_TIMEOUT="1", ODS_TEST_STALLED=stalled,
                       ODS_TEST_COMMAND=str(command), ODS_TEST_PIDS=str(pids))
    process = subprocess.Popen(["bash", str(script), "--output", str(tmp_path / "bundles"), "--json"],
                               env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        pytest.fail(f"support collection hung on {stalled}")
    assert process.returncode == 0, stderr
    receipt = json.loads(stdout)
    assert receipt["archive_exists"]
    bundle = Path(receipt["bundle_dir"])
    manifest = json.loads((bundle / "manifest.json").read_text())
    row = next(item for item in manifest["commands"] if item["label"] == label)
    assert row["exit_code"] == 124
    partial_path = "validation/compose-flags.txt" if stalled == "resolver" else path
    assert "partial diagnostic output" in (bundle / partial_path).read_text()
    assert "timed out after 1 seconds" in (bundle / path).read_text()
    for pid in pids.read_text().splitlines():
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid), 0)


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "1.5", "999999999999999999"])
def test_invalid_timeout_is_rejected_before_creating_output(tmp_path, value):
    script = Path(__file__).resolve().parents[1] / "scripts/ods-support-bundle.sh"
    output = tmp_path / "must-not-exist"
    result = subprocess.run(["bash", str(script), "--output", str(output)],
                            env=dict(os.environ, ODS_SUPPORT_COMMAND_TIMEOUT=value),
                            capture_output=True, text=True, timeout=8)
    assert result.returncode == 2
    assert "ODS_SUPPORT_COMMAND_TIMEOUT" in result.stderr
    assert not output.exists()
