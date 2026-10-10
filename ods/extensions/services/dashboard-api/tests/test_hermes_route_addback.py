"""Hermes Library add-back must follow the selected ODS model route."""

import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml


ODS_ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "ods_host_agent_hermes_addback", ODS_ROOT / "bin" / "ods-host-agent.py",
)
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def write_install(tmp_path, env, *, live=None):
    template = tmp_path / "extensions/services/hermes/cli-config.yaml.template"
    template.parent.mkdir(parents=True)
    template.write_text(
        'model:\n  default: "template-model"\n  provider: "custom"\n'
        '  base_url: "http://llama-server:8080/v1"\n'
        '  context_length: 131072\nother:\n  owner_value: retained\n',
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("\n".join(f"{k}={json.dumps(v)}" for k, v in env.items()) + "\n")
    live_path = tmp_path / "data/hermes/config.yaml"
    if live is not None:
        live_path.parent.mkdir(parents=True)
        live_path.write_text(live, encoding="utf-8")
    return template, live_path


def external_env(**overrides):
    env = {
        "LLM_BACKEND": "external",
        "ODS_MODEL_SWITCHBOARD": "observe",
        "EXTERNAL_LLM_MODEL": "provider/model-a",
        "HERMES_LLM_BASE_URL": "http://litellm:4000/v1",
        "HERMES_LLM_API_KEY": 'private-"key\\value',
        "MAX_CONTEXT": "65536",
    }
    env.update(overrides)
    return env


def test_external_addback_patches_template_before_first_start(tmp_path, monkeypatch, caplog):
    template, live_path = write_install(tmp_path, external_env())
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)

    assert agent._prepare_hermes_route_for_start() == (True, "")
    model = yaml.safe_load(template.read_text(encoding="utf-8"))["model"]
    assert model["default"] == "provider/model-a"
    assert model["base_url"] == "http://litellm:4000/v1"
    assert "api_key" not in model
    assert model["context_length"] == 65536
    assert live_path.is_file()
    private_model = yaml.safe_load(live_path.read_text(encoding="utf-8"))["model"]
    assert private_model["api_key"] == 'private-"key\\value'
    if os.name != "nt":
        assert stat.S_IMODE(live_path.stat().st_mode) == 0o600
    assert 'private-"key\\value' not in caplog.text
    first = template.read_bytes(), live_path.read_bytes()
    assert agent._prepare_hermes_route_for_start() == (True, "")
    assert (template.read_bytes(), live_path.read_bytes()) == first


def test_external_addback_updates_route_but_retains_owner_state(tmp_path, monkeypatch):
    template, live_path = write_install(
        tmp_path, external_env(),
        live='model:\n  default: "old-model"\n  provider: "custom"\n'
             '  base_url: "http://llama-server:8080/v1"\n'
             '  context_length: 131072\nother:\n  owner_value: retained\n',
    )
    sentinel = live_path.parent / "sessions" / "owner.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)

    assert agent._prepare_hermes_route_for_start() == (True, "")
    config = yaml.safe_load(live_path.read_text(encoding="utf-8"))
    assert config["model"]["default"] == "provider/model-a"
    assert config["model"]["base_url"] == "http://litellm:4000/v1"
    assert config["model"]["api_key"] == 'private-"key\\value'
    assert config["other"]["owner_value"] == "retained"
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert yaml.safe_load(template.read_text(encoding="utf-8"))["model"]["base_url"] == "http://litellm:4000/v1"
    assert "api_key" not in yaml.safe_load(template.read_text(encoding="utf-8"))["model"]


def test_local_addback_patches_template_and_preserves_existing_live_config(tmp_path, monkeypatch):
    template, live_path = write_install(
        tmp_path,
        {"LLM_BACKEND": "llama-server", "ODS_MODEL_SWITCHBOARD": "enabled",
         "GGUF_FILE": "local.gguf", "HERMES_LLM_BASE_URL": "http://model-router:9099/v1",
         "HERMES_LLM_API_KEY": "no-key", "MAX_CONTEXT": "131072"},
        live='model:\n  default: "owner-model"\n  base_url: "http://owner:8080/v1"\n',
    )
    before = live_path.read_bytes()
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)

    assert agent._prepare_hermes_route_for_start() == (True, "")
    assert live_path.read_bytes() == before
    model = yaml.safe_load(template.read_text(encoding="utf-8"))["model"]
    assert model["default"] == "ods/current"
    assert model["base_url"] == "http://model-router:9099/v1"
    assert "api_key" not in model


