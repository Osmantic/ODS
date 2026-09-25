"""Exercise the installer platform selector with native tools on a spaced PATH."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("directory", ["native tools", "native [tools]"])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("major,expected", [(22, 0), (18, 1)])
def test_node_selector_preserves_executable_path(tmp_path, directory, explicit, major, expected):
    bindir = tmp_path / directory
    bindir.mkdir()
    node, npm = bindir / "node", bindir / "npm"
    shutil.copyfile(ROOT / "tests/fixtures/node-runtime" / f"node{major}", node)
    shutil.copyfile("/bin/true", npm)
    node.chmod(0o755)
    npm.chmod(0o755)
    args = [str(node), str(npm)] if explicit else []
    result = subprocess.run([
        "bash", "-c", 'set -euo pipefail; source "$1"; shift; ods_linux_node_tools_available "$@"',
        "node-path-fixture", str(ROOT / "installers/lib/node-runtime.sh"), *args,
    ], env=dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"]),
       text=True, capture_output=True, timeout=5)
    assert result.returncode == expected, result.stdout + result.stderr
