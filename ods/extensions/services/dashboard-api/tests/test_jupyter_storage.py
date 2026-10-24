"""Start the shipped Jupyter recipe with writable persistent notebook mounts."""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest


ODS = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("existing", [False, True])
def test_start_prepares_notebook_mounts_before_compose(tmp_path, monkeypatch, existing):
    install = tmp_path / "install"
    home = tmp_path / "home"
    home.mkdir()
    for name in ("INSTALL_DIR", "ODS_HOME", "ODS_INSTALL_DIR"):
        monkeypatch.setenv(name, str(install))
    monkeypatch.setenv("HOME", str(home))
    spec = importlib.util.spec_from_file_location("jupyter_storage_agent", ODS / "bin/ods-host-agent.py")
    agent = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, agent)
    spec.loader.exec_module(agent)
    installed = install / "data/user-extensions/jupyter"
    shutil.copytree(ODS / "extensions/library/services/jupyter", installed)
    paths = [install / "data/jupyter" / name for name in ("workspace", "notebooks")]
    foreign = tmp_path / "foreign/existing.ipynb"
    foreign.parent.mkdir()
    foreign.write_bytes(b"foreign notebook")
    if existing:
        for path in paths:
            path.mkdir(parents=True)
            (path / "existing.ipynb").write_bytes(b"owner notebook")
    ownership = []
    monkeypatch.setattr(agent, "INSTALL_DIR", install)
    monkeypatch.setattr(agent, "EXTENSIONS_DIR", install / "extensions")
    monkeypatch.setattr(agent, "USER_EXTENSIONS_DIR", install / "data/user-extensions")
    monkeypatch.setattr(agent, "resolve_compose_flags", lambda: ["-f", "fixture-compose.yaml"])
    monkeypatch.setattr(agent, "_fs_type", lambda _path: "ext4")
    monkeypatch.setattr(agent.os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(agent.os, "chown", lambda path, uid, gid: ownership.append((Path(path), uid, gid)), raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected host command")

    monkeypatch.setattr(agent.subprocess, "run", forbidden)
    monkeypatch.setattr(agent.subprocess, "Popen", forbidden)

    def compose(service_id, flags, **_kwargs):
        assert service_id == "jupyter"
        assert flags == ["-f", "fixture-compose.yaml"]
        assert ownership == [(path, 1000, 1000) for path in paths]
        assert all(path.is_dir() for path in paths)
        if existing:
            assert all((path / "existing.ipynb").read_bytes() == b"owner notebook" for path in paths)
        assert foreign.read_bytes() == b"foreign notebook"
        return agent.subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(agent, "_run_selected_extension_up", compose)
    assert agent.docker_compose_action("jupyter", "start") == (True, "")
