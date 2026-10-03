"""A support archive must not collect unrelated containers matching '*ods*'."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest


@pytest.mark.parametrize('foreign_count', [2, 30])
def test_archive_logs_only_include_the_ods_container_namespace(tmp_path, foreign_count):
    scripts = tmp_path / 'install/scripts'
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / 'scripts/ods-support-bundle.sh'
    script = scripts / source.name
    shutil.copyfile(source, script)
    docker = tmp_path / 'docker-fixture'
    calls = tmp_path / 'log-calls'
    names = [f'private-pods-{index}' for index in range(foreign_count)] + ['ods-hermes', 'ods-langfuse-postgres']
    docker.write_text('''#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  version|info|compose) printf 'fixture docker\n' ;;
  ps) cat "$ODS_TEST_NAMES" ;;
  logs) printf '%s\n' "${!#}" >> "$ODS_TEST_LOG_CALLS"; printf 'log for %s\n' "${!#}" ;;
  *) exit 2 ;;
esac
''')
    docker.chmod(0o755)
    names_file = tmp_path / 'names'
    names_file.write_text('\n'.join(names) + '\n')
    environment = dict(os.environ, ODS_SUPPORT_BUNDLE_DOCKER=str(docker),
                       ODS_TEST_NAMES=str(names_file), ODS_TEST_LOG_CALLS=str(calls))
    result = subprocess.run(['bash', str(script), '--output', str(tmp_path / 'bundles'), '--json'], env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert calls.read_text().splitlines() == ['ods-hermes', 'ods-langfuse-postgres']
    with tarfile.open(receipt['archive']) as archive:
        logs = [member.name.rsplit('/', 1)[-1] for member in archive.getmembers() if '/logs/' in member.name and member.isfile()]
    assert sorted(logs) == ['ods-hermes.log', 'ods-langfuse-postgres.log']
