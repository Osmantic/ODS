"""Selection checks must survive state changes and contend on a real lock."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extension-selection.py"
SPEC = importlib.util.spec_from_file_location("extension_selection", SCRIPT)
selection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(selection)


def extension(root, service_id, *, depends=(), compose_depends=(), enabled=True):
    directory = root / "extensions" / "services" / service_id
    directory.mkdir(parents=True, exist_ok=True)
    deps = ", ".join(depends)
    (directory / "manifest.yaml").write_text(
        f"service:\n  id: {service_id}\n  depends_on: [{deps}]\n", encoding="utf-8"
    )
    chosen = "compose.yaml" if enabled else "compose.yaml.disabled"
    compose_deps = ", ".join(compose_depends)
    (directory / chosen).write_text(
        f"services:\n  {service_id}:\n    image: example:latest\n"
        f"    depends_on: [{compose_deps}]\n", encoding="utf-8"
    )
    return directory


def restore(root, preset, *, compose_flags="-f docker-compose.base.yml"):
    base = root / "docker-compose.base.yml"
    if not base.exists():
        base.write_text("services: {}\n", encoding="utf-8")
    return selection.restore_preset(root, preset, compose_flags=compose_flags)


def test_preset_accepts_native_windows_compose_flags_before_library_enable(tmp_path):
    """The installed Windows stack must let the Library enable a dependency pair."""
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    (tmp_path / ".env").write_text("ODS_MODE=local\n", encoding="utf-8")
    (tmp_path / "docker-compose.base.yml").write_text(
        "services:\n  dashboard: {}\n", encoding="utf-8",
    )
    (tmp_path / "docker-compose.nvidia.yml").write_text(
        "services:\n  llama-server: {}\n", encoding="utf-8",
    )
    extension(tmp_path, "litellm")
    search = extension(tmp_path, "searxng", enabled=False)
    research = extension(tmp_path, "perplexica", depends=("searxng",), enabled=False)
    preset = tmp_path / "extensions.list"
    preset.write_text("enabled:searxng\nenabled:perplexica\n", encoding="utf-8")
    flags = ("--env-file .env -f docker-compose.base.yml "
             "-f docker-compose.nvidia.yml -f extensions/services/litellm/compose.yaml")

    assert restore(tmp_path, preset, compose_flags=flags) == (2, 0, [])
    assert (search / "compose.yaml").is_file()
    assert (research / "compose.yaml").is_file()


@pytest.mark.parametrize("prefix", [
    "--env-file", "--env-file ../.env", "--env-file /tmp/.env",
    "--env-file .env.local", r"--env-file .\env", '--env-file ".env"',
    "--env-file .env --env-file .env",
    "--project-name other", "-f", "-f docker-compose.base.yml --env-file .env",
])
def test_preset_rejects_untrusted_native_compose_flags_without_marker_moves(tmp_path, prefix):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    (tmp_path / ".env").write_text("ODS_MODE=local\n", encoding="utf-8")
    target = extension(tmp_path, "searxng", enabled=False)
    preset = tmp_path / "extensions.list"
    preset.write_text("enabled:searxng\n", encoding="utf-8")

    with pytest.raises(selection.SelectionError, match="Invalid current Compose flags"):
        restore(tmp_path, preset, compose_flags=f"{prefix} -f docker-compose.base.yml")
    assert (target / "compose.yaml.disabled").is_file()
    assert not (target / "compose.yaml").exists()


def test_preset_rejects_symlinked_native_env_file_without_marker_moves(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    external = tmp_path / "other.env"
    external.write_text("ODS_MODE=local\n", encoding="utf-8")
    try:
        (tmp_path / ".env").symlink_to(external)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    target = extension(tmp_path, "searxng", enabled=False)
    preset = tmp_path / "extensions.list"
    preset.write_text("enabled:searxng\n", encoding="utf-8")

    with pytest.raises(selection.SelectionError, match="Invalid current Compose environment file"):
        restore(tmp_path, preset, compose_flags="--env-file .env -f docker-compose.base.yml")
    assert (target / "compose.yaml.disabled").is_file()
    assert not (target / "compose.yaml").exists()


def test_selected_compose_and_user_shadowing(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    extension(tmp_path, "search")
    extension(tmp_path, "manifest-user", depends=("search",))
    extension(tmp_path, "compose-user", compose_depends=("search",))
    assert selection._enabled_dependents(tmp_path, "search") == ["compose-user", "manifest-user"]

    # The Compose resolver still selects the enabled bundled fragment even
    # when a same-name user definition is disabled.
    user = tmp_path / "data" / "user-extensions" / "manifest-user"
    user.mkdir()
    (user / "compose.yaml.disabled").write_text("services: {}\n", encoding="utf-8")
    assert selection._enabled_dependents(tmp_path, "search") == ["compose-user", "manifest-user"]

    (user / "compose.yaml.disabled").rename(user / "compose.yaml")
    (user / "manifest.yaml").write_text(
        "service:\n  id: manifest-user\n  depends_on: [search]\n", encoding="utf-8"
    )
    assert selection._enabled_dependents(tmp_path, "search") == ["manifest-user", "compose-user"]
    (user / "manifest.yaml").write_text(
        "service:\n  id: manifest-user\n  depends_on: []\n", encoding="utf-8"
    )
    assert selection._enabled_dependents(tmp_path, "search") == ["compose-user", "manifest-user"]


def test_incomplete_user_directory_cannot_hide_enabled_bundled_dependent(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "search")
    bundled = extension(tmp_path, "consumer", depends=("search",))
    user = tmp_path / "data" / "user-extensions" / "consumer"
    user.mkdir()

    # The Compose resolver still selects the bundled fragment. An interrupted
    # user install must not make its dependency disappear from disable checks.
    assert (bundled / "compose.yaml").is_file()
    assert selection._enabled_dependents(tmp_path, "search") == ["consumer"]
    with pytest.raises(selection.SelectionError, match="enabled extensions depend on search"):
        selection.run("disable", tmp_path, "search")
    assert (target / "compose.yaml").is_file()


def test_selected_json_manifest_dependent_blocks_disable(tmp_path):
    user_root = tmp_path / "data" / "user-extensions"
    user_root.mkdir(parents=True)
    target = extension(tmp_path, "search")
    consumer = user_root / "consumer"
    consumer.mkdir()
    (consumer / "compose.yaml").write_text(
        "services:\n  consumer:\n    image: alpine:3.22\n", encoding="utf-8",
    )
    (consumer / "manifest.json").write_text(json.dumps({
        "schema_version": "ods.services.v1",
        "service": {"id": "consumer", "depends_on": ["search"]},
    }), encoding="utf-8")

    assert selection._enabled_dependents(tmp_path, "search") == ["consumer"]
    with pytest.raises(selection.SelectionError, match="enabled extensions depend on search"):
        selection.run("check-disable", tmp_path, "search")
    assert (target / "compose.yaml").is_file()


def test_malformed_selected_json_manifest_fails_closed(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "search")
    consumer = extension(tmp_path, "consumer")
    (consumer / "manifest.yaml").unlink()
    (consumer / "manifest.json").write_text('{"service":', encoding="utf-8")

    with pytest.raises(selection.SelectionError, match="Cannot inspect selected file"):
        selection.run("check-disable", tmp_path, "search")
    assert (target / "compose.yaml").is_file()


def test_bad_selected_compose_fails_closed(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    extension(tmp_path, "search")
    peer = extension(tmp_path, "consumer", compose_depends=("search",))
    (peer / "compose.yaml").write_text("services: [invalid]\n", encoding="utf-8")
    with pytest.raises(selection.SelectionError, match="Invalid selected Compose services"):
        selection._enabled_dependents(tmp_path, "search")


def test_missing_yaml_module_fails_closed_for_selected_peer(tmp_path, monkeypatch):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    extension(tmp_path, "search")
    extension(tmp_path, "consumer", compose_depends=("search",))
    monkeypatch.setitem(sys.modules, "yaml", None)
    with pytest.raises(selection.SelectionError, match="PyYAML is required"):
        selection.run("disable", tmp_path, "search")
    assert (tmp_path / "extensions" / "services" / "search" / "compose.yaml").is_file()


def test_state_change_between_preflight_and_commit_retains_selection_and_data(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "search")
    retained = tmp_path / "data" / "search" / "settings.json"
    retained.parent.mkdir()
    retained.write_text("keep", encoding="utf-8")
    assert selection.run("check-disable", tmp_path, "search") == "ready"

    peer = extension(tmp_path, "consumer", compose_depends=("search",))
    with pytest.raises(selection.SelectionError, match="enabled extensions depend on search"):
        selection.run("disable", tmp_path, "search")
    assert (target / "compose.yaml").is_file()
    assert retained.read_text(encoding="utf-8") == "keep"

    (peer / "compose.yaml").rename(peer / "compose.yaml.disabled")
    assert selection.run("disable", tmp_path, "search") == "disabled"
    assert (target / "compose.yaml.disabled").is_file()
    assert not (target / "compose.yaml").exists()
    assert retained.read_text(encoding="utf-8") == "keep"


def test_cli_stop_and_commit_hold_selection_lock_against_dependent_enable(tmp_path, monkeypatch):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "search")
    dependent = extension(tmp_path, "consumer", depends=("search",), enabled=False)
    original_run = subprocess.run

    def stop_with_concurrent_enable(args, **kwargs):
        assert args[-2:] == ["stop", "search"]
        assert (target / "compose.yaml").is_file()
        contender = original_run(
            [sys.executable, str(SCRIPT), "enable", "--install-dir", str(tmp_path),
             "--service-id", "consumer", "--lock-timeout", "0.1"],
            capture_output=True, text=True, timeout=5,
        )
        assert contender.returncode == 1
        assert "Timed out waiting for extensions lock" in contender.stderr
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(selection.subprocess, "run", stop_with_concurrent_enable)
    assert selection.run("disable", tmp_path, "search", stop_mode="compose",
                         compose_flags="-f docker-compose.base.yml") == "disabled"
    assert (target / "compose.yaml.disabled").is_file()
    assert (dependent / "compose.yaml.disabled").is_file()


def test_cli_stop_failure_preserves_selection_and_cache(tmp_path, monkeypatch):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "search")
    cache = tmp_path / ".compose-flags"
    cache.write_text("previous selection", encoding="utf-8")
    monkeypatch.setattr(
        selection.subprocess, "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "Docker failure"),
    )
    with pytest.raises(selection.SelectionError, match="selection unchanged"):
        selection.run("disable", tmp_path, "search", stop_mode="compose",
                      compose_flags="-f docker-compose.base.yml")
    assert (target / "compose.yaml").is_file()
    assert cache.read_text(encoding="utf-8") == "previous selection"


def test_enable_rechecks_prerequisite_then_preserves_data(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    prerequisite = extension(tmp_path, "search", enabled=False)
    target = extension(tmp_path, "consumer", depends=("search",), enabled=False)
    retained = tmp_path / "data" / "consumer" / "settings.json"
    retained.parent.mkdir()
    retained.write_text("keep", encoding="utf-8")
    cache = tmp_path / ".compose-flags"
    cache.write_text("stale", encoding="utf-8")
    with pytest.raises(selection.SelectionError, match="disabled prerequisites: search"):
        selection.run("enable", tmp_path, "consumer")
    assert (target / "compose.yaml.disabled").is_file()
    assert cache.is_file()
    assert selection.run("enable", tmp_path, "search") == "enabled"
    assert (prerequisite / "compose.yaml").is_file()
    assert not cache.exists()
    assert selection.run("enable", tmp_path, "consumer") == "enabled"
    assert (target / "compose.yaml").is_file()
    assert retained.read_text(encoding="utf-8") == "keep"


def test_enable_accepts_same_fragment_and_base_services(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "consumer", enabled=False)
    (target / "compose.yaml.disabled").write_text(
        "services:\n  consumer:\n    image: example:latest\n"
        "    depends_on: [consumer-db, postgres]\n"
        "  consumer-db:\n    image: example:latest\n", encoding="utf-8"
    )
    assert selection.run("enable", tmp_path, "consumer") == "enabled"


def test_enable_core_bypass_does_not_trust_disabled_user_shadow(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "gateway", depends=("llama-server",), enabled=False)
    assert selection.run("enable", tmp_path, "gateway", core_services={"llama-server"}) == "enabled"
    (target / "compose.yaml").rename(target / "compose.yaml.disabled")
    user = tmp_path / "data" / "user-extensions" / "llama-server"
    user.mkdir()
    (user / "compose.yaml.disabled").write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(selection.SelectionError, match="disabled prerequisites: llama-server"):
        selection.run("enable", tmp_path, "gateway", core_services={"llama-server"})
    assert (target / "compose.yaml.disabled").is_file()


def test_enable_refuses_disabled_known_compose_dependency(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    extension(tmp_path, "search", enabled=False)
    target = extension(tmp_path, "consumer", compose_depends=("search",), enabled=False)
    with pytest.raises(selection.SelectionError, match="disabled prerequisites: search"):
        selection.run("enable", tmp_path, "consumer")
    assert (target / "compose.yaml.disabled").is_file()


def test_enable_existing_selection_revalidates_and_invalidates_cache(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "consumer", enabled=True)
    cache = tmp_path / ".compose-flags"
    cache.write_text("stale", encoding="utf-8")
    assert selection.run("enable", tmp_path, "consumer") == "already-enabled"
    assert not cache.exists()
    (target / "manifest.yaml").write_text(
        "service:\n  id: consumer\n  depends_on: [search]\n", encoding="utf-8"
    )
    with pytest.raises(selection.SelectionError, match="disabled prerequisites: search"):
        selection.run("enable", tmp_path, "consumer")
    assert (target / "compose.yaml").is_file()


def test_enable_refuses_divergent_dual_markers_without_losing_data(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "consumer")
    enabled = target / "compose.yaml"
    disabled = target / "compose.yaml.disabled"
    disabled.write_text("services:\n  other:\n    image: example:latest\n", encoding="utf-8")
    cache = tmp_path / ".compose-flags"
    cache.write_text("previous selection", encoding="utf-8")

    with pytest.raises(selection.SelectionError, match="Conflicting Compose selection files"):
        selection.run("enable", tmp_path, "consumer")

    assert enabled.is_file()
    assert disabled.is_file()
    assert cache.read_text(encoding="utf-8") == "previous selection"


def test_enable_reports_committed_selection_when_cache_save_fails(tmp_path, monkeypatch, capsys):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "consumer", enabled=False)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    resolver = scripts / "resolve-compose-stack.sh"
    resolver.write_text("#!/bin/sh\n", encoding="utf-8")
    resolver.chmod(0o755)
    monkeypatch.setattr(selection.shutil, "which", lambda _: "bash")
    monkeypatch.setattr(
        selection.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="-f docker-compose.base.yml\n", stderr="",
        ),
    )
    original_replace = selection.os.replace

    def fail_cache_replace(source, destination):
        if Path(destination).name == ".compose-flags":
            raise OSError("simulated cache write failure")
        original_replace(source, destination)

    monkeypatch.setattr(selection.os, "replace", fail_cache_replace)

    assert selection.run("enable", tmp_path, "consumer") == "enabled"
    assert (target / "compose.yaml").is_file()
    assert not (target / "compose.yaml.disabled").exists()
    assert not (tmp_path / ".compose-flags").exists()
    assert "WARNING: Cannot save Compose cache" in capsys.readouterr().err


def test_separate_process_lock_blocks_commit_then_releases(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "search")
    holder_code = (
        "import importlib.util,sys,time; from pathlib import Path; "
        "s=importlib.util.spec_from_file_location('selection',sys.argv[1]); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "lock=m._selection_lock(Path(sys.argv[2]),2); lock.__enter__(); "
        "print('held',flush=True); time.sleep(1); lock.__exit__(None,None,None)"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, str(SCRIPT), str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(selection.SelectionError, match="Timed out waiting"):
            selection.run("disable", tmp_path, "search", timeout=0.1)
        assert (target / "compose.yaml").is_file()
    finally:
        stdout, stderr = holder.communicate(timeout=5)
        assert holder.returncode == 0, stderr or stdout
    assert selection.run("disable", tmp_path, "search") == "disabled"


def test_preset_restore_orders_dependents_and_prerequisites(tmp_path, monkeypatch):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    search = extension(tmp_path, "search")
    consumer = extension(tmp_path, "consumer", depends=("search",))
    preset = tmp_path / "extensions.list"
    preset.write_text("disabled:search\ndisabled:consumer\n", encoding="utf-8")
    original_replace = selection.os.replace
    moves = []
    monkeypatch.setattr(selection, "_stop_for_disable", lambda *args, **kwargs: None)

    def ordered_replace(source, target):
        moves.append(Path(source).parent.name)
        if Path(source).parent.name == "search" and Path(target).name.endswith("disabled"):
            assert not (consumer / "compose.yaml").exists()
        if Path(source).parent.name == "consumer" and Path(target).name == "compose.yaml":
            assert (search / "compose.yaml").exists()
        original_replace(source, target)

    monkeypatch.setattr(selection.os, "replace", ordered_replace)
    assert restore(tmp_path, preset) == (0, 2, [])
    assert moves == ["consumer", "search"]

    moves.clear()
    preset.write_text("enabled:consumer\nenabled:search\n", encoding="utf-8")
    assert restore(tmp_path, preset) == (2, 0, [])
    assert moves == ["search", "consumer"]


def test_preset_rejects_invalid_final_graph_before_any_rename(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    search = extension(tmp_path, "search")
    consumer = extension(tmp_path, "consumer", compose_depends=("search",))
    preset = tmp_path / "extensions.list"
    preset.write_text("disabled:search\n", encoding="utf-8")
    retained = tmp_path / "data" / "search" / "settings.json"
    retained.parent.mkdir()
    retained.write_text("keep", encoding="utf-8")
    with pytest.raises(selection.SelectionError, match="without prerequisites: search"):
        restore(tmp_path, preset)
    assert (search / "compose.yaml").is_file()
    assert (consumer / "compose.yaml").is_file()
    assert retained.read_text(encoding="utf-8") == "keep"


def test_preset_rejects_conflicting_entries_and_user_shadow(tmp_path):
    user_root = tmp_path / "data" / "user-extensions"
    user_root.mkdir(parents=True)
    extension(tmp_path, "search")
    user_search = user_root / "search"
    user_search.mkdir()
    (user_search / "compose.yaml.disabled").write_text("services: {}\n", encoding="utf-8")
    consumer = extension(tmp_path, "consumer", depends=("search",), enabled=False)
    preset = tmp_path / "extensions.list"
    preset.write_text("enabled:consumer\ndisabled:consumer\n", encoding="utf-8")
    with pytest.raises(selection.SelectionError, match="Conflicting preset states"):
        restore(tmp_path, preset)
    preset.write_text("enabled:consumer\n", encoding="utf-8")
    with pytest.raises(selection.SelectionError, match="without prerequisites: search"):
        restore(tmp_path, preset)
    assert (consumer / "compose.yaml.disabled").is_file()
    assert (user_search / "compose.yaml.disabled").is_file()


def test_preset_partial_rename_failure_reports_committed_prefix(tmp_path, monkeypatch):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    search = extension(tmp_path, "search")
    consumer = extension(tmp_path, "consumer", depends=("search",))
    preset = tmp_path / "extensions.list"
    preset.write_text("disabled:search\ndisabled:consumer\n", encoding="utf-8")
    cache = tmp_path / ".compose-flags"
    cache.write_text("stale", encoding="utf-8")
    original_replace = selection.os.replace
    monkeypatch.setattr(selection, "_stop_for_disable", lambda *args, **kwargs: None)

    def fail_second(source, target):
        if Path(source).parent.name == "search":
            raise OSError("simulated rename failure")
        original_replace(source, target)

    monkeypatch.setattr(selection.os, "replace", fail_second)
    with pytest.raises(selection.SelectionError, match="after 0 enabled and 1 disabled"):
        restore(tmp_path, preset)
    assert (consumer / "compose.yaml.disabled").is_file()
    assert (search / "compose.yaml").is_file()
    assert not cache.exists()


def test_preset_repairs_invalid_starting_graph_before_enable_start(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    search = extension(tmp_path, "search", enabled=False)
    consumer = extension(tmp_path, "consumer", depends=("search",))
    preset = tmp_path / "extensions.list"
    preset.write_text("enabled:consumer\n", encoding="utf-8")
    with pytest.raises(selection.SelectionError, match="without prerequisites: search"):
        restore(tmp_path, preset)
    assert (search / "compose.yaml.disabled").is_file()
    assert (consumer / "compose.yaml").is_file()

    preset.write_text("enabled:search\n", encoding="utf-8")
    assert restore(tmp_path, preset) == (1, 0, [])
    assert (search / "compose.yaml").is_file()
    assert (consumer / "compose.yaml").is_file()


def test_preset_noop_enable_invalidates_stale_compose_cache(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    extension(tmp_path, "search")
    cache = tmp_path / ".compose-flags"
    cache.write_text("stale flags", encoding="utf-8")
    preset = tmp_path / "extensions.list"
    preset.write_text("enabled:search\n", encoding="utf-8")
    assert restore(tmp_path, preset) == (0, 0, [])
    assert not cache.exists()


def test_preset_rejects_symlink_and_directory_inputs(tmp_path):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "search")
    preset = tmp_path / "extensions.list"
    preset.write_text("disabled:search\n", encoding="utf-8")
    linked = tmp_path / "linked.list"
    try:
        linked.symlink_to(preset)
    except (OSError, NotImplementedError):
        pass
    else:
        with pytest.raises(selection.SelectionError, match="Invalid preset extensions list"):
            restore(tmp_path, linked)
    cache = tmp_path / ".compose-flags"
    cache.mkdir()
    with pytest.raises(selection.SelectionError, match="Compose cache is a directory"):
        restore(tmp_path, preset)
    assert (target / "compose.yaml").is_file()


def test_preset_stops_every_owned_fragment_service_before_disabling(tmp_path, monkeypatch):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "search")
    (target / "compose.yaml").write_text(
        "services:\n  search: {}\n  search-db: {}\n", encoding="utf-8",
    )
    helper = tmp_path / "scripts" / "stop-owned-containers.py"
    helper.parent.mkdir()
    helper.write_text("", encoding="utf-8")
    preset = tmp_path / "extensions.list"
    preset.write_text("disabled:search\n", encoding="utf-8")
    calls = []

    def stopped_before_rename(command, **kwargs):
        calls.append(command)
        assert (target / "compose.yaml").is_file()
        assert not (target / "compose.yaml.disabled").exists()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(selection.subprocess, "run", stopped_before_rename)
    assert restore(tmp_path, preset) == (0, 1, [])
    assert calls == [[sys.executable, str(helper), "--install-dir", str(tmp_path),
                      "--preserve-restart-policy",
                      "--service", "search", "--service", "search-db"]]
    assert (target / "compose.yaml.disabled").is_file()


def test_preset_does_not_stop_shared_core_service(tmp_path, monkeypatch):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    (tmp_path / "docker-compose.base.yml").write_text(
        "services:\n  litellm: {}\n", encoding="utf-8",
    )
    target = extension(tmp_path, "langfuse")
    (target / "compose.yaml").write_text(
        "services:\n  langfuse: {}\n  litellm: {}\n", encoding="utf-8",
    )
    helper = tmp_path / "scripts" / "stop-owned-containers.py"
    helper.parent.mkdir()
    helper.write_text("", encoding="utf-8")
    preset = tmp_path / "extensions.list"
    preset.write_text("disabled:langfuse\n", encoding="utf-8")
    calls = []

    def record(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(selection.subprocess, "run", record)
    assert restore(tmp_path, preset) == (0, 1, [])
    assert calls == [[sys.executable, str(helper), "--install-dir", str(tmp_path),
                      "--preserve-restart-policy",
                      "--service", "langfuse"]]


def test_preset_stops_local_litellm_despite_inactive_external_overlay(tmp_path, monkeypatch):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    (tmp_path / "docker-compose.base.yml").write_text(
        "services:\n  dashboard-api: {}\n", encoding="utf-8",
    )
    (tmp_path / "docker-compose.external-llm.yml").write_text(
        "services:\n  litellm: {}\n", encoding="utf-8",
    )
    extension(tmp_path, "litellm")
    helper = tmp_path / "scripts" / "stop-owned-containers.py"
    helper.parent.mkdir()
    helper.write_text("", encoding="utf-8")
    preset = tmp_path / "extensions.list"
    preset.write_text("disabled:litellm\n", encoding="utf-8")
    calls = []

    def record(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(selection.subprocess, "run", record)
    assert restore(tmp_path, preset) == (0, 1, [])
    assert calls == [[sys.executable, str(helper), "--install-dir", str(tmp_path),
                      "--preserve-restart-policy", "--service", "litellm"]]


def test_preset_refuses_litellm_disable_with_selected_external_overlay(tmp_path, monkeypatch):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    (tmp_path / "docker-compose.base.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "docker-compose.external-llm.yml").write_text(
        "services:\n  litellm: {}\n", encoding="utf-8",
    )
    target = extension(tmp_path, "litellm")
    unrelated = extension(tmp_path, "zz-unrelated")
    preset = tmp_path / "extensions.list"
    preset.write_text("disabled:litellm\ndisabled:zz-unrelated\n", encoding="utf-8")
    monkeypatch.setattr(selection.subprocess, "run", lambda *args, **kwargs: pytest.fail(
        "selected external gateway LiteLLM must not be stopped",
    ))
    with pytest.raises(selection.SelectionError, match="requires litellm"):
        restore(tmp_path, preset, compose_flags=(
            "-f docker-compose.base.yml -f docker-compose.external-llm.yml"
        ))
    assert (target / "compose.yaml").is_file()
    assert (unrelated / "compose.yaml").is_file()


def test_preset_disables_library_service_with_selected_mac_native_overlay(tmp_path, monkeypatch):
    """A valid Compose override must not strand an installed Library service."""
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    (tmp_path / "docker-compose.base.yml").write_text("services: {}\n", encoding="utf-8")
    native = tmp_path / "installers" / "macos" / "pixel-native.compose.yaml.disabled"
    native.parent.mkdir(parents=True)
    shipped = SCRIPT.parents[1] / "installers" / "macos" / native.name
    native.write_text(shipped.read_text(encoding="utf-8"), encoding="utf-8")
    target = extension(tmp_path, "cyberchef")
    helper = tmp_path / "scripts" / "stop-owned-containers.py"
    helper.parent.mkdir()
    helper.write_text("", encoding="utf-8")
    preset = tmp_path / "extensions.list"
    preset.write_text("disabled:cyberchef\n", encoding="utf-8")
    calls = []

    def stopped(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(selection.subprocess, "run", stopped)
    flags = f"-f docker-compose.base.yml -f {native.relative_to(tmp_path).as_posix()}"
    assert selection.restore_preset(tmp_path, preset, compose_flags=flags, strict=True) == (0, 1, [])
    assert calls == [[sys.executable, str(helper), "--install-dir", str(tmp_path),
                      "--preserve-restart-policy", "--service", "cyberchef"]]
    assert (target / "compose.yaml.disabled").is_file()
    assert not (target / "compose.yaml").exists()


def test_unknown_compose_tag_keeps_library_selection_enabled(tmp_path, monkeypatch):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    (tmp_path / "docker-compose.base.yml").write_text("services: {}\n", encoding="utf-8")
    native = tmp_path / "installers" / "macos" / "pixel-native.compose.yaml.disabled"
    native.parent.mkdir(parents=True)
    native.write_text("services:\n  pixel-native-ingress:\n    volumes: !unknown [x]\n",
                      encoding="utf-8")
    target = extension(tmp_path, "cyberchef")
    preset = tmp_path / "extensions.list"
    preset.write_text("disabled:cyberchef\n", encoding="utf-8")
    monkeypatch.setattr(selection.subprocess, "run", lambda *_a, **_k: pytest.fail(
        "unknown tags must be refused before stopping a service"))
    with pytest.raises(selection.SelectionError, match="Cannot inspect selected file"):
        selection.restore_preset(
            tmp_path, preset,
            compose_flags=f"-f docker-compose.base.yml -f {native.relative_to(tmp_path).as_posix()}",
            strict=True,
        )
    assert (target / "compose.yaml").is_file()


def test_preset_stops_shared_service_only_after_last_overlay_is_disabled(tmp_path, monkeypatch):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    for service_id in ("first", "second"):
        target = extension(tmp_path, service_id)
        (target / "compose.yaml").write_text(
            f"services:\n  {service_id}: {{}}\n  shared: {{}}\n", encoding="utf-8",
        )
    helper = tmp_path / "scripts" / "stop-owned-containers.py"
    helper.parent.mkdir()
    helper.write_text("", encoding="utf-8")
    preset = tmp_path / "extensions.list"
    preset.write_text("disabled:first\ndisabled:second\n", encoding="utf-8")
    calls = []

    def record(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(selection.subprocess, "run", record)
    assert restore(tmp_path, preset) == (0, 2, [])
    assert calls == [
        [sys.executable, str(helper), "--install-dir", str(tmp_path),
         "--preserve-restart-policy",
         "--service", "second"],
        [sys.executable, str(helper), "--install-dir", str(tmp_path),
         "--preserve-restart-policy",
         "--service", "first", "--service", "shared"],
    ]


def test_preset_stop_failure_keeps_marker_enabled(tmp_path, monkeypatch):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "search")
    helper = tmp_path / "scripts" / "stop-owned-containers.py"
    helper.parent.mkdir()
    helper.write_text("", encoding="utf-8")
    preset = tmp_path / "extensions.list"
    preset.write_text("disabled:search\n", encoding="utf-8")
    monkeypatch.setattr(
        selection.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "stop refused"),
    )
    with pytest.raises(selection.SelectionError, match="Could not confirm stop for search"):
        restore(tmp_path, preset)
    assert (target / "compose.yaml").is_file()


def test_preset_empty_fragment_never_issues_unfiltered_stop(tmp_path, monkeypatch):
    (tmp_path / "data" / "user-extensions").mkdir(parents=True)
    target = extension(tmp_path, "empty")
    (target / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    preset = tmp_path / "extensions.list"
    preset.write_text("disabled:empty\n", encoding="utf-8")
    monkeypatch.setattr(selection.subprocess, "run", lambda *args, **kwargs: pytest.fail(
        "empty fragment must not invoke an unfiltered owned-container stop",
    ))
    assert restore(tmp_path, preset) == (0, 1, [])
    assert (target / "compose.yaml.disabled").is_file()
