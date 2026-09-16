"""Basic auth and single-token URI credentials must not leak into support bundles."""

import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path


def test_archive_redacts_basic_auth_and_token_uris(tmp_path):
    scripts = tmp_path / "install/scripts"
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "scripts/ods-support-bundle.sh"
    script = scripts / source.name
    shutil.copyfile(source, script)

    basic_payload = "dXNlcjpwYXNzd29yZA=="
    git_token = "ghp_secrettoken123456789"
    auth_token = "opaque_token_payload_xyz"

    env_lines = [
        f"TEST_GIT_REMOTE=https://{git_token}@github.com/org/repo.git",
        f"API_ROUTER_URL=https://{git_token}@proxy.internal/v1",
    ]
    (scripts.parent / ".env").write_text("\n".join(env_lines) + "\n")

    docker = tmp_path / "docker-fixture"
    docker.write_text(f'''#!/usr/bin/env bash
set -eu
case "$1" in
  version|info|compose) printf 'fixture docker\\n' ;;
  ps) printf 'ods-test\\n' ;;
  logs)
    printf 'Incoming request: Authorization: Basic {basic_payload}\\n'
    printf 'Upstream sync: https://{git_token}@github.com/org/repo.git\\n'
    printf 'Bearer auth: Authorization: Bearer {auth_token}\\n'
    ;;
  *) exit 2 ;;
esac
''')
    docker.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script), "--output", str(tmp_path / "bundles"), "--json"],
        env=dict(os.environ, ODS_SUPPORT_BUNDLE_DOCKER=str(docker)),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    with tarfile.open(receipt["archive"]) as archive:
        files = {
            member.name: archive.extractfile(member).read().decode(errors="replace")
            for member in archive.getmembers()
            if member.isfile() and not Path(member.name).name.startswith("._")
        }

    env_redacted = next(v for k, v in files.items() if k.endswith("/config/env.redacted"))
    log_content = next(v for k, v in files.items() if k.endswith("/logs/ods-test.log"))

    # Assert secrets do NOT appear anywhere in redacted files
    for name, content in [("env.redacted", env_redacted), ("ods-test.log", log_content)]:
        assert basic_payload not in content, f"Basic auth payload leaked in {name}:\n{content}"
        assert git_token not in content, f"Single-token URI leaked in {name}:\n{content}"
        assert auth_token not in content, f"Token payload leaked in {name}:\n{content}"

    # Assert proper redaction markers exist
    assert "https://[REDACTED]@github.com/org/repo.git" in env_redacted
    assert "https://[REDACTED]@github.com/org/repo.git" in log_content
    assert "Authorization: [REDACTED]" in log_content


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_archive_redacts_basic_auth_and_token_uris(Path(td))
    print("[PASS] test_support_bundle_auth_redaction")
