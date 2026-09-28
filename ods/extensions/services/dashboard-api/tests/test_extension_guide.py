"""How an installed extension is opened and used: manifest-derived guide facts."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from extension_guide import (
    docs_url, guide, host_port, https_url, internal_url, setting_role, ui_path, usage_kind,
)
from models import ServiceStatus
from user_extensions import _reset_cache, scan_user_extension_services

LIBRARY = Path(__file__).resolve().parents[3] / "library" / "services"
CATALOG = Path(__file__).resolve().parents[4] / "config" / "extensions-catalog.json"
SECRET_VALUE = "do-not-print-3f9a1c"


def _no_env(_key):
    return ""


# --- Pure manifest facts ---


@pytest.mark.parametrize("service, features, expected", [
    ({"port": 8080}, [{"launch": {"type": "service", "path": "/"}}], "web"),
    ({"port": 8080}, [{"launch": {"type": "internal"}}], "web"),
    ({"port": 8000}, [{"launch": {"type": "none"}}], "api"),
    ({"port": 8000, "external_link": False}, [{"launch": {"type": "service"}}], "api"),
    # Without declarations the schema default applies: a dashboard quick link.
    ({"port": 3000}, [], "web"),
    ({"port": 3000}, None, "web"),
    ({"port": 0}, [{"launch": {"type": "service"}}], "none"),
    ({}, [], "none"),
])
def test_usage_kind_follows_manifest_declarations(service, features, expected):
    assert usage_kind(service, features) == expected


@pytest.mark.parametrize("value, expected", [
    ("/", "/"), ("/dashboard", "/dashboard"), ("/_utils/", "/_utils/"), ("/index.php/admin", "/index.php/admin"),
    ("//evil.example", "/"), ("/../etc", "/"), ("http://x", "/"), ("/a?b", "/"), ("/#/", "/"), (None, "/"), (3, "/"),
])
def test_ui_path_accepts_only_plain_paths(value, expected):
    assert ui_path({"ui_path": value}) == expected


@pytest.mark.parametrize("value, expected", [
    ("https://github.com/gchq/CyberChef", "https://github.com/gchq/CyberChef"),
    ("https://docs.example.test:8443/guide#install", "https://docs.example.test:8443/guide#install"),
    ("http://github.com/x", None),
    ("https://user:pass@example.test/", None),
    ("https://example.test/a b", None),
    ("https://example.test/\nx", None),
    ("javascript:alert(1)", None),
    ("https://", None),
    ("https://" + "a" * 600, None),
    (None, None),
])
def test_https_url_accepts_only_plain_https_links(value, expected):
    assert https_url(value) == expected


def test_docs_url_prefers_the_manifest_then_upstream_repository():
    provenance = {"repository": "https://github.com/yuzutech/kroki"}
    assert docs_url({"docs_url": "https://docs.kroki.io/"}, provenance) == "https://docs.kroki.io/"
    assert docs_url({}, provenance) == "https://github.com/yuzutech/kroki"
    assert docs_url({"docs_url": "http://insecure.example"}, provenance) == "https://github.com/yuzutech/kroki"
    assert docs_url({}, {"repository": "git@github.com:x/y.git"}) is None
    assert docs_url({}, None) is None


def test_host_port_reads_only_the_extensions_own_port_setting():
    reads = []

    def read_env(key):
        reads.append(key)
        return {"KROKI_PORT": "12072", "DASHBOARD_API_KEY": "12345"}.get(key, "")

    kroki = {"port": 8000, "external_port_default": 11072, "external_port_env": "KROKI_PORT"}
    assert host_port("kroki", kroki, read_env) == 12072
    # A setting that is not named for this extension is never read.
    foreign = {**kroki, "external_port_env": "DASHBOARD_API_KEY"}
    assert host_port("kroki", foreign, read_env) == 11072
    assert "DASHBOARD_API_KEY" not in reads
    assert host_port("kroki", kroki, lambda key: "not-a-port") == 11072
    assert host_port("kroki", kroki, lambda key: "70000") == 11072
    # A service that publishes no host port has none, whatever the setting says.
    assert host_port("kroki", {**kroki, "external_port_default": 0}, read_env) is None
    assert host_port("uptime-kuma", {"port": 3001, "external_port_env": "UPTIME_KUMA_PORT"},
                     lambda key: "11999" if key == "UPTIME_KUMA_PORT" else "") == 11999


def test_internal_url_uses_the_declared_network_host():
    assert internal_url("kroki", {"port": 8000, "default_host": "kroki"}) == "http://kroki:8000"
    assert internal_url("kroki", {"port": 8000, "default_host": "evil host/x"}) == "http://kroki:8000"
    assert internal_url("kroki", {"port": 0}) is None


@pytest.mark.parametrize("key, role", [
    ("GOTIFY_ADMIN_PASSWORD", "sign_in"),
    ("KANBOARD_INITIAL_PASSWORD", "sign_in"),
    ("MAILPIT_UI_AUTH", "sign_in"),
    ("MARIMO_TOKEN", "sign_in"),
    ("WALLABAG_INITIAL_EMAIL", "sign_in"),
    ("HUGINN_ADMIN_USER", "sign_in"),
    ("SHLINK_DB_PASSWORD", "internal"),
    ("KESTRA_DATABASE_PASSWORD", "internal"),
    ("GRAFANA_SECRET_KEY", "internal"),
    ("WAKAPI_PASSWORD_SALT", "internal"),
    ("HOMEBOX_API_KEY_PEPPER", "internal"),
    ("SHLINK_API_KEY", "api_key"),
    ("MEILISEARCH_MASTER_KEY", "api_key"),
    ("BOOKSTACK_URL", "setting"),
])
def test_setting_role_hints(key, role):
    assert setting_role(key) == role


def test_guide_reports_setting_presence_never_values():
    fields = [
        {"key": "GOTIFY_ADMIN_PASSWORD", "required": True, "secret": True, "configured": True,
         "description": "Initial administrator password.", "format": None},
        {"key": "GOTIFY_DB_PASSWORD", "required": False, "secret": True, "configured": False,
         "description": "", "format": None},
    ]
    service = {"id": "gotify", "port": 80, "external_port_default": 11040, "ui_path": "/",
               "external_port_env": "GOTIFY_PORT", "default_host": "gotify"}
    result = guide("gotify", service, [{"launch": {"type": "service", "path": "/"}}],
                   {"repository": "https://github.com/gotify/server"}, fields, lambda key: SECRET_VALUE)
    assert SECRET_VALUE not in json.dumps(result)
    assert result == {
        "schemaVersion": 1, "kind": "web", "uiPath": "/", "hostPort": 11040,
        "internalUrl": "http://gotify:80", "docsUrl": "https://github.com/gotify/server",
        "settings": [
            {"key": "GOTIFY_ADMIN_PASSWORD", "description": "Initial administrator password.", "secret": True,
             "required": True, "configured": True, "role": "sign_in"},
            {"key": "GOTIFY_DB_PASSWORD", "description": "", "secret": True,
             "required": False, "configured": False, "role": "internal"},
        ],
    }


# --- Curated library: generated, not hand-typed ---


def _library_recipes():
    for directory in sorted(LIBRARY.iterdir()):
        manifest = directory / "manifest.yaml"
        if manifest.is_file():
            yield directory, yaml.safe_load(manifest.read_text(encoding="utf-8"))


def test_curated_recipes_with_provenance_link_their_upstream_project():
    for directory, manifest in _library_recipes():
        provenance_path = directory / "upstream.json"
        if not provenance_path.is_file():
            continue
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        assert docs_url(manifest["service"], provenance), directory.name


def test_curated_recipes_without_a_page_are_never_offered_as_applications():
    for directory, manifest in _library_recipes():
        service = manifest["service"]
        if service.get("external_link") is False:
            assert usage_kind(service, manifest.get("features")) in {"api", "none"}, directory.name


def test_shipped_catalog_carries_how_to_open_each_recipe():
    entries = {entry["id"]: entry for entry in json.loads(CATALOG.read_text(encoding="utf-8"))["extensions"]}
    assert entries["cyberchef"]["ui_path"] == "/"
    assert entries["cyberchef"]["docs_url"] == "https://github.com/gchq/CyberChef"
    assert usage_kind(entries["cyberchef"], entries["cyberchef"]["features"]) == "web"
    assert entries["kroki"]["external_link"] is False
    assert usage_kind(entries["kroki"], entries["kroki"]["features"]) == "api"
    assert usage_kind(entries["uptime-kuma"], entries["uptime-kuma"]["features"]) == "web"


# --- Installed extensions: scan, catalog, detail and Applications ---


def _install(user_dir, service_id, service, *, features=None, enabled=True, readme=None, provenance=None):
    directory = user_dir / service_id
    directory.mkdir(parents=True)
    (directory / ("compose.yaml" if enabled else "compose.yaml.disabled")).write_text(
        "services:\n  svc:\n    image: test:latest\n", encoding="utf-8")
    (directory / "manifest.yaml").write_text(yaml.safe_dump({
        "schema_version": "ods.services.v1",
        "service": {"id": service_id, "name": service_id.title(), "compose_file": "compose.yaml", **service},
        "features": features or [],
    }), encoding="utf-8")
    if readme is not None:
        (directory / "README.md").write_text(readme, encoding="utf-8")
    if provenance is not None:
        (directory / "upstream.json").write_text(json.dumps(provenance), encoding="utf-8")
    return directory


WEB = [{"id": "ui", "name": "UI", "description": "d", "icon": "Box", "category": "tools",
        "requirements": {}, "priority": 1, "launch": {"type": "service", "path": "/"}}]
API = [{**WEB[0], "launch": {"type": "none"}}]


@pytest.fixture()
def extension_roots(monkeypatch, tmp_path):
    user_dir, library_dir, builtin_dir = tmp_path / "user", tmp_path / "lib", tmp_path / "builtin"
    for directory in (user_dir, library_dir, builtin_dir):
        directory.mkdir()
    monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)
    monkeypatch.setattr("routers.extensions.EXTENSIONS_LIBRARY_DIR", library_dir)
    monkeypatch.setattr("routers.extensions.EXTENSIONS_DIR", builtin_dir)
    monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("routers.extensions.SERVICES", {})
    monkeypatch.setattr("routers.extensions.GPU_BACKEND", "nvidia")
    _reset_cache()
    yield user_dir
    _reset_cache()


def test_scan_records_kind_page_and_moved_port(extension_roots, monkeypatch):
    user_dir = extension_roots
    _install(user_dir, "uptime-kuma", {"port": 3001, "external_port_default": 11027,
                                       "external_port_env": "UPTIME_KUMA_PORT", "health": "/",
                                       "ui_path": "/dashboard"}, features=WEB)
    _install(user_dir, "kroki", {"port": 8000, "external_port_default": 11072, "health": "/health",
                                 "external_link": False}, features=API)
    monkeypatch.setenv("UPTIME_KUMA_PORT", "12027")
    services = scan_user_extension_services(user_dir)
    assert services["uptime-kuma"]["kind"] == "web"
    assert services["uptime-kuma"]["ui_path"] == "/dashboard"
    assert services["uptime-kuma"]["external_port"] == 12027
    assert services["kroki"]["kind"] == "api"
    assert services["kroki"]["external_port"] == 11072


def test_catalog_says_how_each_extension_opens(test_client, extension_roots, monkeypatch):
    user_dir = extension_roots
    _install(user_dir, "uptime-kuma", {"port": 3001, "external_port_default": 11027,
                                       "external_port_env": "UPTIME_KUMA_PORT", "health": "/"}, features=WEB)
    monkeypatch.setenv("UPTIME_KUMA_PORT", "12027")
    catalog = [
        {"id": "uptime-kuma", "name": "Uptime Kuma", "port": 3001, "external_port_default": 11027,
         "ui_path": "/", "features": WEB, "gpu_backends": ["all"], "depends_on": []},
        {"id": "kroki", "name": "Kroki", "port": 8000, "external_port_default": 11072,
         "external_link": False, "features": API, "gpu_backends": ["all"], "depends_on": []},
    ]
    monkeypatch.setattr("routers.extensions.EXTENSION_CATALOG", catalog)
    healthy = ServiceStatus(id="uptime-kuma", name="Uptime Kuma", port=3001, external_port=12027, status="healthy")
    with (patch("helpers.get_cached_services", return_value=[]),
          patch("helpers.check_service_health", new_callable=AsyncMock, return_value=healthy)):
        response = test_client.get("/api/extensions/catalog", headers=test_client.auth_headers)
    assert response.status_code == 200
    entries = {entry["id"]: entry for entry in response.json()["extensions"]}
    assert entries["uptime-kuma"]["status"] == "enabled"
    assert entries["uptime-kuma"]["usage_kind"] == "web"
    assert entries["uptime-kuma"]["external_port"] == 12027
    assert entries["kroki"]["usage_kind"] == "api"
    assert "external_port" not in entries["kroki"]


def test_detail_guide_describes_use_without_setting_values(test_client, extension_roots, monkeypatch):
    user_dir = extension_roots
    _install(user_dir, "gotify", {
        "port": 80, "external_port_default": 11040, "health": "/health", "ui_path": "/",
        "default_host": "gotify", "description": "Push notifications.",
        "env_vars": [
            {"key": "GOTIFY_ADMIN_PASSWORD", "secret": True, "required": True,
             "description": "Initial administrator password."},
            {"key": "GOTIFY_TIMEZONE", "required": False, "description": "Time zone."},
        ],
    }, features=WEB, readme="# Gotify\n\nSign in as **admin**.\n",
        provenance={"repository": "https://github.com/gotify/server", "license": "MIT"})
    monkeypatch.setenv("GOTIFY_ADMIN_PASSWORD", SECRET_VALUE)
    monkeypatch.setattr("routers.extensions.EXTENSION_CATALOG", [
        {"id": "gotify", "name": "Gotify", "port": 80, "external_port_default": 11040,
         "features": WEB, "gpu_backends": ["all"], "depends_on": []},
        {"id": "notifier", "name": "Notifier", "port": 9000, "features": [],
         "gpu_backends": ["all"], "depends_on": ["gotify"]},
    ])
    with (patch("helpers.get_cached_services", return_value=[]),
          patch("helpers.check_service_health", new_callable=AsyncMock,
                return_value=ServiceStatus(id="gotify", name="Gotify", port=80, external_port=11040,
                                           status="healthy"))):
        response = test_client.get("/api/extensions/gotify", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert SECRET_VALUE not in response.text
    body = response.json()
    assert body["dependents"] == ["notifier"]
    assert body["integration"]["documentation"].startswith("# Gotify")
    guide_body = body["guide"]
    assert guide_body["kind"] == "web"
    assert guide_body["hostPort"] == 11040
    assert guide_body["internalUrl"] == "http://gotify:80"
    assert guide_body["docsUrl"] == "https://github.com/gotify/server"
    assert guide_body["settings"] == [
        {"key": "GOTIFY_ADMIN_PASSWORD", "description": "Initial administrator password.", "secret": True,
         "required": True, "configured": True, "role": "sign_in"},
        {"key": "GOTIFY_TIMEZONE", "description": "Time zone.", "secret": False,
         "required": False, "configured": False, "role": "setting"},
    ]


def test_applications_list_installed_extensions_that_have_a_page(test_client, extension_roots, monkeypatch):
    user_dir = extension_roots
    _install(user_dir, "uptime-kuma", {"port": 3001, "external_port_default": 11027, "health": "/"},
             features=WEB)
    _install(user_dir, "cyberchef", {"port": 8080, "external_port_default": 11028, "health": "/",
                                     "ui_path": "/"}, features=WEB)
    _install(user_dir, "kroki", {"port": 8000, "external_port_default": 11072, "health": "/health",
                                 "external_link": False}, features=API)
    _install(user_dir, "freshrss", {"port": 80, "external_port_default": 11030, "health": "/"},
             features=WEB, enabled=False)
    _install(user_dir, "hidden", {"port": 80, "external_port_default": 0, "health": "/"}, features=WEB)
    import config
    monkeypatch.setattr(config, "SERVICES", {})
    monkeypatch.setattr("main.SERVICES", config.SERVICES)

    async def health(service_id, cfg, timeout=None):
        return ServiceStatus(id=service_id, name=cfg["name"], port=cfg["port"],
                             external_port=cfg["external_port"],
                             status="healthy" if service_id == "uptime-kuma" else "down")

    with patch("helpers.check_service_health", side_effect=health):
        response = test_client.get("/api/external-links", headers=test_client.auth_headers)
    assert response.status_code == 200
    links = {link["id"]: link for link in response.json()}
    assert set(links) == {"uptime-kuma", "cyberchef"}
    assert links["uptime-kuma"] == {
        "id": "uptime-kuma", "label": "Uptime-Kuma", "port": 11027, "ui_path": "/", "public_url": "",
        "icon": "ExternalLink", "healthNeedles": [], "source": "extension", "status": "healthy",
    }
    assert links["cyberchef"]["status"] == "down"


def test_applications_keep_built_in_services_listed_once(test_client, extension_roots, monkeypatch):
    user_dir = extension_roots
    _install(user_dir, "n8n", {"port": 5678, "external_port_default": 5678, "health": "/healthz"}, features=WEB)
    services = {"n8n": {"name": "n8n", "port": 5678, "external_port": 5678, "health": "/healthz", "host": "n8n"}}
    import config
    monkeypatch.setattr(config, "SERVICES", services)
    monkeypatch.setattr("main.SERVICES", services)
    monkeypatch.setattr("routers.extensions.SERVICES", services)
    with patch("helpers.check_service_health", new_callable=AsyncMock) as health:
        response = test_client.get("/api/external-links", headers=test_client.auth_headers)
    assert [link["id"] for link in response.json()] == ["n8n"]
    assert "source" not in response.json()[0]
    health.assert_not_awaited()
