"""Security-focused tests for the Settings environment editor."""

import json

import pytest


@pytest.fixture()
def settings_env_fixture(tmp_path, monkeypatch):
    install_root = tmp_path / "ods"
    install_root.mkdir()
    data_root = tmp_path / "data"
    data_root.mkdir()

    env_path = install_root / ".env"
    example_path = install_root / ".env.example"
    schema_path = install_root / ".env.schema.json"

    env_path.write_text(
        "OPENAI_API_KEY=sk-live-secret\n"
        "LLM_BACKEND=local\n"
        "WEBUI_AUTH=true\n",
        encoding="utf-8",
    )

    example_path.write_text(
        "# ════════════════════════════════\n"
        "# LLM Settings\n"
        "# ════════════════════════════════\n"
        "OPENAI_API_KEY=\n"
        "LLM_BACKEND=local\n"
        "WEBUI_AUTH=true\n"
        "# LLAMA_ARG_N_CPU_MOE=25\n",
        encoding="utf-8",
    )

    schema_path.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "OPENAI_API_KEY": {
                        "type": "string",
                        "description": "Key used for cloud LLM providers.",
                        "secret": True,
                    },
                    "LLM_BACKEND": {
                        "type": "string",
                        "description": "Primary LLM backend mode.",
                        "enum": ["local", "cloud"],
                        "default": "local",
                    },
                    "WEBUI_AUTH": {
                        "type": "boolean",
                        "description": "Require login for the WebUI.",
                        "default": True,
                    },
                    "LLAMA_ARG_N_CPU_MOE": {
                        "type": "integer",
                        "description": "Optional llama.cpp MoE tuning knob.",
                        "minimum": 0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("main._resolve_install_root", lambda: install_root)
    monkeypatch.setattr("main._resolve_runtime_env_path", lambda: env_path)
    monkeypatch.setattr("main.DATA_DIR", str(data_root))
    monkeypatch.setattr(
        "settings.request_agent_json",
        lambda method, path, *, timeout: {"status": "ok"},
    )

    def fake_env_update(raw_text):
        backup_dir = data_root / "config-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / ".env.backup.test"
        if env_path.exists():
            backup_path.write_bytes(env_path.read_bytes())
        payload = raw_text if raw_text.endswith("\n") else raw_text + "\n"
        env_path.write_text(payload, encoding="utf-8")
        return {"backup_path": "data/config-backups/.env.backup.test"}

    monkeypatch.setattr("main._call_agent_env_update", fake_env_update)

    def fake_resolve_template(name: str):
        if name == ".env.example":
            return example_path
        if name == ".env.schema.json":
            return schema_path
        return install_root / name

    monkeypatch.setattr("main._resolve_template_path", fake_resolve_template)

    from main import _cache

    _cache.clear()

    return {
        "install_root": install_root,
        "data_root": data_root,
        "env_path": env_path,
    }


def test_api_settings_env_masks_secret_values(test_client, settings_env_fixture):
    response = test_client.get("/api/settings/env", headers=test_client.auth_headers)

    assert response.status_code == 200
    payload = response.json()

    assert payload["path"] == ".env"
    assert payload["raw"] == ""
    assert payload["values"]["OPENAI_API_KEY"] == ""
    assert payload["fields"]["OPENAI_API_KEY"]["value"] == ""
    assert payload["fields"]["OPENAI_API_KEY"]["hasValue"] is True
    assert payload["fields"]["OPENAI_API_KEY"]["secret"] is True
    assert payload["values"]["LLM_BACKEND"] == "local"
    assert payload["fields"]["LLM_BACKEND"]["value"] == "local"
    assert payload["agentAvailable"] is True


def test_api_settings_env_marks_runtime_mode_read_only(test_client, settings_env_fixture):
    env_path = settings_env_fixture["env_path"]
    env_path.write_text(
        env_path.read_text(encoding="utf-8") + "ODS_MODE=local\n",
        encoding="utf-8",
    )

    response = test_client.get("/api/settings/env", headers=test_client.auth_headers)

    assert response.status_code == 200
    field = response.json()["fields"]["ODS_MODE"]
    assert field["readOnly"] is True
    assert "installer" in field["readOnlyReason"].lower()


def test_api_settings_env_rejects_runtime_mode_change(test_client, settings_env_fixture):
    env_path = settings_env_fixture["env_path"]
    env_path.write_text(
        env_path.read_text(encoding="utf-8") + "ODS_MODE=cloud\n",
        encoding="utf-8",
    )

    response = test_client.put(
        "/api/settings/env",
        headers=test_client.auth_headers,
        json={"mode": "form", "values": {"ODS_MODE": "local"}},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["issues"] == [{
        "key": "ODS_MODE",
        "message": "Runtime mode is selected by the installer and cannot be changed from the dashboard.",
    }]
    assert "ODS_MODE=cloud" in env_path.read_text(encoding="utf-8")


def test_api_settings_env_allows_unchanged_runtime_mode(test_client, settings_env_fixture):
    env_path = settings_env_fixture["env_path"]
    env_path.write_text(
        env_path.read_text(encoding="utf-8") + "ODS_MODE=local\n",
        encoding="utf-8",
    )

    response = test_client.put(
        "/api/settings/env",
        headers=test_client.auth_headers,
        json={
            "mode": "form",
            "values": {"ODS_MODE": "local", "WEBUI_AUTH": "false"},
        },
    )

    assert response.status_code == 200
    updated_env = env_path.read_text(encoding="utf-8")
    assert "ODS_MODE=local" in updated_env
    assert "WEBUI_AUTH=false" in updated_env


def test_api_settings_env_preserves_existing_secret_when_blank(test_client, settings_env_fixture):
    env_path = settings_env_fixture["env_path"]

    response = test_client.put(
        "/api/settings/env",
        headers=test_client.auth_headers,
        json={
            "mode": "form",
            "values": {
                "OPENAI_API_KEY": "",
                "LLM_BACKEND": "cloud",
                "WEBUI_AUTH": "false",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    updated_env = env_path.read_text(encoding="utf-8")

    assert "OPENAI_API_KEY=sk-live-secret" in updated_env
    assert "LLM_BACKEND=cloud" in updated_env
    assert "WEBUI_AUTH=false" in updated_env
    assert payload["values"]["OPENAI_API_KEY"] == ""
    assert payload["fields"]["OPENAI_API_KEY"]["hasValue"] is True
    assert payload["backupPath"].startswith("data/config-backups/.env.backup.")
    assert payload["applyPlan"]["status"] == "ready"
    assert payload["applyPlan"]["services"] == ["llama-server", "open-webui"]


def test_api_settings_env_rejects_raw_mode(test_client, settings_env_fixture):
    response = test_client.put(
        "/api/settings/env",
        headers=test_client.auth_headers,
        json={"mode": "raw", "raw": "OPENAI_API_KEY=oops\n"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["detail"]["message"] == "Only form-based editing is supported for security reasons."


def test_api_settings_env_rejects_model_identity_bypass(
    test_client, settings_env_fixture,
):
    env_path = settings_env_fixture["env_path"]
    env_path.write_text(
        env_path.read_text(encoding="utf-8") + "LLM_MODEL=old-model\n",
        encoding="utf-8",
    )

    response = test_client.put(
        "/api/settings/env",
        headers=test_client.auth_headers,
        json={"mode": "form", "values": {"LLM_MODEL": "new-model"}},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["issues"] == [{
        "key": "LLM_MODEL",
        "message": "The active model is managed by Model Manager so model consumers stay synchronized.",
    }]
    assert "LLM_MODEL=old-model" in env_path.read_text(encoding="utf-8")


def test_api_settings_env_rejects_new_unknown_keys(test_client, settings_env_fixture):
    response = test_client.put(
        "/api/settings/env",
        headers=test_client.auth_headers,
        json={
            "mode": "form",
            "values": {
                "OPENAI_API_KEY": "",
                "INJECTED_FLAG": "true",
            },
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["detail"]["message"] == "Configuration validation failed."
    assert payload["detail"]["issues"] == [
        {
            "key": "INJECTED_FLAG",
            "message": "Field is not editable from the dashboard. Only schema-backed fields and existing local overrides can be changed here.",
        }
    ]


def test_api_settings_env_allows_existing_local_override(test_client, settings_env_fixture):
    env_path = settings_env_fixture["env_path"]
    env_path.write_text(
        env_path.read_text(encoding="utf-8") + "LOCAL_OVERRIDE=keep-me\n",
        encoding="utf-8",
    )

    response = test_client.put(
        "/api/settings/env",
        headers=test_client.auth_headers,
        json={
            "mode": "form",
            "values": {
                "LOCAL_OVERRIDE": "updated",
            },
        },
    )

    assert response.status_code == 200
    updated_env = env_path.read_text(encoding="utf-8")
    assert "LOCAL_OVERRIDE=updated" in updated_env


def test_api_settings_env_rejects_newline_in_value(test_client, settings_env_fixture):
    response = test_client.put(
        "/api/settings/env",
        headers=test_client.auth_headers,
        json={
            "mode": "form",
            "values": {
                "LLM_BACKEND": "local\nINJECTED_KEY=malicious",
            },
        },
    )

    assert response.status_code == 400
    assert "invalid characters" in response.json()["detail"]


def test_api_settings_env_rejects_null_byte_in_value(test_client, settings_env_fixture):
    response = test_client.put(
        "/api/settings/env",
        headers=test_client.auth_headers,
        json={
            "mode": "form",
            "values": {
                "LLM_BACKEND": "local\x00injected",
            },
        },
    )

    assert response.status_code == 400
    assert "invalid characters" in response.json()["detail"]


def test_api_settings_env_save_returns_llama_apply_plan(test_client, settings_env_fixture):
    env_path = settings_env_fixture["env_path"]
    env_path.write_text(
        env_path.read_text(encoding="utf-8") + "CTX_SIZE=8192\n",
        encoding="utf-8",
    )

    response = test_client.put(
        "/api/settings/env",
        headers=test_client.auth_headers,
        json={
            "mode": "form",
            "values": {
                "CTX_SIZE": "16384",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["applyPlan"]["status"] == "ready"
    assert payload["applyPlan"]["services"] == ["llama-server"]
    assert "llama-server" in payload["applyPlan"]["summary"]


def test_api_settings_env_preserves_commented_empty_llama_args(test_client, settings_env_fixture):
    env_path = settings_env_fixture["env_path"]

    response = test_client.put(
        "/api/settings/env",
        headers=test_client.auth_headers,
        json={
            "mode": "form",
            "values": {
                "LLM_BACKEND": "cloud",
            },
        },
    )

    assert response.status_code == 200
    updated_lines = env_path.read_text(encoding="utf-8").splitlines()
    assert "# LLAMA_ARG_N_CPU_MOE=25" in updated_lines
    assert not any(line.startswith("LLAMA_ARG_N_CPU_MOE=") for line in updated_lines)


def test_api_settings_env_unsets_empty_existing_llama_arg(test_client, settings_env_fixture):
    env_path = settings_env_fixture["env_path"]
    env_path.write_text(
        env_path.read_text(encoding="utf-8") + "LLAMA_ARG_N_CPU_MOE=30\n",
        encoding="utf-8",
    )

    response = test_client.put(
        "/api/settings/env",
        headers=test_client.auth_headers,
        json={
            "mode": "form",
            "values": {
                "LLAMA_ARG_N_CPU_MOE": "",
            },
        },
    )

    assert response.status_code == 200
    updated_lines = env_path.read_text(encoding="utf-8").splitlines()
    assert "# LLAMA_ARG_N_CPU_MOE=25" in updated_lines
    assert not any(line.startswith("LLAMA_ARG_N_CPU_MOE=") for line in updated_lines)


def test_api_settings_env_apply_calls_host_agent(test_client, monkeypatch):
    captured = {}

    def fake_call(service_ids):
        captured["service_ids"] = service_ids
        return {"status": "ok"}

    monkeypatch.setattr("main._call_agent_core_recreate", fake_call)

    response = test_client.post(
        "/api/settings/env/apply",
        headers=test_client.auth_headers,
        json={"service_ids": ["llama-server"]},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["service_ids"] == ["llama-server"]


def test_api_settings_env_apply_allows_hermes_services(test_client, monkeypatch):
    captured = {}

    def fake_call(service_ids):
        captured["service_ids"] = service_ids
        return {"status": "ok"}

    monkeypatch.setattr("main._call_agent_core_recreate", fake_call)

    response = test_client.post(
        "/api/settings/env/apply",
        headers=test_client.auth_headers,
        json={"service_ids": ["hermes-proxy", "hermes"]},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["service_ids"] == ["hermes", "hermes-proxy"]


def test_api_settings_env_apply_allows_model_router(test_client, monkeypatch):
    captured = {}

    def fake_call(service_ids):
        captured["service_ids"] = service_ids
        return {"status": "ok"}

    monkeypatch.setattr("main._call_agent_core_recreate", fake_call)

    response = test_client.post(
        "/api/settings/env/apply",
        headers=test_client.auth_headers,
        json={"service_ids": ["model-router"]},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["service_ids"] == ["model-router"]


def test_api_settings_env_apply_rejects_disallowed_service(test_client):
    response = test_client.post(
        "/api/settings/env/apply",
        headers=test_client.auth_headers,
        json={"service_ids": ["dashboard-api"]},
    )

    assert response.status_code == 400
    assert "not eligible" in response.json()["detail"]["message"].lower()


def test_check_host_agent_available_uses_shared_transport(monkeypatch):
    import settings

    calls = []

    def fake_request(method, path, *, timeout):
        calls.append((method, path, timeout))
        return {"status": "ok"}

    monkeypatch.setattr(settings, "request_agent_json", fake_request)

    assert settings._check_host_agent_available() is True
    assert calls == [("GET", "/health", 3)]


@pytest.mark.parametrize(
    "error",
    [
        pytest.param("unavailable", id="unavailable"),
        pytest.param("http-error", id="http-error"),
        pytest.param("protocol-error", id="protocol-error"),
    ],
)
def test_check_host_agent_available_handles_typed_transport_errors(monkeypatch, error):
    import settings
    from host_agent_client import AgentHTTPError, AgentProtocolError, AgentUnavailable

    failures = {
        "unavailable": AgentUnavailable("connection refused"),
        "http-error": AgentHTTPError(503, "not ready"),
        "protocol-error": AgentProtocolError("invalid JSON"),
    }

    def fail(*args, **kwargs):
        raise failures[error]

    monkeypatch.setattr(settings, "request_agent_json", fail)

    assert settings._check_host_agent_available() is False


def test_settings_apply_plan_maps_hermes_env_keys():
    from settings import _compute_env_apply_plan

    previous = {
        "HERMES_LANGUAGE": "en",
        "HERMES_PROXY_PORT": "9120",
        "ODS_AUTH_UPSTREAM": "ods-dashboard-api:3002",
        "WHATSAPP_ENABLED": "false",
        "SEARXNG_URL": "http://searxng:8080",
    }
    updated = {
        "HERMES_LANGUAGE": "pt",
        "HERMES_PROXY_PORT": "9121",
        "ODS_AUTH_UPSTREAM": "dashboard-api:3002",
        "WHATSAPP_ENABLED": "true",
        "SEARXNG_URL": "http://search:8080",
    }

    plan = _compute_env_apply_plan(previous, updated)

    assert plan["status"] == "ready"
    assert plan["services"] == ["hermes", "hermes-proxy"]
    assert plan["manualKeys"] == []


def test_settings_apply_plan_maps_agent_and_proxy_env_keys():
    from settings import _compute_env_apply_plan

    previous = {
        "APE_STRICT_MODE": "false",
        "ODS_PROXY_PORT": "80",
        "OPENCLAW_DANGEROUSLY_DISABLE_DEVICE_AUTH": "",
    }
    updated = {
        "APE_STRICT_MODE": "true",
        "ODS_PROXY_PORT": "8080",
        "OPENCLAW_DANGEROUSLY_DISABLE_DEVICE_AUTH": "true",
    }

    plan = _compute_env_apply_plan(previous, updated)

    assert plan["status"] == "ready"
    assert plan["services"] == ["ape", "ods-proxy", "openclaw"]
    assert plan["manualKeys"] == []


# --- Render round-trip fidelity ---


def test_render_env_preserves_extras_with_empty_values():
    """Keys with empty values must survive _render_env_from_values round-trip.

    Regression guard for fork issue #335: the old filter
    ``value != ""`` silently dropped keys like LLAMA_ARG_TENSOR_SPLIT=""
    on every save.
    """
    from main import _render_env_from_values

    values = {
        "LLM_BACKEND": "local",
        "TENSOR_SPLIT": "",       # intentionally empty
        "GPU_UUID": "GPU-abc123",
    }
    rendered = _render_env_from_values(values)
    assert "TENSOR_SPLIT=" in rendered
    assert "GPU_UUID=GPU-abc123" in rendered


@pytest.fixture()
def commented_example_template(tmp_path, monkeypatch):
    """Patch ``_resolve_template_path`` so .env.example resolution returns a
    controlled file containing a commented-assignment line.

    Required because the default test environment cannot resolve the real
    ``.env.example`` (INSTALL_DIR is /tmp/ods-test-install which does not
    exist), so without this fixture every value in ``values`` would fall
    through to the *extras* branch of ``_render_env_from_values`` and
    bypass the ``commented_assignment`` branch the #529 fix targets.

    LLAMA_ARG_TENSOR_SPLIT mirrors its real form at .env.example:184
    (``# KEY=            # trailing comment``).
    """
    example_path = tmp_path / ".env.example"
    example_path.write_text(
        "LLM_BACKEND=local\n"
        "# LLAMA_ARG_TENSOR_SPLIT=            # Proportional VRAM weights (e.g. 3,1)\n",
        encoding="utf-8",
    )

    def fake_resolve_template(name: str):
        if name == ".env.example":
            return example_path
        return tmp_path / name

    monkeypatch.setattr("main._resolve_template_path", fake_resolve_template)
    return example_path


def test_render_env_uncomments_commented_key_with_empty_value(commented_example_template):
    """Regression for #529: a commented-out key in .env.example with an explicit
    empty value in ``values`` must be rendered as an active empty assignment,
    not silently kept as the comment line.

    Exercises the ``commented_assignment`` branch of
    ``_render_env_from_values`` (line 700 in main.py) which the extras-only
    test above does not cover. Reverting the production fix flips this from
    PASS to FAIL.
    """
    from main import _render_env_from_values

    rendered = _render_env_from_values({"LLAMA_ARG_TENSOR_SPLIT": ""})
    lines = rendered.splitlines()
    assert "LLAMA_ARG_TENSOR_SPLIT=" in lines, "must be rendered as active empty assignment"
    # Comment-line form must not survive — the original line had a trailing
    # comment so test the substring rather than the exact line.
    assert not any(line.lstrip().startswith("# LLAMA_ARG_TENSOR_SPLIT=") for line in lines), \
        "comment line must not survive"


def test_render_env_uncomments_commented_key_with_value(commented_example_template):
    """Companion to the empty-value test: a commented key in .env.example with
    a non-empty value in ``values`` must also be uncommented and assigned."""
    from main import _render_env_from_values

    rendered = _render_env_from_values({"LLAMA_ARG_TENSOR_SPLIT": "3,1"})
    assert "LLAMA_ARG_TENSOR_SPLIT=3,1" in rendered.splitlines()


def test_render_env_preserves_commented_key_absent_from_values(commented_example_template):
    """Absent commented defaults should stay commented on dashboard saves.

    Explicit empty values are meaningful, but missing values should not turn
    optional template defaults into active empty assignments.
    """
    from main import _render_env_from_values

    rendered = _render_env_from_values({})  # nothing in values
    lines = rendered.splitlines()
    assert "LLAMA_ARG_TENSOR_SPLIT=" not in lines
    assert any(line.lstrip().startswith("# LLAMA_ARG_TENSOR_SPLIT=") for line in lines)


# --- Production schema secret-flag coverage ---


@pytest.mark.parametrize(
    "key",
    [
        "TARGET_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "TOGETHER_API_KEY",
        "LIVEKIT_API_KEY",
        "AUDIO_STT_OPENAI_API_KEY",
        "AUDIO_TTS_OPENAI_API_KEY",
    ],
)
def test_production_schema_marks_provider_api_keys_secret(key):
    """Credential API keys in the production schema must carry ``secret: true``.

    Regression guard: without the explicit flag, masking in both
    ``ods config show`` and ``GET /api/settings/env`` falls back to a
    name-pattern match. The schema should be the authoritative source.
    """
    import pathlib

    schema_path = pathlib.Path(__file__).resolve().parents[4] / ".env.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    entry = schema["properties"].get(key)
    assert entry is not None, f"schema missing entry for {key}"
    assert entry.get("secret") is True, f"{key} must have 'secret': true in .env.schema.json"


def test_env_example_keys_are_present_in_schema():
    """Every documented .env.example key should be editable in Settings."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[4]
    schema = json.loads((root / ".env.schema.json").read_text(encoding="utf-8"))
    example = (root / ".env.example").read_text(encoding="utf-8")
    documented_keys = {
        match.group(1)
        for match in re.finditer(r"^\s*#?\s*([A-Z][A-Z0-9_]+)=", example, flags=re.MULTILINE)
    }
    schema_keys = set(schema.get("properties", {}))

    assert documented_keys - schema_keys == set()
