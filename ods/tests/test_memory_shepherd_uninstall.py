"""Exercise uninstall against real unit files with an isolated service manager."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != "posix", reason="Systemd uninstall uses Bash")
@pytest.mark.parametrize("user_mode", [False, True])
@pytest.mark.parametrize("failure", ["", "stop-timer", "disable-timer", "stop-service"])
def test_uninstall_preserves_recovery_files_on_manager_failure(tmp_path, user_mode, failure):
    root = tmp_path / "install with spaces"
    prefix = root / (".config/systemd/user" if user_mode else "units")
    prefix.mkdir(parents=True)
    files = {
        "memory-shepherd.timer": "[Timer]\nOnUnitActiveSec=3h\n",
        "memory-shepherd.service": "[Service]\nType=oneshot\n",
        "unrelated.service": "Preserve unrelated unit\n",
    }
    for name, content in files.items():
        (prefix / name).write_text(content)
    config = root / "memory-shepherd.conf"
    config.write_text("operator configuration\n")
    tools = tmp_path / "bin"
    tools.mkdir()
    log = tmp_path / "manager.jsonl"
    manager = tools / "systemctl"
    manager.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
args = sys.argv[1:]
with open(os.environ['MANAGER_LOG'], 'a') as output:
    output.write(json.dumps(args) + '\\n')
command = [arg for arg in args if arg != '--user']
failures = {'stop-timer': ['stop', 'memory-shepherd.timer'],
            'disable-timer': ['disable', 'memory-shepherd.timer'],
            'stop-service': ['stop', 'memory-shepherd.service']}
if command == failures.get(os.environ['MANAGER_FAILURE']):
    print('fixture: service manager could not complete the operation', file=sys.stderr)
    raise SystemExit(1)
''')
    manager.chmod(0o700)
    script = Path(__file__).resolve().parents[1] / "memory-shepherd/uninstall.sh"
    env = {"PATH": f"{tools}:/usr/bin:/bin", "HOME": str(root),
           "MANAGER_LOG": str(log), "MANAGER_FAILURE": failure}
    result = subprocess.run(["bash", str(script), "--prefix", str(prefix)],
                            env=env,
                            capture_output=True, text=True, timeout=10)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert all((args[0] == "--user") == user_mode for args in calls)
    if failure:
        assert result.returncode != 0
        assert "service manager could not complete" in result.stderr
        assert "Done. Removed" not in result.stdout
        assert all((prefix / name).read_text() == content for name, content in files.items())
        assert not any("daemon-reload" in args for args in calls)
        recovered = subprocess.run(["bash", str(script), "--prefix", str(prefix)],
                                   env={**env, "MANAGER_FAILURE": ""},
                                   capture_output=True, text=True, timeout=10)
        assert recovered.returncode == 0, recovered.stderr
        assert not (prefix / "memory-shepherd.timer").exists()
        assert not (prefix / "memory-shepherd.service").exists()
    else:
        assert result.returncode == 0, result.stderr
        assert not (prefix / "memory-shepherd.timer").exists()
        assert not (prefix / "memory-shepherd.service").exists()
        assert [args[-2:] for args in calls[:3]] == [
            ["stop", "memory-shepherd.timer"], ["disable", "memory-shepherd.timer"],
            ["stop", "memory-shepherd.service"]]
        assert calls[-1][-1] == "daemon-reload"
        assert "Done. Removed 1 timer(s) and 1 service(s)." in result.stdout
    assert config.read_text() == "operator configuration\n"
    assert (prefix / "unrelated.service").read_text() == files["unrelated.service"]
