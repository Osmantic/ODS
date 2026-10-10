"""Mac extension starts retain the private host-agent Python after cache invalidation.

The macOS LaunchAgent runs a private Python with PyYAML, but its PATH contains
only Homebrew/system tools. A selected extension invalidates .compose-flags,
so the next start must pass that interpreter to the real Bash resolver.
"""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_host_agent import _agent_path, _mod


@pytest.mark.parametrize("inherited_python", [None, "/old/installation/bin/python"])
def test_macos_resolver_uses_agent_python_over_login_environment(
    tmp_path, monkeypatch, inherited_python,
):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "resolve-compose-stack.sh").touch()
    private_python = "/Users/owner/ODS install/.venv/host-agent/bin/python"
    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(_mod, "sys", SimpleNamespace(executable=private_python))
    monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_mod, "_find_usable_bash", lambda: "/bin/bash")
    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    monkeypatch.setenv("DOCKER_HOST", "unix:///Users/owner/.docker/run/docker.sock")
    if inherited_python is None:
        monkeypatch.delenv("ODS_PYTHON_CMD", raising=False)
    else:
        monkeypatch.setenv("ODS_PYTHON_CMD", inherited_python)
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "-f docker-compose.base.yml\n", "")

    monkeypatch.setattr(_mod.subprocess, "run", run)
    assert _mod.resolve_compose_flags() == ["-f", "docker-compose.base.yml"]
    assert calls[0]["ODS_PYTHON_CMD"] == private_python
    assert calls[0]["DOCKER_HOST"] == "unix:///Users/owner/.docker/run/docker.sock"


def test_linux_resolver_retains_existing_python_policy(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "resolve-compose-stack.sh").touch()
    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_mod, "_find_usable_bash", lambda: "/bin/bash")
    monkeypatch.setenv("ODS_PYTHON_CMD", "/usr/bin/python3")
    monkeypatch.setenv("ODS_PYTHON_PREFER_SYSTEM", "1")
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "-f docker-compose.base.yml\n", "")

    monkeypatch.setattr(_mod.subprocess, "run", run)
    assert _mod.resolve_compose_flags() == ["-f", "docker-compose.base.yml"]
    assert calls[0]["ODS_PYTHON_CMD"] == "/usr/bin/python3"
    assert calls[0]["ODS_PYTHON_PREFER_SYSTEM"] == "1"


def _bash_path(path):
    normalized = str(path).replace("\\", "/")
    if len(normalized) > 2 and normalized[1] == ":":
        return "/" + normalized[0].lower() + normalized[2:]
    return normalized


def test_cache_invalidation_resolves_with_private_python_when_path_has_no_yaml(
    tmp_path, monkeypatch,
):
    """Run the production resolver and module detector, with no Docker calls."""
    if os.name == "nt":
        bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        shell_tools = bash.parents[1] / "usr/bin"
        if not bash.is_file():
            pytest.skip("Git Bash is required for the real resolver subprocess")
    else:
        found = shutil.which("bash")
        if found is None:
            pytest.skip("Bash is required for the real resolver subprocess")
        bash = Path(found)
        shell_tools = Path("/usr/bin")

    source = _agent_path.parents[1]
    install = tmp_path / "retained install"
    for relative in ("scripts/resolve-compose-stack.sh", "lib/python-cmd.sh"):
        target = install / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((source / relative).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    (install / "docker-compose.base.yml").write_text("services: {}\n", encoding="utf-8")
    cache = install / ".compose-flags"
    cache.write_text("-f stale-compose.yml\n", encoding="utf-8")

    # The public Python still works, but -S isolates it from installed PyYAML.
    # The private entry point uses the same real interpreter with dependencies.
    public_bin = tmp_path / "system-bin"
    public_bin.mkdir()
    real_python = shlex.quote(_bash_path(sys.executable))
    for name in ("python", "python3"):
        wrapper = public_bin / name
        wrapper.write_text(f"#!/bin/sh\nexec {real_python} -S \"$@\"\n", encoding="utf-8", newline="\n")
        wrapper.chmod(0o755)
    private_python = install / ".venv/host-agent/bin/python"
    private_python.parent.mkdir(parents=True)
    private_python.write_text(f"#!/bin/sh\nexec {real_python} \"$@\"\n", encoding="utf-8", newline="\n")
    private_python.chmod(0o755)

    monkeypatch.setattr(_mod, "INSTALL_DIR", install)
    monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_mod, "_find_usable_bash", lambda: str(bash))
    monkeypatch.setattr(_mod, "_to_bash_path", _bash_path)
    monkeypatch.setattr(_mod, "sys", SimpleNamespace(executable=_bash_path(private_python)))
    monkeypatch.setattr(_mod, "TIER", "4")
    monkeypatch.setattr(_mod, "GPU_BACKEND", "apple")
    monkeypatch.setattr(_mod, "GPU_COUNT", "1")
    monkeypatch.setenv("PATH", _bash_path(public_bin) + ":" + _bash_path(shell_tools) + ":/bin")
    monkeypatch.setenv("ENABLE_OPEN_WEBUI", "true")
    for name in ("ODS_PYTHON_CMD", "ODS_PYTHON_PREFER_SYSTEM", "PYTHONPATH", "LOCALAPPDATA", "USERPROFILE"):
        monkeypatch.delenv(name, raising=False)

    # The identical production resolver fails without the explicit interpreter.
    baseline = subprocess.run(
        [str(bash), _bash_path(install / "scripts/resolve-compose-stack.sh"),
         "--script-dir", _bash_path(install), "--tier", "4", "--gpu-backend", "apple"],
        capture_output=True, text=True, timeout=30,
    )
    assert baseline.returncode == 2
    assert "PyYAML is required" in baseline.stderr

    _mod.invalidate_compose_cache()
    assert not cache.exists()
    assert _mod.resolve_compose_flags() == ["-f", "docker-compose.base.yml"]
