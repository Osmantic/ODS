"""The host agent pulls images even when the user's credential helper cannot run.

Docker Desktop's WSL integration writes ``"credsStore": "desktop.exe"`` to
~/.docker/config.json. The helper is on the Windows PATH that interactive WSL
shells append, not on the systemd service PATH, so every extension install on
Strixy (2026-10-03) failed with:

    error getting credentials - err: exec: "docker-credential-desktop.exe":
    executable file not found in $PATH
"""

import inspect
import json
import stat

from test_host_agent_install_rollback import _mod


def _home(tmp_path, config):
    home = tmp_path / "home"
    (home / ".docker").mkdir(parents=True)
    if config is not None:
        (home / ".docker" / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return home


def _environ(home, path_dir, **extra):
    path_dir.mkdir(exist_ok=True)
    return {"HOME": str(home), "PATH": str(path_dir), **extra}


def _helper(path_dir, name):
    path_dir.mkdir(exist_ok=True)
    helper = path_dir / f"docker-credential-{name}"
    helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)


def test_unreachable_desktop_helper_uses_credential_free_install_config(tmp_path):
    home = _home(tmp_path, {"credsStore": "desktop.exe", "auths": {"private.example": {"auth": "x"}}})
    install = tmp_path / "ods"
    install.mkdir()

    config_dir = _mod._public_docker_client_config(install, _environ(home, tmp_path / "bin"))

    assert config_dir == install / "data" / "docker-client-public"
    document = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
    assert document == {"auths": {}}
    assert not list(config_dir.glob("*.tmp"))


def test_user_cli_plugin_directories_stay_discoverable(tmp_path):
    extra = tmp_path / "plugins"
    extra.mkdir()
    home = _home(tmp_path, {"credsStore": "desktop.exe",
                            "cliPluginsExtraDirs": [str(extra), "relative", str(tmp_path / "gone"), str(extra)]})
    (home / ".docker" / "cli-plugins").mkdir()
    install = tmp_path / "ods"
    install.mkdir()

    config_dir = _mod._public_docker_client_config(install, _environ(home, tmp_path / "bin"))

    document = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
    assert document["cliPluginsExtraDirs"] == [str(home / ".docker" / "cli-plugins"), str(extra)]


def test_unreachable_per_registry_helper_also_switches(tmp_path):
    home = _home(tmp_path, {"credHelpers": {"registry.example": "ecr-login"}})
    install = tmp_path / "ods"
    install.mkdir()

    assert _mod._public_docker_client_config(install, _environ(home, tmp_path / "bin")) is not None


def test_runnable_helper_keeps_the_user_config(tmp_path):
    home = _home(tmp_path, {"credsStore": "desktop.exe", "credHelpers": {"ghcr.io": "pass"}})
    install = tmp_path / "ods"
    install.mkdir()
    _helper(tmp_path / "bin", "desktop.exe")
    _helper(tmp_path / "bin", "pass")

    assert _mod._public_docker_client_config(install, _environ(home, tmp_path / "bin")) is None
    assert not (install / "data").exists()


def test_explicit_docker_config_and_missing_config_are_left_alone(tmp_path):
    install = tmp_path / "ods"
    install.mkdir()
    home = _home(tmp_path, {"credsStore": "desktop.exe"})
    owner_choice = _environ(home, tmp_path / "bin", DOCKER_CONFIG=str(tmp_path / "owner"))
    assert _mod._public_docker_client_config(install, owner_choice) is None

    bare = _home(tmp_path / "bare", None)
    assert _mod._public_docker_client_config(install, _environ(bare, tmp_path / "bin")) is None
    assert not (install / "data").exists()


def test_startup_exports_the_config_before_any_docker_command():
    source = inspect.getsource(_mod.main)
    export = source.index('os.environ["DOCKER_CONFIG"] = str(docker_config)')
    assert source.index("_public_docker_client_config(INSTALL_DIR, os.environ)") < export
    assert export < source.index("_schedule_initial_switchboard_verification(")
    assert export < source.index("_create_host_agent_server(")
