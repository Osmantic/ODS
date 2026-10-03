"""Dependency discovery must work before a catalog recipe is installed."""

import pytest
import yaml
from fastapi import HTTPException

from routers import extensions


def roots(monkeypatch, tmp_path):
    result = []
    for name in ("USER_EXTENSIONS_DIR", "EXTENSIONS_DIR", "EXTENSIONS_LIBRARY_DIR"):
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setattr(extensions, name, path)
        result.append(path)
    monkeypatch.setattr(extensions, "ALWAYS_ON_SERVICES", set())
    return result


def recipe(root, name, deps, *, enabled=False):
    directory = root / name
    directory.mkdir()
    (directory / "manifest.yaml").write_text(
        yaml.safe_dump({"service": {"id": name, "depends_on": deps}}), encoding="utf-8",
    )
    if enabled:
        (directory / "compose.yaml").write_text("services: {}\n", encoding="utf-8")


def test_uninstalled_library_tree_is_discovered_but_not_satisfied(monkeypatch, tmp_path):
    user, builtin, library = roots(monkeypatch, tmp_path)
    recipe(library, "app", ["worker"])
    recipe(library, "worker", ["database"], enabled=True)
    recipe(library, "database", [], enabled=True)
    assert extensions._read_direct_deps("app") == ["worker"]
    assert extensions._get_missing_deps_transitive("app") == ["database", "worker"]
    assert not extensions._is_dep_satisfied("worker")


def test_installed_definition_shadows_library_dependencies(monkeypatch, tmp_path):
    user, builtin, library = roots(monkeypatch, tmp_path)
    recipe(library, "app", ["unused"])
    recipe(builtin, "app", ["native-db"])
    assert extensions._read_direct_deps("app") == ["native-db"]
    recipe(user, "app", ["custom-db"])
    assert extensions._read_direct_deps("app") == ["custom-db"]


def test_incomplete_user_install_does_not_fall_back_to_library(monkeypatch, tmp_path):
    user, builtin, library = roots(monkeypatch, tmp_path)
    recipe(library, "app", ["unused"])
    (user / "app").mkdir()
    assert extensions._read_direct_deps("app") == []


def test_library_dependency_cycle_is_rejected_before_install(monkeypatch, tmp_path):
    user, builtin, library = roots(monkeypatch, tmp_path)
    recipe(library, "app", ["worker"])
    recipe(library, "worker", ["app"])
    with pytest.raises(HTTPException, match="Circular dependency"):
        extensions._get_missing_deps_transitive("app")


@pytest.mark.parametrize("declaration", [None, "database", {}, [1], ["../database"], ["database\n"]])
def test_malformed_dependencies_are_not_reported_as_empty(monkeypatch, tmp_path, declaration):
    _, _, library = roots(monkeypatch, tmp_path)
    recipe(library, "app", declaration)
    with pytest.raises(HTTPException, match="Invalid dependency manifest") as error:
        extensions._get_missing_deps_transitive("app")
    assert error.value.status_code == 400


@pytest.mark.parametrize("contents", [b"service: [", b"[]", b"service: null", b"\xff"])
def test_unreadable_manifest_blocks_dependency_resolution(monkeypatch, tmp_path, contents):
    _, _, library = roots(monkeypatch, tmp_path)
    recipe(library, "app", [])
    (library / "app" / "manifest.yaml").write_bytes(contents)
    with pytest.raises(HTTPException, match="Invalid dependency manifest"):
        extensions._get_missing_deps_transitive("app")


def test_enabled_shared_subtree_is_read_once(monkeypatch, tmp_path):
    user, _, library = roots(monkeypatch, tmp_path)
    recipe(library, "app", ["left", "right"])
    recipe(user, "left", ["shared"], enabled=True)
    recipe(user, "right", ["shared"], enabled=True)
    recipe(user, "shared", ["database"], enabled=True)
    recipe(library, "database", [])
    read = extensions._read_direct_deps
    calls = []

    def recording(service_id):
        calls.append(service_id)
        return read(service_id)

    monkeypatch.setattr(extensions, "_read_direct_deps", recording)
    assert extensions._get_missing_deps_transitive("app") == ["database"]
    assert calls.count("shared") == 1


def test_invalid_installed_manifest_does_not_fall_back(monkeypatch, tmp_path):
    user, _, library = roots(monkeypatch, tmp_path)
    recipe(library, "app", [])
    recipe(user, "app", "database")
    with pytest.raises(HTTPException, match="Invalid dependency manifest"):
        extensions._read_direct_deps("app")
