"""Required extension settings are requested before install or enable.

A library extension whose Compose file uses ``${NAME:?}`` cannot start without
NAME, and an enabled definition that cannot be interpolated fails Compose for
the whole stack. The install and enable endpoints therefore refuse up front,
with the keys to configure, instead of acknowledging work that cannot run.
"""

import json

import pytest
import yaml

from extension_install_plan import build_install_plan, declares_setup_hook
from test_extensions import _patch_mutation_config

GOTIFY_COMPOSE = (
    "services:\n  gotify:\n    image: ods/gotify:3.1.1-local-v1\n    environment:\n"
    "      GOTIFY_DEFAULTUSER_PASS: ${GOTIFY_ADMIN_PASSWORD:?Set the initial administrator password}\n")
# A declared-required setting with a Compose default (immich's DB password).
DEFAULTED_COMPOSE = (
    "services:\n  photos:\n    image: example/photos:1\n    environment:\n"
    "      - DB_PASSWORD=${PHOTOS_DB_PASSWORD:-postgres}\n")
ADMIN_PASSWORD = {"key": "GOTIFY_ADMIN_PASSWORD", "required": True, "secret": True,
                  "description": "Initial administrator password", "default": "never-project-this"}


def _definition(root, service_id, compose, env_vars, *, enabled=True, name=None, **service):
    directory = root / service_id
    directory.mkdir(parents=True)
    (directory / ("compose.yaml" if enabled else "compose.yaml.disabled")).write_text(compose, encoding="utf-8")
    (directory / "manifest.yaml").write_text(yaml.safe_dump({
        "schema_version": "ods.services.v1",
        "service": {"id": service_id, "name": name or service_id, "env_vars": env_vars, **service},
    }), encoding="utf-8")
    return directory


@pytest.fixture
def host(tmp_path, monkeypatch):
    _patch_mutation_config(monkeypatch, tmp_path)
    import config
    configured = set()
    monkeypatch.setattr(config, "_read_env_value", lambda key: "value" if key in configured else "")
    installs, starts = [], []
    monkeypatch.setattr("routers.extensions._call_agent_install",
                        lambda sid, operation_id=None: installs.append(sid) or True)
    monkeypatch.setattr("routers.extensions._call_agent", lambda action, sid: starts.append((action, sid)) or True)
    monkeypatch.setattr("routers.extensions._call_agent_compose_rename", lambda action, sid: True)

    def progress(service_id, status):
        directory = tmp_path / "extension-progress"
        directory.mkdir(exist_ok=True)
        (directory / f"{service_id}.json").write_text(json.dumps({
            "service_id": service_id, "status": status, "error": "previous attempt failed",
            "started_at": "2026-09-25T10:50:00+00:00", "updated_at": "2026-09-25T10:50:00+00:00"}))

    class Host:
        library = tmp_path / "lib"
        users = tmp_path / "user"
        builtin = tmp_path / "builtin"
        data = tmp_path
    Host.configured, Host.installs, Host.starts, Host.progress = configured, installs, starts, progress
    return Host


def _post(client, path):
    return client.post(path, headers=client.auth_headers)


def test_install_refuses_missing_required_setting_before_copying_anything(test_client, host):
    _definition(host.library, "gotify", GOTIFY_COMPOSE, [ADMIN_PASSWORD], name="Gotify")

    response = _post(test_client, "/api/extensions/gotify/install")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail == {
        "code": "missing_configuration",
        "service_id": "gotify",
        "message": ("Gotify needs required settings before it can be installed: GOTIFY_ADMIN_PASSWORD. "
                    "Nothing was changed."),
        "missing_configuration": ["GOTIFY_ADMIN_PASSWORD"],
        "configuration": [{"key": "GOTIFY_ADMIN_PASSWORD", "secret": True,
                           "description": "Initial administrator password"}],
    }
    assert "never-project-this" not in response.text
    assert not (host.users / "gotify").exists()
    assert not (host.data / "extension-progress" / "gotify.json").exists()
    assert host.installs == []


def test_install_proceeds_once_the_setting_is_configured(test_client, host):
    _definition(host.library, "gotify", GOTIFY_COMPOSE, [ADMIN_PASSWORD])
    host.configured.add("GOTIFY_ADMIN_PASSWORD")

    response = _post(test_client, "/api/extensions/gotify/install")

    assert response.status_code == 200
    assert response.json()["message"] == "Extension installed and starting."
    assert host.installs == ["gotify"]
    assert (host.users / "gotify" / "compose.yaml").is_file()


def test_fresh_install_collects_every_declared_required_setting(test_client, host):
    # Nothing is initialized yet, so a Compose default is not a reason to
    # skip a setting the manifest declares required.
    field = {"key": "PHOTOS_DB_PASSWORD", "required": True, "secret": True}
    optional = {"key": "PHOTOS_THEME", "required": False}
    _definition(host.library, "photos", DEFAULTED_COMPOSE, [field, optional])

    response = _post(test_client, "/api/extensions/photos/install")

    assert response.status_code == 400
    assert response.json()["detail"]["missing_configuration"] == ["PHOTOS_DB_PASSWORD"]


