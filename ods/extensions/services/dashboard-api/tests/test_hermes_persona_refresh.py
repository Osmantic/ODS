"""Library start refreshes generated context without replacing owner personas."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fcntl", reason="The copy component runs inside the Linux Hermes container")
ODS_ROOT = Path(__file__).resolve().parents[4]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent = load("ods_persona_refresh_agent", ODS_ROOT / "bin/ods-host-agent.py")
builder = load("ods_persona_refresh_builder", ODS_ROOT / "scripts/build-installation-context.py")
copier = load("ods_persona_refresh_copier", ODS_ROOT / "scripts/sync-hermes-persona.py")


@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(agent, "_runtime_uses_router_transport", lambda env: False)
    monkeypatch.setattr(agent, "_is_windows_host_llama_server", lambda env: False)
    services = {"model-router"}
    monkeypatch.setattr(builder, "_running_services", lambda root: set(services))
    monkeypatch.setattr(builder, "_loaded_model", lambda *args, **kwargs: "fixture-9b")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "build-installation-context.py").write_text("# staged builder")
    (scripts / "sync-hermes-persona.py").write_text("# container copy component")
    template = tmp_path / "extensions/services/hermes/SOUL.md.template"
    template.parent.mkdir(parents=True)
    template.write_text("Owner template text\n<!-- INSTALLATION_CONTEXT -->\n")
    env = tmp_path / ".env"
    env.write_text("LLM_BACKEND=llama-server\nODS_UID=10000\nODS_GID=10000\n")
    source = tmp_path / "data/persona/SOUL.md"
    builder.build_soul(template, env, source)
    runtime = tmp_path / "runtime-SOUL.md"
    runtime.write_bytes(source.read_bytes())
    calls = []
    fail_copy = [False]

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "docker":
            assert cmd[:7] == ["docker", "exec", "-i", "--user", "10000:10000", "ods-hermes", "python3"]
            if fail_copy[0]:
                return subprocess.CompletedProcess(cmd, 1, "", "fixture failure")
            payload = json.loads(kwargs["input"])
            status = copier.sync_persona(str(runtime), **payload)
            return subprocess.CompletedProcess(cmd, 0, status + "\n", "")
        assert Path(cmd[1]).name == "build-installation-context.py"
        staged = Path(cmd[cmd.index("--output") + 1])
        profile = cmd[cmd.index("--profile") + 1] if "--profile" in cmd else "full"
        builder.build_soul(template, env, staged, profile=profile)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(agent.subprocess, "run", run)
    return source, runtime, template, services, calls, fail_copy


def test_generated_persona_refreshes_after_extensions_are_enabled(install):
    source, runtime, template, services, calls, _ = install
    source_inode, runtime_inode = source.stat().st_ino, runtime.stat().st_ino
    template_bytes = template.read_bytes()
    services.update({"open-webui", "perplexica", "searxng"})
    assert agent._refresh_running_hermes_persona() == (True, "")
    assert runtime.read_bytes() == source.read_bytes()
    assert all(label in runtime.read_text() for label in ("Open WebUI", "Perplexica", "SearXNG"))
    assert (source.stat().st_ino, runtime.stat().st_ino) == (source_inode, runtime_inode)
    assert template.read_bytes() == template_bytes
    source_time, runtime_time = source.stat().st_mtime_ns, runtime.stat().st_mtime_ns
    assert agent._refresh_running_hermes_persona() == (True, "")
    assert (source.stat().st_mtime_ns, runtime.stat().st_mtime_ns) == (source_time, runtime_time)


def test_custom_runtime_persona_is_preserved(install):
    source, runtime, _, services, _, _ = install
    runtime.write_text("Owner's custom persona\n")
    before = runtime.stat().st_mtime_ns
    services.add("searxng")
    assert agent._refresh_running_hermes_persona() == (True, "")
    assert runtime.read_text() == "Owner's custom persona\n"
    assert runtime.stat().st_mtime_ns == before
    assert "SearXNG" in source.read_text()


def test_failed_copy_keeps_old_source_for_successful_retry(install):
    source, runtime, _, services, _, fail_copy = install
    previous = source.read_bytes()
    services.add("searxng")
    fail_copy[0] = True
    assert agent._refresh_running_hermes_persona()[0] is False
    assert source.read_bytes() == runtime.read_bytes() == previous
    fail_copy[0] = False
    assert agent._refresh_running_hermes_persona() == (True, "")
    assert source.read_bytes() == runtime.read_bytes()
    assert "SearXNG" in runtime.read_text()


def test_missing_builder_refuses_refresh_without_changing_files(install):
    source, runtime, _, _, calls, _ = install
    before = source.read_bytes()
    (agent.INSTALL_DIR / "scripts/build-installation-context.py").unlink()
    assert agent._refresh_running_hermes_persona()[0] is False
    assert source.read_bytes() == runtime.read_bytes() == before
    assert not calls


def test_source_edit_during_container_copy_is_preserved(install, monkeypatch):
    source, runtime, _, services, _, _ = install
    services.add("searxng")
    original_sync = copier.sync_persona

    def concurrent_edit(*args, **kwargs):
        result = original_sync(*args, **kwargs)
        source.write_text("Owner changed generated source during refresh")
        return result

    monkeypatch.setattr(copier, "sync_persona", concurrent_edit)
    assert agent._refresh_running_hermes_persona()[0] is False
    assert source.read_text() == "Owner changed generated source during refresh"
    assert "SearXNG" in runtime.read_text()
