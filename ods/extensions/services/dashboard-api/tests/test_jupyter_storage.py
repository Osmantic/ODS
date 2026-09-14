"""The shipped Jupyter manifest must prepare writable notebook mounts."""

import importlib.util
import sys
from pathlib import Path

import pytest

ODS = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("jupyter_storage_agent", ODS / "bin/ods-host-agent.py")
agent = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = agent
spec.loader.exec_module(agent)


@pytest.mark.parametrize("existing", [False, True])
def test_start_prepares_shipped_jupyter_mounts_before_compose(tmp_path, monkeypatch, existing):
    import shutil

    installed = tmp_path / "extensions/jupyter"
    shutil.copytree(ODS / "extensions/library/services/jupyter", installed)
    paths = [tmp_path / "data/jupyter" / name for name in ("workspace", "notebooks")]
    if existing:
        for path in paths:
            path.mkdir(parents=True)
            (path / "existing.ipynb").write_text("preserve notebook", encoding="utf-8")
    ownership = []
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(agent, "EXTENSIONS_DIR", tmp_path / "extensions")
    monkeypatch.setattr(agent, "USER_EXTENSIONS_DIR", tmp_path / "user-extensions")
    monkeypatch.setattr(agent, "resolve_compose_flags", lambda: ["-f", "fixture-compose.yaml"])
    monkeypatch.setattr(agent, "_fs_type", lambda _path: "ext4")
    monkeypatch.setattr(agent.os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(agent.os, "chown", lambda path, uid, gid: ownership.append((Path(path), uid)), raising=False)

    def compose(cmd, **_kwargs):
        assert cmd == ["docker", "compose", "-f", "fixture-compose.yaml", "up", "-d", "jupyter"]
        assert ownership == [(path, 1000) for path in paths]
        assert all(path.is_dir() for path in paths)
        if existing:
            assert all((path / "existing.ipynb").read_text() == "preserve notebook" for path in paths)
        return agent.subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(agent.subprocess, "run", compose)
    assert agent.docker_compose_action("jupyter", "start") == (True, "")