def test_external_addback_fails_before_start_without_gateway_key(tmp_path, monkeypatch):
    template, _ = write_install(tmp_path, external_env(HERMES_LLM_API_KEY=""))
    before = template.read_bytes()
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    assert agent._prepare_hermes_route_for_start() == (False, "Hermes external gateway key is missing")
    assert template.read_bytes() == before


def test_external_model_identity_wins_even_if_switchboard_setting_is_stale():
    assert agent._hermes_selected_model(external_env(ODS_MODEL_SWITCHBOARD="enabled")) == "provider/model-a"


def test_quoted_api_key_is_replaced_without_duplicate_and_ambiguous_owner_file_fails(tmp_path, monkeypatch):
    _, live_path = write_install(
        tmp_path, external_env(),
        live='model:\n  default: "old"\n  "api_key" : "old-secret"\n',
    )
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    assert agent._prepare_hermes_route_for_start() == (True, "")
    assert yaml.safe_load(live_path.read_text(encoding="utf-8"))["model"]["api_key"] == 'private-"key\\value'
    assert live_path.read_text(encoding="utf-8").count("api_key:") == 1

    live_path.write_text('model:\n  api_key: "first"\n  "api_key": "second"\n', encoding="utf-8")
    before = live_path.read_bytes()
    assert agent._prepare_hermes_route_for_start()[0] is False
    assert live_path.read_bytes() == before


def test_retained_four_space_api_key_replaced_at_model_level(tmp_path, monkeypatch, caplog):
    _, live_path = write_install(
        tmp_path, external_env(),
        live='model:\n    default: "old"\n    api_key: "stale-secret"\n'
             '    owner_nested:\n        api_key: "nested-owner-key"\n'
             'other:\n  owner_value: retained\n',
    )
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)

    assert agent._prepare_hermes_route_for_start() == (True, "")
    text = live_path.read_text(encoding="utf-8")
    config = yaml.safe_load(text)
    assert config["model"]["api_key"] == 'private-"key\\value'
    assert config["model"]["owner_nested"]["api_key"] == "nested-owner-key"
    assert config["other"]["owner_value"] == "retained"
    assert sum(line.startswith('    api_key: ') for line in text.splitlines()) == 1
    assert "stale-secret" not in text
    assert "stale-secret" not in caplog.text
    assert 'private-"key\\value' not in caplog.text

    live_path.write_text(
        'model:\n    api_key: "first"\n    "api_key": "second"\n',
        encoding="utf-8",
    )
    before = live_path.read_bytes()
    assert agent._prepare_hermes_route_for_start()[0] is False
    assert live_path.read_bytes() == before
    assert "first" not in caplog.text
    assert "second" not in caplog.text


def test_nested_owner_fields_are_not_treated_as_model_route(tmp_path, monkeypatch):
    _, live_path = write_install(
        tmp_path, external_env(),
        live='model:\n    owner_nested:\n'
             '        default: "owner-model"\n'
             '        base_url: "http://owner/v1"\n'
             '        context_length: 123\n'
             '        max_tokens: 77\n'
             'auxiliary:\n  compression:\n'
             '    context_length: 131072\n'
             '    owner_nested:\n      context_length: 42\n'
             'other:\n  context_length: 999\n',
    )
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)

    assert agent._prepare_hermes_route_for_start() == (True, "")
    config = yaml.safe_load(live_path.read_text(encoding="utf-8"))
    model = config["model"]
    assert model["default"] == "provider/model-a"
    assert model["base_url"] == "http://litellm:4000/v1"
    assert model["api_key"] == 'private-"key\\value'
    assert model["context_length"] == 65536
    assert model["max_tokens"] == 1024
    assert model["owner_nested"] == {
        "default": "owner-model",
        "base_url": "http://owner/v1",
        "context_length": 123,
        "max_tokens": 77,
    }
    assert config["auxiliary"]["compression"]["context_length"] == 65536
    assert config["auxiliary"]["compression"]["owner_nested"]["context_length"] == 42
    assert config["other"]["context_length"] == 999


