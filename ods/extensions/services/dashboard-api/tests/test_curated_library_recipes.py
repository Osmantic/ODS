"""Real curated recipes must survive the actual library installation boundary."""

import json
import re
import copy
from pathlib import Path

import pytest
import yaml

from routers import extensions


ODS = Path(__file__).resolve().parents[4]
LIBRARY = ODS / "extensions/library/services"
RECIPES = sorted(path.parent for path in LIBRARY.glob("*/upstream.json"))
DEPLOYABLE_RECIPES = [path for path in RECIPES if (path / "compose.yaml").is_file()]
REFERENCE_RECIPES = [path for path in RECIPES if path not in DEPLOYABLE_RECIPES]
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


def _assert_runtime_contract(service, definition):
    # Portless one-shot CLIs have no daemon for a healthcheck to monitor. An
    # explicit CLI contract must not exempt network services from their probes.
    if (service.get("startup_check") is False and service.get("port") == 0
            and service.get("external_port_default") == 0):
        assert service.get("external_link") is False
        assert not definition.get("ports")
        assert definition.get("restart") == "no"
        command = definition.get("command")
        assert isinstance(command, list) and command
        assert all(isinstance(argument, str) and argument.strip() for argument in command)
    else:
        assert definition.get("healthcheck", {}).get("test"), "Persistent services require a healthcheck"


@pytest.mark.parametrize("recipe", DEPLOYABLE_RECIPES, ids=lambda path: path.name)
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
        _assert_runtime_contract(service, compose["services"][recipe.name])
        # API-only extensions must not open a broken application page.
        if service.get("external_link") is False:
            assert all(feature["launch"]["type"] == "none" for feature in manifest["features"])
    assert not staged.exists()
    assert not destination.exists()  # staging must never install/start anything


@pytest.mark.parametrize("recipe", REFERENCE_RECIPES, ids=lambda path: path.name)
def test_reference_only_recipe_is_rejected_without_installing(recipe, tmp_path, monkeypatch):
    assert any((recipe / name).is_file() for name in ("compose.yaml.disabled", "compose.yaml.reference")), \
        "Missing deployable compose must not silently classify a broken recipe as reference material"
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", LIBRARY)
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    destination = tmp_path / "user" / recipe.name
    with pytest.raises(extensions.HTTPException) as caught:
        with extensions._staged_library_extension(recipe.name, destination):
            pytest.fail("Reference-only recipe reached the install boundary")
    assert caught.value.status_code == 400
    assert "no deployable compose.yaml" in caught.value.detail
    assert not destination.exists()
    catalog = json.loads((ODS / "config/extensions-catalog.json").read_text(encoding="utf-8"))["extensions"]
    assert recipe.name not in {entry["id"] for entry in catalog}


@pytest.mark.parametrize("change", ["command", "restart", "startup_check", "published_port"])
def test_cli_contract_does_not_hide_a_broken_runtime(change):
    recipe = LIBRARY / "aider"
    service = copy.deepcopy(yaml.safe_load((recipe / "manifest.yaml").read_text(encoding="utf-8"))["service"])
    definition = copy.deepcopy(yaml.safe_load((recipe / "compose.yaml").read_text(encoding="utf-8"))["services"]["aider"])
    _assert_runtime_contract(service, definition)
    if change == "command":
        definition["command"] = []
    elif change == "restart":
        definition["restart"] = "unless-stopped"
    elif change == "startup_check":
        service["startup_check"] = True
    else:
        definition["ports"] = ["12345:12345"]
    with pytest.raises(AssertionError):
        _assert_runtime_contract(service, definition)


def test_network_service_cannot_skip_healthcheck_with_startup_check_false():
    with pytest.raises(AssertionError, match="healthcheck"):
        _assert_runtime_contract({"startup_check": False, "port": 8080, "external_port_default": 8080},
                                 {"restart": "unless-stopped", "command": ["server"]})


def _assert_primary_port_available(catalog, recipe_id):
    entry = next(item for item in catalog if item["id"] == recipe_id)
    port = entry["external_port_default"]
    assert type(port) is int and 0 <= port <= 65535
    if port == 0:
        return  # Explicit no-published-port sentinel; it reserves no socket.
    assert [item["id"] for item in catalog if item.get("external_port_default") == port] == [recipe_id], \
        f"Published port {port} collides for {recipe_id}"


