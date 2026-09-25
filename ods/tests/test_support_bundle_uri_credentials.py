"""Database/broker URI credentials must not survive a shareable archive."""

import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path


def test_archive_redacts_non_http_uri_credentials(tmp_path):
    scripts = tmp_path / "install/scripts"
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "scripts/ods-support-bundle.sh"
    script = scripts / source.name
    shutil.copyfile(source, script)
    schemes = ["postgres", "postgresql", "mongodb+srv", "mysql", "redis", "amqps", "https"]
    secrets = [f"private-password-{index}" for index in range(len(schemes))]
    uris = [f"{scheme}://operator:{secret}@database.example:1234/app"
            for scheme, secret in zip(schemes, secrets)]
    # Generic URI-valued config keys are not classified as secret key names.
    (scripts.parent / ".env").write_text(
        "\n".join(f"CONNECTION_{index}={uri}" for index, uri in enumerate(uris)) + "\n"
    )
    log = tmp_path / "log"
    log.write_text("\n".join(uris) + "\nhttps://docs.example/guide\n")
    docker = tmp_path / "docker-fixture"
    docker.write_text('''#!/usr/bin/env bash
set -eu
case "$1" in
  version|info|compose) printf 'fixture docker\n' ;;
  ps) printf 'ods-database\n' ;;
  logs) cat "$ODS_TEST_URI_LOG" ;;
  *) exit 2 ;;
esac
''')
    docker.chmod(0o755)
    result = subprocess.run(
        ["bash", str(script), "--output", str(tmp_path / "bundles"), "--json"],
        env=dict(os.environ, ODS_SUPPORT_BUNDLE_DOCKER=str(docker), ODS_TEST_URI_LOG=str(log)),
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    with tarfile.open(receipt["archive"]) as archive:
        files = {member.name: archive.extractfile(member).read().decode()
                 for member in archive.getmembers() if member.isfile()}
    for name, text in files.items():
        for secret in secrets:
            assert secret not in text, name
    for suffix in ["/logs/ods-database.log", "/config/env.redacted", "/manifest/evidence.json"]:
        text = next(value for name, value in files.items() if name.endswith(suffix))
        for scheme in schemes:
            assert f"{scheme}://[REDACTED]@database.example:1234/app" in text
    logs = next(value for name, value in files.items() if name.endswith("/logs/ods-database.log"))
    assert "https://docs.example/guide" in logs
