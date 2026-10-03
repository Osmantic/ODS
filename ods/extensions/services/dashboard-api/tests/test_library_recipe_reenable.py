"""Enabling an installed curated library recipe reuses install's trust decision.

Install grants a curated recipe its local ``build:`` and the host-gateway
``extra_hosts`` route after staging it from the library. Disabling and then
enabling it, or starting it after a stop, must reach the same decision from
the same evidence: the installed files still match the library recipe. An
imported recipe, or a curated one whose files changed after install, must
still get the untrusted checks.

The recipes are the real curated gaia (local build + host-gateway),
mapshaper (local build) and ollama (image only) recipes, installed through
the real library install path into a temporary root.
"""

import json
import logging
import os
import shutil
from pathlib import Path

import pytest

from routers import extensions as ext_mod
from test_curated_library_recipes import _resolve
from test_extension_installed_recipe import installed_recipe  # noqa: F401 (fixture)
from test_extensions import _patch_mutation_config


ODS = Path(__file__).resolve().parents[4]
LIBRARY = ODS / "extensions/library/services"


@pytest.fixture()
def roots(test_client, monkeypatch, tmp_path):
    """A library and an install root whose user-extensions the resolver reads."""
    library, install = tmp_path / "lib", tmp_path / "ods"
    user = install / "data/user-extensions"
    (install / "config").mkdir(parents=True)
    (install / "data").mkdir()
    shutil.copy2(ODS / "config/core-service-ids.json", install / "config/core-service-ids.json")
    # A resolvable core stack, as test_curated_library_recipes._install_root
    # writes it: the resolver drops a user extension that needs a service no
    # resolved file declares.
    (install / "docker-compose.base.yml").write_text(
        "services:\n  llama-server:\n    image: example:llama-server\n", encoding="utf-8")
    for backend in ("nvidia", "amd", "cpu"):
        (install / f"docker-compose.{backend}.yml").write_text("services: {}\n", encoding="utf-8")
    _patch_mutation_config(monkeypatch, tmp_path, lib_dir=library, user_dir=user)
    monkeypatch.setattr(ext_mod, "_call_agent_invalidate_compose_cache", lambda: None)
    monkeypatch.setattr(ext_mod, "_call_agent_hook", lambda _sid, _hook: True)
    monkeypatch.setattr(ext_mod, "_sync_extension_config",
                        lambda _sid, *, preserve_existing=False: True)
    return library, user


def _install(roots, recipe):
    library, user = roots
    shutil.copytree(LIBRARY / recipe, library / recipe)
    with ext_mod._extensions_lock():
        ext_mod._install_from_library(recipe)
    return user / recipe


def _post(test_client, recipe, action):
    return test_client.post(f"/api/extensions/{recipe}/{action}",
                            headers=test_client.auth_headers)


def _lost_privileges(response):
    """The part of a 400 detail that says why curated privileges were lost."""
    detail = response.json()["detail"]
    return detail if "no longer has its curated-library privileges" in detail else ""


def _mark_imported(directory):
    """Give a recipe the upstream.json marker of an imported GitHub recipe."""
    path = directory / "upstream.json"
    upstream = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
        "repository": "https://github.com/example/project", "commit": "a" * 40,
    }
    path.write_text(json.dumps({**upstream, "origin": "github-proposal"}), encoding="utf-8")


@pytest.mark.parametrize("recipe", ["gaia", "mapshaper", "ollama"])
def test_disable_then_enable_curated_recipe(test_client, roots, recipe):
    installed = _install(roots, recipe)

    disabled = _post(test_client, recipe, "disable")
    assert disabled.status_code == 200, disabled.text
    assert (installed / "compose.yaml.disabled").is_file()

    enabled = _post(test_client, recipe, "enable")
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["action"] == "enabled"
    assert (installed / "compose.yaml").is_file()
    assert not (installed / "compose.yaml.disabled").exists()

    # A second cycle behaves the same: nothing the round trip writes counts
    # as a local modification.
    assert _post(test_client, recipe, "disable").status_code == 200
    assert _post(test_client, recipe, "enable").status_code == 200


@pytest.mark.skipif(shutil.which("bash") is None, reason="the resolver is a Bash script")
def test_reenabled_recipes_stay_in_the_resolved_stack(test_client, roots):
    """After disable and enable, the real compose resolver keeps both recipes."""
    _library, user = roots
    for recipe in ("gaia", "mapshaper"):
        _install(roots, recipe)
        assert _post(test_client, recipe, "disable").status_code == 200
        assert _post(test_client, recipe, "enable").status_code == 200

    for backend in ("nvidia", "amd", "cpu"):
        files, stderr = _resolve(user.parent.parent, backend)

        for recipe in ("gaia", "mapshaper"):
            assert f"data/user-extensions/{recipe}/compose.yaml" in files, stderr
        # Neither refused (compose policy) nor skipped (dependency cascade).
        assert not [line for line in stderr.splitlines() if line.startswith("WARNING")], stderr