def test_quoted_retained_route_keys_are_replaced_without_duplicates(tmp_path, monkeypatch):
    _, live_path = write_install(
        tmp_path, external_env(),
        live='"model":\n'
             '    "default": "old-model"\n'
             "    'base_url': 'http://old/v1'\n"
             '    "api_key": "old-secret"\n'
             "    'context_length': 131072\n"
             '    "max_tokens": 777\n'
             '"auxiliary":\n  "compression":\n    "context_length": 131072\n',
    )
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)

    assert agent._prepare_hermes_route_for_start() == (True, "")
    text = live_path.read_text(encoding="utf-8")
    config = yaml.safe_load(text)
    assert config["model"] == {
        "default": "provider/model-a",
        "base_url": "http://litellm:4000/v1",
        "api_key": 'private-"key\\value',
        "context_length": 65536,
        "max_tokens": 777,
    }
    assert config["auxiliary"]["compression"]["context_length"] == 65536
    assert "old-secret" not in text
    model_text = text.split('"auxiliary":', 1)[0]
    direct_fields = [
        line[4:].split(":", 1)[0].strip().strip("'\"")
        for line in model_text.splitlines()
        if line.startswith("    ") and not line.startswith("     ")
    ]
    for field in ("default", "base_url", "api_key", "context_length", "max_tokens"):
        assert direct_fields.count(field) == 1


def test_matching_retained_route_symlink_fails_closed(tmp_path, monkeypatch):
    template, live_path = write_install(
        tmp_path, external_env(),
        live='model:\n  default: "old"\n  base_url: "http://old/v1"\n',
    )
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    assert agent._prepare_hermes_route_for_start() == (True, "")

    owner_target = tmp_path / "owner-config.yaml"
    live_path.rename(owner_target)
    before_target = owner_target.read_bytes()
    before_template = template.read_bytes()
    try:
        live_path.symlink_to(owner_target)
    except (OSError, NotImplementedError):
        pytest.skip("This host cannot create a file symlink")
    assert agent._prepare_hermes_route_for_start() == (
        False, "Hermes route config path is not a regular file"
    )
    assert owner_target.read_bytes() == before_target
    assert template.read_bytes() == before_template


def test_nonregular_retained_route_fails_closed(tmp_path, monkeypatch):
    template, live_path = write_install(
        tmp_path, external_env(),
        live='model:\n  default: "old"\n  base_url: "http://old/v1"\n',
    )
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    before_template = template.read_bytes()
    live_path.unlink()
    live_path.mkdir()
    assert agent._prepare_hermes_route_for_start() == (
        False, "Hermes route config path is not a regular file"
    )
    assert live_path.is_dir()
    assert template.read_bytes() == before_template


def test_external_compose_plan_refuses_stale_local_overlay(tmp_path, monkeypatch):
    write_install(tmp_path, external_env())
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    assert agent._hermes_compose_plan_error(["-f", "extensions/services/hermes/compose.local.yaml"])
    assert agent._hermes_compose_plan_error(["-f", "extensions/services/hermes/compose.yaml"]) == ""


@pytest.mark.parametrize("refresh_ok", [True, False])
def test_hermes_start_prepares_route_before_compose_up(monkeypatch, refresh_ok):
    order = []

    def record(name, result):
        order.append(name)
        return result

    monkeypatch.setattr(agent, "resolve_compose_flags", lambda: [])
    monkeypatch.setattr(agent, "_hermes_compose_plan_error", lambda flags: "")
    monkeypatch.setattr(agent, "_prepare_hermes_route_for_start", lambda: record("route", (True, "")))
    monkeypatch.setattr(agent, "_prepare_hermes_persona_for_start", lambda: record("persona", (True, "")))
    refresh_result = (refresh_ok, "" if refresh_ok else "Persona refresh failed")
    monkeypatch.setattr(agent, "_refresh_running_hermes_persona", lambda: record("refresh", refresh_result))
    monkeypatch.setattr(agent, "_precreate_data_dirs", lambda service: None)
    monkeypatch.setattr(agent, "_repair_rootless_data_ownership", lambda service: None)
    monkeypatch.setattr(agent, "_find_ext_dir", lambda service: None)
    monkeypatch.setattr(agent, "_run_selected_extension_up", lambda service, flags, **kwargs: record(
        "compose", subprocess.CompletedProcess([], 0, "", "")))
    monkeypatch.setattr(agent.subprocess, "run", lambda command, **kwargs: (
        pytest.fail(f"Unexpected subprocess outside selected start: {command}")))

    assert agent.docker_compose_action("hermes", "start") == refresh_result
    assert order == ["route", "persona", "compose", "refresh"]