@pytest.mark.parametrize("hook", [{"setup_hook": "setup.sh"}, {"hooks": {"post_install": "hooks/post_install.sh"}}])
def test_setup_hook_owns_its_settings_during_install(test_client, host, hook):
    _definition(host.library, "hooked", GOTIFY_COMPOSE.replace("GOTIFY_ADMIN", "HOOKED_ADMIN"),
                [{**ADMIN_PASSWORD, "key": "HOOKED_ADMIN_PASSWORD"}], **hook)

    response = _post(test_client, "/api/extensions/hooked/install")

    assert response.status_code == 200
    assert host.installs == ["hooked"]


def test_enable_refuses_unresolvable_definition_and_leaves_it_disabled(test_client, host):
    extension = _definition(host.users, "gotify", GOTIFY_COMPOSE, [ADMIN_PASSWORD], enabled=False, name="Gotify")
    host.progress("gotify", "error")  # Disabled by the host agent after a failed install.

    response = _post(test_client, "/api/extensions/gotify/enable")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "missing_configuration"
    assert detail["message"].startswith("Gotify needs required settings before it can be started: GOTIFY_ADMIN_PASSWORD.")
    assert (extension / "compose.yaml.disabled").is_file() and not (extension / "compose.yaml").exists()
    assert host.starts == []

    host.configured.add("GOTIFY_ADMIN_PASSWORD")
    assert _post(test_client, "/api/extensions/gotify/enable").status_code == 200
    assert (extension / "compose.yaml").is_file()
    assert host.starts == [("start", "gotify")]


def test_enable_does_not_demand_settings_compose_can_default(test_client, host):
    # An installed definition may already have initialized data with the
    # Compose default; forcing a new value there would break it.
    field = {"key": "PHOTOS_DB_PASSWORD", "required": True, "secret": True}
    extension = _definition(host.users, "photos", DEFAULTED_COMPOSE, [field], enabled=False)

    response = _post(test_client, "/api/extensions/photos/enable")

    assert response.status_code == 200
    assert (extension / "compose.yaml").is_file()


def test_enable_retry_reruns_setup_hook_but_plain_enable_does_not(test_client, host):
    compose = GOTIFY_COMPOSE.replace("GOTIFY_ADMIN", "HOOKED_ADMIN")
    field = {**ADMIN_PASSWORD, "key": "HOOKED_ADMIN_PASSWORD"}
    _definition(host.users, "hooked", compose, [field], enabled=False, setup_hook="setup.sh")

    refused = _post(test_client, "/api/extensions/hooked/enable")
    assert refused.status_code == 400
    assert refused.json()["detail"]["missing_configuration"] == ["HOOKED_ADMIN_PASSWORD"]

    host.progress("hooked", "error")  # The agent re-runs the hook on this retry.
    assert _post(test_client, "/api/extensions/hooked/enable").status_code == 200


def test_auto_enabled_dependency_is_checked_before_anything_is_activated(test_client, host):
    app = _definition(host.users, "app", "services:\n  app:\n    image: example/app:1\n", [],
                      enabled=False, depends_on=["gotify"])
    dependency = _definition(host.users, "gotify", GOTIFY_COMPOSE, [ADMIN_PASSWORD], enabled=False, name="Gotify")

    response = _post(test_client, "/api/extensions/app/enable?auto_enable_deps=true")

    assert response.status_code == 400
    assert response.json()["detail"]["service_id"] == "gotify"
    assert (app / "compose.yaml.disabled").is_file() and (dependency / "compose.yaml.disabled").is_file()
    assert host.starts == []


def test_builtin_enable_keeps_existing_behavior(test_client, host):
    _definition(host.builtin, "builtin-svc", GOTIFY_COMPOSE.replace("GOTIFY_ADMIN", "BUILTIN_SVC_ADMIN"),
                [{**ADMIN_PASSWORD, "key": "BUILTIN_SVC_ADMIN_PASSWORD"}], enabled=False)

    assert _post(test_client, "/api/extensions/builtin-svc/enable").status_code == 200


def test_unreadable_declarations_do_not_become_a_new_refusal(test_client, host):
    _definition(host.library, "odd", "services:\n  odd:\n    image: example/odd:1\n",
                [{"key": "ODD_TOKEN", "required": "yes"}])

    assert _post(test_client, "/api/extensions/odd/install").status_code == 200


def test_install_plan_marks_steps_whose_setup_hook_owns_settings():
    def plan(service):
        return build_install_plan("app", [{"id": "app", "status": "not_installed", "installable": True}],
                                  {"app": service}.__getitem__, lambda key: False)["steps"][0]

    field = {"key": "APP_SECRET", "required": True, "secret": True}
    assert plan({"id": "app", "env_vars": [field]})["setupHook"] is False
    hooked = plan({"id": "app", "env_vars": [field], "setup_hook": "setup.sh"})
    assert hooked["setupHook"] is True
    # Presence is still reported exactly as before for the Portal.
    assert hooked["missingConfiguration"] == ["APP_SECRET"]
    assert declares_setup_hook({"hooks": {"post_install": "hooks/post.sh"}}) is True
    assert declares_setup_hook({"hooks": {"pre_start": "hooks/pre.sh"}, "setup_hook": " "}) is False