@pytest.mark.parametrize("recipe", ["gaia", "mapshaper"])
def test_start_after_stop_keeps_curated_trust(test_client, roots, recipe):
    """Enable on an enabled, stopped extension scans compose.yaml the same way."""
    installed = _install(roots, recipe)

    response = _post(test_client, recipe, "enable")

    assert response.status_code == 200, response.text
    assert (installed / "compose.yaml").is_file()


@pytest.mark.parametrize("recipe", ["gaia", "mapshaper"])
def test_same_compose_as_imported_recipe_is_rejected(test_client, roots, recipe):
    """Matching the library byte for byte is not enough: install's trust
    decision must also hold, and an imported recipe never gets it."""
    library, _user = roots
    installed = _install(roots, recipe)
    assert _post(test_client, recipe, "disable").status_code == 200
    _mark_imported(library / recipe)
    _mark_imported(installed)

    response = _post(test_client, recipe, "enable")

    assert response.status_code == 400
    assert "local build" in response.json()["detail"]
    # It never had curated privileges, so there are none to explain.
    assert not _lost_privileges(response)
    assert (installed / "compose.yaml.disabled").is_file()
    assert not (installed / "compose.yaml").exists()


def test_unchanged_imported_recipe_is_not_trusted_as_curated(installed_recipe):  # noqa: F811
    """An imported GitHub recipe whose files all match its library package
    keeps install's decision for it, which is untrusted."""
    _root, directory, _library, _candidate, _projection = installed_recipe
    with ext_mod._staged_library_extension("humanize", directory) as (staged, _digest):
        assert ext_mod._installed_definition_difference(staged, directory) is None

    assert ext_mod._installed_library_recipe_trust(
        "humanize", directory, directory / "compose.yaml") == (False, None)


@pytest.mark.parametrize("recipe", ["gaia", "mapshaper"])
def test_same_compose_without_library_recipe_is_rejected(test_client, roots, recipe):
    """The extension's name alone never grants curated privileges."""
    library, _user = roots
    installed = _install(roots, recipe)
    assert _post(test_client, recipe, "disable").status_code == 200
    shutil.rmtree(library / recipe)

    response = _post(test_client, recipe, "enable")

    assert response.status_code == 400
    assert "local build" in response.json()["detail"]
    assert not _lost_privileges(response)
    assert (installed / "compose.yaml.disabled").is_file()


def _append(path, text):
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


TAMPERING = {
    "gaia-dockerfile": ("gaia", "Dockerfile", lambda d: _append(d / "Dockerfile", "RUN id\n")),
    "gaia-compose": ("gaia", "compose.yaml.disabled",
                     lambda d: _append(d / "compose.yaml.disabled", "# edited\n")),
    "gaia-hook": ("gaia", "hooks/post_install.sh",
                  lambda d: _append(d / "hooks/post_install.sh", "id\n")),
    "gaia-entrypoint-removed": ("gaia", "docker-entrypoint.sh",
                                lambda d: (d / "docker-entrypoint.sh").unlink()),
    "mapshaper-dockerfile": ("mapshaper", "Dockerfile", lambda d: _append(d / "Dockerfile", "RUN id\n")),
    "mapshaper-nginx-conf": ("mapshaper", "nginx.conf", lambda d: _append(d / "nginx.conf", "# edited\n")),
    "mapshaper-manifest": ("mapshaper", "manifest.yaml",
                           lambda d: _append(d / "manifest.yaml", "# edited\n")),
}


def _assert_says_edited(response, recipe, changed):
    """The 400 names the local edit as the cause, not only the lost privilege."""
    lost = _lost_privileges(response)
    assert lost.startswith(f"Extension '{recipe}' no longer has its curated-library privileges "
                           f"because its installed files were edited after install "
                           f"(first difference: {changed})."), lost
    assert "library recipe changed" not in lost


@pytest.mark.parametrize("case", sorted(TAMPERING))
def test_curated_recipe_edited_after_install_is_rejected(test_client, roots, case):
    recipe, changed, tamper = TAMPERING[case]
    installed = _install(roots, recipe)
    assert _post(test_client, recipe, "disable").status_code == 200
    tamper(installed)

    response = _post(test_client, recipe, "enable")

    assert response.status_code == 400
    assert "local build" in response.json()["detail"]
    _assert_says_edited(response, recipe, changed)
    assert (installed / "compose.yaml.disabled").is_file()
    assert not (installed / "compose.yaml").exists()


def test_stopped_curated_recipe_edited_after_install_is_rejected(test_client, roots):
    installed = _install(roots, "gaia")
    _append(installed / "Dockerfile", "RUN id\n")

    response = _post(test_client, "gaia", "enable")

    assert response.status_code == 400
    assert "local build" in response.json()["detail"]
    _assert_says_edited(response, "gaia", "Dockerfile")


