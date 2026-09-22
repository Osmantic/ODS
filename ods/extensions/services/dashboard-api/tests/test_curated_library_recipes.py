"""Real curated recipes must survive the actual library installation boundary."""

import json
import re
from pathlib import Path

import pytest
import yaml

from routers import extensions


ODS = Path(__file__).resolve().parents[4]
LIBRARY = ODS / "extensions/library/services"
RECIPES = sorted(path.parent for path in LIBRARY.glob("*/upstream.json"))
LEGACY_RECIPES = sorted(path.parent for path in LIBRARY.glob("*/compose.yaml")
                        if not (path.parent / "upstream.json").exists())


@pytest.mark.parametrize("recipe", LEGACY_RECIPES, ids=lambda path: path.name)
def test_existing_catalog_recipe_passes_the_same_install_boundary(recipe, tmp_path, monkeypatch):
    """An older catalog entry must not bypass the checks applied to new entries."""
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", LIBRARY)
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    destination = tmp_path / "user" / recipe.name
    with extensions._staged_library_extension(recipe.name, destination) as (staged, digest):
        assert len(digest) == 64
        manifest = yaml.safe_load((staged / "manifest.yaml").read_text(encoding="utf-8"))
        compose = yaml.safe_load((staged / "compose.yaml").read_text(encoding="utf-8"))
        assert manifest['service']['id'] == recipe.name
        assert recipe.name in compose['services']
    assert not destination.exists()


@pytest.mark.parametrize("recipe", RECIPES, ids=lambda path: path.name)
def test_curated_recipe_can_be_staged(recipe, tmp_path, monkeypatch):
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", LIBRARY)
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    destination = tmp_path / "user" / recipe.name
    with extensions._staged_library_extension(recipe.name, destination) as (staged, digest):
        assert len(digest) == 64
        assert (staged / "README.md").is_file()
        assert json.loads((staged / "upstream.json").read_text(encoding="utf-8"))["repository"]
        manifest = yaml.safe_load((staged / "manifest.yaml").read_text(encoding="utf-8"))
        compose = yaml.safe_load((staged / "compose.yaml").read_text(encoding="utf-8"))
        service = manifest["service"]
        assert service["id"] == recipe.name
        assert recipe.name in compose["services"]
        assert compose["services"][recipe.name]["healthcheck"]["test"]
        # API-only extensions must not open a broken application page.
        if service.get("external_link") is False:
            assert all(feature["launch"]["type"] == "none" for feature in manifest["features"])
    assert not staged.exists()
    assert not destination.exists()  # staging must never install/start anything


def test_curated_recipes_have_distinct_projects_and_available_ports():
    assert RECIPES, "Recipe discovery unexpectedly found nothing"
    catalog = json.loads((ODS / "config/extensions-catalog.json").read_text(encoding="utf-8"))["extensions"]
    projects = set()
    for recipe in RECIPES:
        upstream = json.loads((recipe / "upstream.json").read_text(encoding="utf-8"))
        project = upstream["repository"].lower().rstrip("/")
        assert project not in projects, f"Duplicate upstream: {project}"
        projects.add(project)
        entry = next(item for item in catalog if item["id"] == recipe.name)
        port = entry["external_port_default"]
        assert [item["id"] for item in catalog if item.get("external_port_default") == port] == [recipe.name]


def _published_defaults(spec):
    """Read declared recipe defaults, never the developer's environment."""
    def resolve(value):
        value = re.sub(r"\$\{[A-Z][A-Z0-9_]*:-([^{}]*)\}", r"\1", str(value))
        assert "$" not in value, f"Published ports need explicit defaults: {value}"
        return value

    if isinstance(spec, dict):
        published = spec.get("published")
        if published is None:
            return []  # Docker-selected host port, no reserved default
        port = resolve(published)
        protocol = spec.get("protocol", "tcp")
    else:
        address, _, protocol = resolve(spec).partition("/")
        protocol = protocol or "tcp"
        parts = address.rsplit(":", 2)
        if len(parts) == 1:
            return []  # target-only syntax, Docker chooses the host port
        port = parts[-2]
    assert protocol in {"tcp", "udp", "sctp"}
    ends = port.split("-")
    assert 1 <= len(ends) <= 2 and all(value.isdigit() for value in ends), port
    first, last = int(ends[0]), int(ends[-1])
    assert 1 <= first <= last <= 65535, port
    return [(number, protocol) for number in range(first, last + 1)]


def test_curated_companion_ports_do_not_collide_with_catalog_or_other_recipes():
    catalog = json.loads((ODS / "config/extensions-catalog.json").read_text(encoding="utf-8"))["extensions"]
    recipe_ids = {recipe.name for recipe in RECIPES}
    occupied = {}
    # Older entries expose only their primary port in the generated catalog.
    # New recipes must also reserve every companion port from their Compose file.
    for entry in catalog:
        port = entry.get("external_port_default")
        if entry["id"] not in recipe_ids and isinstance(port, int) and port > 0:
            occupied.setdefault((port, "tcp"), []).append(entry["id"])
    for recipe in RECIPES:
        compose = yaml.safe_load((recipe / "compose.yaml").read_text(encoding="utf-8"))
        for service_id, service in compose["services"].items():
            for spec in service.get("ports", []):
                for endpoint in _published_defaults(spec):
                    owner = f"{recipe.name}/{service_id}"
                    assert endpoint not in occupied, f"{owner} conflicts on {endpoint} with {occupied[endpoint]}"
                    occupied[endpoint] = [owner]


@pytest.mark.parametrize("spec,expected", [
    ("${BIND_ADDRESS:-127.0.0.1}:${PRESIDIO_ANONYMIZER_PORT:-11021}:3000", [(11021, "tcp")]),
    ("[::1]:11021:3000/udp", [(11021, "udp")]),
    ({"published": "${APP_PORT:-11028}", "target": 3000}, [(11028, "tcp")]),
    ("11028-11029:3000-3001", [(11028, "tcp"), (11029, "tcp")]),
    ("3000", []),
])
def test_published_port_defaults_cover_companions_and_compose_forms(spec, expected):
    assert _published_defaults(spec) == expected
