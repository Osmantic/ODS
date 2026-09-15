"""Check generated units with systemd's real offline parser."""
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="Requires systemd's unit verifier")
@pytest.mark.parametrize("directory", ["plain", 'ODS "quoted" %H $HOME \\ path'])
@pytest.mark.parametrize("selection", ["relative", "absolute", "default"])
def test_generated_services_preserve_executable_and_selected_config(tmp_path, directory, selection):
    source = Path(__file__).resolve().parents[1] / "memory-shepherd"
    installed = tmp_path / directory
    installed.mkdir()
    shutil.copy2(source / "install.sh", installed)
    worker = installed / "memory-shepherd.sh"
    worker.write_text("#!/bin/sh\nexit 0\n")
    worker.chmod(0o700)
    # The adjacent default deliberately names a different agent. The manager
    # must retain the external selection made during installation.
    adjacent = installed / "memory-shepherd.conf"
    adjacent.write_text("[wrong-agent]\n")
    config = adjacent if selection == "default" else tmp_path / 'selected "config" %H $HOME \\ file.conf'
    config.write_text("[chosen-agent]\n")
    units = tmp_path / "units"
    tools = tmp_path / "bin"
    tools.mkdir()
    manager = tools / "systemctl"
    manager.write_text("#!/bin/sh\nexit 0\n")
    manager.chmod(0o700)
    env = {**os.environ, "PATH": f"{tools}:/usr/bin:/bin"}
    env.pop("MEMORY_SHEPHERD_CONF", None)
    if selection != "default":
        env["MEMORY_SHEPHERD_CONF"] = config.name if selection == "relative" else str(config)
    result = subprocess.run(["bash", str(installed / "install.sh"), "--prefix", str(units)],
                            cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert not (units / "memory-shepherd-wrong-agent.service").exists()
    for name in ("memory-shepherd", "memory-shepherd-chosen-agent"):
        service = units / f"{name}.service"
        checked = subprocess.run(["systemd-analyze", "--generators=no", "--man=no", "verify", str(service)],
                                 capture_output=True, text=True, timeout=10)
        assert checked.returncode == 0, checked.stderr
        # Decode the unit's Environment assignment using its documented C
        # escapes and literal-percent rule. No shell evaluation is involved.
        setting = next((line.removeprefix("Environment=") for line in service.read_text().splitlines()
                        if line.startswith("Environment=")), None)
        assert setting is not None, "The service lost the selected configuration"
        assert json.loads(setting).replace("%%", "%") == f"MEMORY_SHEPHERD_CONF={config}"
        invocation = next(line.removeprefix("ExecStart=") for line in service.read_text().splitlines()
                          if line.startswith("ExecStart="))
        assert shlex.split(invocation.replace("%%", "%")) == [
            ":/bin/bash", str(worker), "all" if name == "memory-shepherd" else "chosen-agent"]