@pytest.mark.skipif(os.name == "nt", reason="needs POSIX file modes")
def test_installed_hook_mode_change_is_rejected(test_client, roots):
    installed = _install(roots, "gaia")
    hook = installed / "hooks/post_install.sh"
    assert hook.stat().st_mode & 0o111
    assert _post(test_client, "gaia", "disable").status_code == 200
    hook.chmod(0o644)

    response = _post(test_client, "gaia", "enable")

    assert response.status_code == 400
    _assert_says_edited(response, "gaia", "hooks/post_install.sh")


def test_linked_installed_file_is_rejected(test_client, roots, tmp_path):
    installed = _install(roots, "mapshaper")
    assert _post(test_client, "mapshaper", "disable").status_code == 200
    outside = tmp_path / "nginx.conf"
    outside.write_bytes((installed / "nginx.conf").read_bytes())
    (installed / "nginx.conf").unlink()
    try:
        (installed / "nginx.conf").symlink_to(outside)
    except OSError:
        pytest.skip("this platform cannot create symlinks")

    response = _post(test_client, "mapshaper", "enable")

    assert response.status_code == 400
    _assert_says_edited(response, "mapshaper", "nginx.conf")


def test_library_change_after_install_needs_an_update_first(test_client, roots, monkeypatch, caplog):
    """The install cannot be vouched for against a library recipe that changed
    since; updating from the library restores the curated decision."""
    library, _user = roots
    installed = _install(roots, "gaia")
    assert _post(test_client, "gaia", "disable").status_code == 200
    _append(library / "gaia" / "README.md", "\nA newer release.\n")

    with caplog.at_level(logging.WARNING, logger=ext_mod.logger.name):
        refused = _post(test_client, "gaia", "enable")
    assert refused.status_code == 400
    assert "no longer matches its curated library recipe" in caplog.text
    # The 400 names the library change as the cause, not a local edit.
    lost = _lost_privileges(refused)
    assert lost.startswith("Extension 'gaia' no longer has its curated-library privileges because "
                           "the library recipe changed since install"), lost
    assert "(first difference: README.md)" in lost
    assert "Update it from the library to restore them." in lost
    assert "edited" not in lost
    assert refused.json()["detail"].endswith("uses a local build without a verified source recipe")

    monkeypatch.setattr(ext_mod, "EXTENSION_CATALOG", [{"id": "gaia", "name": "AMD GAIA", "port": 4200}])
    updated = _post(test_client, "gaia", "update")
    assert updated.status_code == 200, updated.text
    assert (installed / "compose.yaml.disabled").is_file()

    enabled = _post(test_client, "gaia", "enable")
    assert enabled.status_code == 200, enabled.text
    assert (installed / "compose.yaml").is_file()


def test_library_change_and_local_edit_are_both_named(test_client, roots):
    library, _user = roots
    installed = _install(roots, "gaia")
    assert _post(test_client, "gaia", "disable").status_code == 200
    _append(library / "gaia" / "README.md", "\nA newer release.\n")
    _append(installed / "Dockerfile", "RUN id\n")

    response = _post(test_client, "gaia", "enable")

    assert response.status_code == 400
    lost = _lost_privileges(response)
    assert ("because the library recipe changed since install and its installed files were "
            "edited after install (first difference: Dockerfile)") in lost, lost
    assert "keeps your edited files as the rollback backup" in lost


def test_cause_without_an_install_receipt_is_not_guessed(test_client, roots):
    """A legacy install has no receipt to tell the library change from an edit."""
    installed = _install(roots, "mapshaper")
    assert _post(test_client, "mapshaper", "disable").status_code == 200
    (installed / ".ods-library-receipt.json").unlink()
    _append(installed / "nginx.conf", "# edited\n")

    response = _post(test_client, "mapshaper", "enable")

    assert response.status_code == 400
    lost = _lost_privileges(response)
    assert ("because its installed files differ from the library recipe (first difference: "
            "nginx.conf), and no install receipt shows whether the library or the installed "
            "copy changed") in lost, lost


def test_curated_recipe_that_still_matches_passes_no_bind_namespace(roots, monkeypatch):
    """Curated trust and #6718's imported-recipe bind namespace stay separate:
    the enable re-scan of a matching curated recipe is trusted and passes no
    extension_id; an imported recipe's re-scan is untrusted and passes it."""
    library, _user = roots
    installed = _install(roots, "gaia")
    compose = installed / "compose.yaml"
    calls = []
    scan = ext_mod._scan_compose_content

    def record(path, **kwargs):
        if path == compose:  # not the staging scans of the library copy
            calls.append(kwargs)
        return scan(path, **kwargs)

    monkeypatch.setattr(ext_mod, "_scan_compose_content", record)

    ext_mod._scan_installed_compose("gaia", installed, compose, is_builtin=False)
    _mark_imported(library / "gaia")
    _mark_imported(installed)
    with pytest.raises(ext_mod.HTTPException) as refused:
        ext_mod._scan_installed_compose("gaia", installed, compose, is_builtin=False)

    assert [(call["trusted"], call["extension_id"]) for call in calls] == [(True, None), (False, "gaia")]
    assert "local build" in refused.value.detail
