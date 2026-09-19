"""Environment reader in ods-support-bundle must respect dotenv precedence and strip inline comments."""

import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path


def test_support_bundle_reads_env_with_precedence_and_comments(tmp_path):
    scripts = tmp_path / "install/scripts"
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "scripts/ods-support-bundle.sh"
    script = scripts / source.name
    shutil.copyfile(source, script)

    resolve_mock = scripts / "resolve-compose-stack.sh"
    resolve_mock.write_text("#!/usr/bin/env bash\necho dummy-overlay\n")
    resolve_mock.chmod(0o755)

    # Write .env with earlier defaults followed by later overrides and comments
    (scripts.parent / ".env").write_text(
        "GPU_BACKEND=cpu\n"
        "TIER=1\n"
        "GPU_BACKEND=rocm # AMD ROCm accelerator\n"
        'TIER="2" # production tier override\n'
        "ODS_MODE='cloud' # target mode\n"
    )

    docker = tmp_path / "docker-fixture"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'case "$1" in\n'
        "  version|info|compose) printf 'fixture docker\\n' ;;\n"
        "  ps) printf '' ;;\n"
        "  logs) printf '' ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    docker.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script), "--output", str(tmp_path / "bundles"), "--no-logs", "--json"],
        env=dict(os.environ, ODS_SUPPORT_BUNDLE_DOCKER=str(docker), COPYFILE_DISABLE="1"),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    with tarfile.open(receipt["archive"]) as archive:
        flags_member = next(
            m
            for m in archive.getmembers()
            if m.name.endswith("validation/compose-flags.txt")
            and not Path(m.name).name.startswith("._")
        )
        content = archive.extractfile(flags_member).read().decode()

    assert "GPU_BACKEND=rocm\n" in content
    assert "TIER=2\n" in content
    assert "ODS_MODE=cloud\n" in content
    assert "#" not in content.split("COMPOSE_FLAGS")[0]


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        test_support_bundle_reads_env_with_precedence_and_comments(Path(temp_dir))
    print("test_support_bundle_reads_env_precedence: OK")