def test_hermes_persona_repairs_empty_mount_directory_without_deleting_owner_data(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    builder = tmp_path / "scripts/build-installation-context.py"
    builder.parent.mkdir(parents=True)
    builder.write_text("# test builder\n", encoding="utf-8")
    template = tmp_path / "extensions/services/hermes/SOUL.md.template"
    template.parent.mkdir(parents=True)
    template.write_text("Hermes persona\n", encoding="utf-8")
    (tmp_path / ".env").write_text("LLM_BACKEND=external\n", encoding="utf-8")
    output = tmp_path / "data/persona/SOUL.md"
    output.mkdir(parents=True)
    calls = []

    def render(cmd, **kwargs):
        calls.append(cmd)
        assert cmd[cmd.index("--template") + 1] == str(template)
        assert cmd[cmd.index("--output") + 1] == str(output)
        output.write_text("Hermes persona\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "changed", "")

    monkeypatch.setattr(agent.subprocess, "run", render)
    assert agent._prepare_hermes_persona_for_start() == (True, "")
    assert output.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o644
    assert agent._prepare_hermes_persona_for_start() == (True, "")
    assert len(calls) == 1

    output.unlink()
    output.mkdir()
    (output / "owner.txt").write_text("keep", encoding="utf-8")
    assert agent._prepare_hermes_persona_for_start()[0] is False
    assert (output / "owner.txt").read_text(encoding="utf-8") == "keep"


SHIPPED_TEMPLATE = ODS_ROOT / "extensions/services/hermes/cli-config.yaml.template"
INSTALLER_SPEC = importlib.util.spec_from_file_location(
    "ods_patch_hermes_config_addback", ODS_ROOT / "scripts" / "patch-hermes-config.py",
)
installer_patcher = importlib.util.module_from_spec(INSTALLER_SPEC)
INSTALLER_SPEC.loader.exec_module(installer_patcher)
# The switchboard route that both phase 11 and a Library start select.
ROUTE = ("ods/current", "http://model-router:9099/v1", 65536)


def shipped_install(tmp_path):
    template = tmp_path / "extensions/services/hermes/cli-config.yaml.template"
    template.parent.mkdir(parents=True)
    template.write_bytes(SHIPPED_TEMPLATE.read_bytes())
    model, base_url, context = ROUTE
    env = {"LLM_BACKEND": "llama-server", "ODS_MODEL_SWITCHBOARD": "enabled",
           "HERMES_LLM_BASE_URL": base_url, "HERMES_LLM_API_KEY": "no-key",
           "MAX_CONTEXT": str(context)}
    (tmp_path / ".env").write_text("\n".join(f"{k}={json.dumps(v)}" for k, v in env.items()) + "\n")
    return template


def installer_output(path):
    # The installer runs on Linux and WSL. Windows text mode would write
    # CRLF, so normalize only there; the CI comparison stays exact.
    raw = path.read_bytes()
    return raw.replace(b"\r\n", b"\n") if os.name == "nt" else raw


def test_shipped_template_keeps_comments_blank_lines_and_one_context_per_key():
    original = SHIPPED_TEMPLATE.read_text(encoding="utf-8")
    model, base_url, context = ROUTE
    patched, changed = agent._patch_hermes_config_text(
        original, model, base_url=base_url, context_length=context)

    def layout(text):
        return [line for line in text.splitlines() if not line.strip() or line.lstrip().startswith("#")]

    assert changed
    assert layout(patched) == layout(original)
    assert not [line for line in patched.splitlines() if line.startswith("context_length:")]
    config = yaml.safe_load(patched)
    assert "context_length" not in config
    assert config["model"]["context_length"] == context
    assert config["auxiliary"]["compression"]["context_length"] == context
    assert agent._patch_hermes_config_text(
        patched, model, base_url=base_url, context_length=context) == (patched, False)


def test_agent_and_installer_write_identical_template_bytes(tmp_path, monkeypatch):
    template = shipped_install(tmp_path)
    installer_copy = tmp_path / "installer-template.yaml"
    installer_copy.write_bytes(SHIPPED_TEMPLATE.read_bytes())
    model, base_url, context = ROUTE
    assert installer_patcher.patch_config(installer_copy, model, base_url, context) is True
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)

    assert agent._prepare_hermes_route_for_start() == (True, "")
    assert template.read_bytes() == installer_output(installer_copy)


def test_library_start_after_installer_update_does_not_write_the_template(tmp_path, monkeypatch):
    template = shipped_install(tmp_path)
    model, base_url, context = ROUTE
    # Phase 11's switchboard route also sets the gateway key and timeout.
    assert installer_patcher.patch_config(template, model, base_url, context, "no-key", 900) is True
    template.write_bytes(installer_output(template))
    content, before = template.read_bytes(), template.stat()
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)

    assert agent._prepare_hermes_route_for_start() == (True, "")
    after = template.stat()
    assert template.read_bytes() == content
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def bound_view_of(path, tmp_path):
    """A second name for the file's inode, as a container's bind holds it."""
    view = tmp_path / "bound-view"
    try:
        os.link(path, view)
    except (OSError, NotImplementedError):
        pytest.skip("This host cannot create a hard link")
    return view


