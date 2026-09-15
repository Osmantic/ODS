"""Run the shipped launcher with only uvicorn replaced by a recording process."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != "posix", reason="Bash launcher runs on POSIX hosts")
@pytest.mark.parametrize("invocation", ["absolute", "relative", "service"])
@pytest.mark.parametrize("configured", [False, True])
def test_launcher_reaches_uvicorn_with_valid_environment(tmp_path, invocation, configured):
    source = Path(__file__).resolve().parents[1]
    root = tmp_path / "ODS checkout with spaces"
    service = root / "extensions/services/token-spy"
    service.mkdir(parents=True)
    (root / "lib").mkdir()
    shutil.copy2(source / "start.sh", service)
    shutil.copy2(source.parents[2] / "lib/safe-env.sh", root / "lib")
    tools = tmp_path / "bin"
    tools.mkdir()
    capture = tmp_path / "launched.json"
    python = tools / "python3"
    python.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
if sys.argv[1:2] == ['-c']:
    raise SystemExit(0)
with open(os.environ['LAUNCH_CAPTURE'], 'w') as output:
    json.dump({'cwd': os.getcwd(), 'args': sys.argv[1:],
               'agent': os.environ.get('AGENT_NAME'),
               'sessions': json.loads(os.environ['AGENT_SESSION_DIRS']),
               'key': os.environ.get('TOKEN_SPY_API_KEY')}, output)
''', encoding="utf-8")
    python.chmod(0o700)
    if configured:
        (service / ".env").write_text(
            "AGENT_NAME=research\nPORT=9222\nTOKEN_SPY_API_KEY='fixture=key'\n"
            "AGENT_SESSION_DIRS='{\"research\":\"/tmp/session files\"}'\n", encoding="utf-8")
    command, cwd = {
        "absolute": (str(service / "start.sh"), tmp_path),
        "relative": ("extensions/services/token-spy/start.sh", root),
        "service": ("./start.sh", service),
    }[invocation]
    result = subprocess.run(["bash", command], cwd=cwd, capture_output=True, text=True,
                            env={"PATH": f"{tools}:/usr/bin:/bin", "HOME": str(tmp_path),
                                 "LAUNCH_CAPTURE": str(capture)}, timeout=10)
    assert result.returncode == 0, result.stderr
    observed = json.loads(capture.read_text())
    assert observed == {
        "cwd": str(service),
        "args": ["-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port",
                 "9222" if configured else "9110", "--log-level", "warning"],
        "agent": "research" if configured else "openclaw",
        "sessions": {"research": "/tmp/session files"} if configured else {
            "openclaw": "~/ods/data/openclaw/home/agents/main/sessions"},
        "key": "fixture=key" if configured else None,
    }