def test_curated_recipes_have_distinct_projects_and_available_ports():
    assert DEPLOYABLE_RECIPES, "Recipe discovery unexpectedly found nothing"
    catalog = json.loads((ODS / "config/extensions-catalog.json").read_text(encoding="utf-8"))["extensions"]
    projects = set()
    for recipe in DEPLOYABLE_RECIPES:
        upstream = json.loads((recipe / "upstream.json").read_text(encoding="utf-8"))
        project = upstream["repository"].lower().rstrip("/")
        assert project not in projects, f"Duplicate upstream: {project}"
        projects.add(project)
        _assert_primary_port_available(catalog, recipe.name)


def test_zero_port_is_unreserved_but_duplicate_positive_port_is_rejected():
    catalog = [{"id": name, "external_port_default": 0} for name in ("first", "second")]
    _assert_primary_port_available(catalog, "first")
    for entry in catalog:
        entry["external_port_default"] = 12345
    with pytest.raises(AssertionError, match="collides"):
        _assert_primary_port_available(catalog, "first")


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


def _assert_companion_ports_available(catalog, recipes):
    recipe_ids = {recipe.name for recipe in recipes}
    occupied = {}
    # Older entries expose only their primary port in the generated catalog.
    # New recipes must also reserve every companion port from their Compose file.
    for entry in catalog:
        port = entry.get("external_port_default")
        if entry["id"] not in recipe_ids and isinstance(port, int) and port > 0:
            occupied.setdefault((port, "tcp"), []).append(entry["id"])
    for recipe in recipes:
        compose = yaml.safe_load((recipe / "compose.yaml").read_text(encoding="utf-8"))
        for service_id, service in compose["services"].items():
            for spec in service.get("ports", []):
                for endpoint in _published_defaults(spec):
                    owner = f"{recipe.name}/{service_id}"
                    assert endpoint not in occupied, f"{owner} conflicts on {endpoint} with {occupied[endpoint]}"
                    occupied[endpoint] = [owner]


def test_curated_companion_ports_do_not_collide_with_catalog_or_other_recipes():
    catalog = json.loads((ODS / "config/extensions-catalog.json").read_text(encoding="utf-8"))["extensions"]
    _assert_companion_ports_available(catalog, DEPLOYABLE_RECIPES)


@pytest.mark.parametrize("conflict", ["catalog", "other_recipe", "same_recipe_companion"])
def test_companion_port_conflicts_are_still_rejected(tmp_path, conflict):
    recipe = tmp_path / "first"
    recipe.mkdir()
    definition = {"services": {"first": {"ports": ["12345:8080"]}}}
    recipes, catalog = [recipe], []
    if conflict == "catalog":
        catalog = [{"id": "legacy", "external_port_default": 12345}]
    elif conflict == "other_recipe":
        other = tmp_path / "second"
        other.mkdir()
        (other / "compose.yaml").write_text(json.dumps({"services": {"companion": {"ports": ["12345:9090"]}}}))
        recipes.append(other)
    else:
        definition["services"]["companion"] = {"ports": ["12345:9090"]}
    (recipe / "compose.yaml").write_text(json.dumps(definition))
    with pytest.raises(AssertionError, match="conflicts"):
        _assert_companion_ports_available(catalog, recipes)


@pytest.mark.parametrize("spec,expected", [
    ("${BIND_ADDRESS:-127.0.0.1}:${PRESIDIO_ANONYMIZER_PORT:-11021}:3000", [(11021, "tcp")]),
    ("[::1]:11021:3000/udp", [(11021, "udp")]),
    ({"published": "${APP_PORT:-11028}", "target": 3000}, [(11028, "tcp")]),
    ("11028-11029:3000-3001", [(11028, "tcp"), (11029, "tcp")]),
    ("3000", []),
])
def test_published_port_defaults_cover_companions_and_compose_forms(spec, expected):
    assert _published_defaults(spec) == expected