def test_library_start_rewrites_the_template_in_place(tmp_path, monkeypatch):
    template = shipped_install(tmp_path)
    view = bound_view_of(template, tmp_path)
    inode = template.stat().st_ino
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)

    assert agent._prepare_hermes_route_for_start() == (True, "")
    assert template.stat().st_ino == inode
    assert view.read_bytes() == template.read_bytes()
    assert yaml.safe_load(view.read_text(encoding="utf-8"))["model"]["default"] == "ods/current"


def test_model_activation_patches_the_template_in_place(tmp_path):
    template = tmp_path / "cli-config.yaml.template"
    template.write_bytes(SHIPPED_TEMPLATE.read_bytes())
    view = bound_view_of(template, tmp_path)
    inode = template.stat().st_ino

    assert agent._patch_hermes_model_config(
        template, "new.gguf", base_url="http://llama-server:8080/v1", context_length=32768) is True
    assert template.stat().st_ino == inode
    assert yaml.safe_load(view.read_text(encoding="utf-8"))["model"]["default"] == "new.gguf"


@pytest.mark.skipif(os.name == "nt", reason="Windows files have no POSIX owner to check")
def test_library_start_refuses_a_template_the_install_owner_does_not_own(tmp_path, monkeypatch):
    template = shipped_install(tmp_path)
    before = template.read_bytes()
    other_uid = template.stat().st_uid + 1
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(agent.os, "geteuid", lambda: other_uid)

    assert agent._prepare_hermes_route_for_start() == (
        False,
        "Refused to update the Hermes configuration template: "
        "cli-config.yaml.template is not owned by the ODS install owner",
    )
    assert template.read_bytes() == before


def test_bound_file_writer_refuses_a_symlink(tmp_path):
    target = tmp_path / "owner-file.yaml"
    target.write_bytes(b"owner: data\n")
    link = tmp_path / "cli-config.yaml.template"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("This host cannot create a file symlink")
    with pytest.raises(agent.BindSourceRefused, match="is not a regular file"):
        agent._write_bound_file_in_place(link, b"replaced\n")
    assert target.read_bytes() == b"owner: data\n"


def test_hermes_external_plan_keeps_search_without_managed_llama():
    service_dir = ODS_ROOT / "extensions/services/hermes"
    manifest = yaml.safe_load((service_dir / "manifest.yaml").read_text(encoding="utf-8"))
    catalog = json.loads((ODS_ROOT / "config/extensions-catalog.json").read_text(encoding="utf-8"))
    entry = next(item for item in catalog["extensions"] if item["id"] == "hermes")
    assert manifest["service"]["depends_on"] == entry["depends_on"] == ["searxng"]
    overlay = yaml.safe_load((service_dir / "compose.local.yaml").read_text(encoding="utf-8"))
    assert overlay["services"]["hermes"]["depends_on"]["llama-server"] == {
        "condition": "service_healthy",
    }
