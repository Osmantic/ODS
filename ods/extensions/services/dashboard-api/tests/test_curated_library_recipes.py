"""Real curated recipes must survive the actual library installation boundary."""

import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
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


def _recipe_files(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file() and not path.is_symlink()}


@pytest.mark.parametrize("recipe", sorted(path.parent for path in LIBRARY.glob("*/compose.yaml")),
                         ids=lambda path: path.name)
def test_library_staging_keeps_every_recipe_file(recipe, tmp_path, monkeypatch):
    """The staged copy is the image build context, so no shipped file may be left out.

    Dockerfiles COPY recipe files such as README.md (mapshaper, blockbench)
    and read .dockerignore; only compose.yaml's build context is rewritten.
    """
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", LIBRARY)
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    shipped = _recipe_files(recipe)
    with extensions._staged_library_extension(recipe.name, tmp_path / "user" / recipe.name) as (staged, _):
        installed = _recipe_files(staged)
    assert sorted(installed) == sorted(shipped)
    changed = sorted(path for path in shipped if installed[path] != shipped[path])
    assert changed in ([], ["compose.yaml"])


def test_library_staging_copies_nested_and_dot_files_but_not_links(tmp_path, monkeypatch):
    """Unit test of the staging copy: every regular file survives; links do not."""
    library = tmp_path / "library"
    recipe = library / "mapshaper"
    shutil.copytree(LIBRARY / "mapshaper", recipe)
    extra = {
        "docs/notes.md": b"nested docs\n",
        "tests/fixture.json": b"{}\n",
        "examples/sample.geojson": b"{}\n",
        ".gitignore": b"*.tmp\n",
        "assets/deep/README.md": b"deep\n",
    }
    for relative, content in extra.items():
        (recipe / relative).parent.mkdir(parents=True, exist_ok=True)
        (recipe / relative).write_bytes(content)
    try:
        (recipe / "linked.md").symlink_to(recipe / "README.md")
    except OSError:
        pass  # Unprivileged Windows cannot create links; the copy is still checked.
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", library)
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    with extensions._staged_library_extension("mapshaper", tmp_path / "user" / "mapshaper") as (staged, _):
        installed = _recipe_files(staged)
        assert not (staged / "linked.md").exists()
        compose = yaml.safe_load((staged / "compose.yaml").read_text(encoding="utf-8"))
        context = Path(compose["services"]["mapshaper"]["build"]["context"])
        assert context == (tmp_path / "user" / "mapshaper").resolve()
    assert sorted(installed) == sorted(_recipe_files(recipe))
    for relative in (*extra, "README.md", ".dockerignore", "Dockerfile"):
        assert installed[relative] == (recipe / relative).read_bytes()


RESOLVER = ODS / "scripts/resolve-compose-stack.sh"
INSTALLABLE = sorted(path.parent for path in LIBRARY.glob("*/compose.yaml"))


def _install_root(tmp_path, monkeypatch):
    """An install root whose user-extensions are written by the real library install."""
    root = tmp_path / "ods"
    (root / "config").mkdir(parents=True)
    shutil.copy2(ODS / "config/core-service-ids.json", root / "config/core-service-ids.json")
    (root / "docker-compose.base.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", LIBRARY)
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", root / "data/user-extensions")
    return root


def _resolver_scan(root):
    """The compose resolver's own user-extension scan (Python inside the Bash script)."""
    source = RESOLVER.read_text(encoding="utf-8")
    start = source.index("_LOOPBACK_VAR_DEFAULT_RE = re.compile(")
    end = source.index("def _extension_base_path(", start)
    namespace = {"script_dir": root, "pathlib": pathlib, "re": re, "os": os, "json": json, "yaml": yaml}
    exec(compile(source[start:end], str(RESOLVER), "exec"), namespace)
    return namespace["_scan_user_compose_content"], namespace["_library_recipe_trusted"]


# The resolver never grants user extensions accelerator devices, so it drops
# these overlays (the service still starts, without the device). That policy
# is separate from install success; any other overlay rejection is a failure.
_DEVICE_OVERLAY_REJECTION = re.compile(
    r"service '[^']+' (requests GPU passthrough via deploy\.resources\.reservations\.devices"
    r"|declares devices)$")


@pytest.mark.parametrize("recipe", INSTALLABLE, ids=lambda path: path.name)
def test_installed_library_recipe_passes_the_compose_resolver(recipe, tmp_path, monkeypatch):
    """dashboard-api accepting a recipe is not enough: every `ods` command and the
    host agent's install build resolve the stack through resolve-compose-stack.sh,
    which drops a user extension whose compose its own scan rejects (gaia's
    extra_hosts made the install fail with "Invalid installation Compose
    dependency graph")."""
    root = _install_root(tmp_path, monkeypatch)
    extensions._install_from_library(recipe.name)
    installed = root / "data/user-extensions" / recipe.name
    scan, trusted = _resolver_scan(root)
    library_trust = trusted(installed)
    ok, warnings = scan(installed / "compose.yaml", library_trust)
    assert ok and not warnings, f"compose.yaml: {warnings}"
    for overlay in sorted(installed.glob("compose.*.yaml")):
        ok, warnings = scan(overlay, library_trust)
        unexpected = [item for item in warnings if not _DEVICE_OVERLAY_REJECTION.match(item)]
        assert not unexpected, f"{overlay.name}: {unexpected}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="the resolver is a Bash script")
@pytest.mark.parametrize("backend", ["nvidia", "amd", "cpu"])
def test_resolver_keeps_every_installed_library_recipe(backend, tmp_path, monkeypatch):
    """End to end: install every recipe, run the real resolver, and find each one."""
    root = _install_root(tmp_path, monkeypatch)
    expected = []
    for recipe in INSTALLABLE:
        extensions._install_from_library(recipe.name)
        manifest = yaml.safe_load((recipe / "manifest.yaml").read_text(encoding="utf-8"))
        backends = manifest["service"].get("gpu_backends", ["all"])
        if backend in backends or "all" in backends or "none" in backends:
            expected.append(f"data/user-extensions/{recipe.name}/compose.yaml")
    assert "data/user-extensions/gaia/compose.yaml" in expected
    env = {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
           "HOME": str(root), "ODS_MODE": "local"}
    result = subprocess.run(["bash", str(RESOLVER), "--script-dir", str(root),
                             "--gpu-backend", backend, "--tier", "1"],
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    files = shlex.split(result.stdout)[1::2]
    assert [path for path in expected if path not in files] == [], result.stderr
    unexpected = [line for line in result.stderr.splitlines()
                  if line.startswith("WARNING")
                  and not _DEVICE_OVERLAY_REJECTION.match(line.split(": ", 2)[-1])]
    assert not unexpected, result.stderr


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


# Name every image at a registry that serves it. Vanity hosts that proxy Docker
# Hub (their /v2/ challenge names realm="https://auth.docker.io/token") pull
# from one shared egress address, so all anonymous users of that host share a
# single Docker Hub pull budget, and docker.io mirrors or `docker login` do not
# apply. On 2026-09-25 docker.swagger.io and cr.weaviate.io both answered 429
# (ratelimit-source 54.184.99.3, remaining 0) while docker.io served the same
# digests, failing the swagger-ui thin build. lscr.io fronts ghcr.io, not Hub.
IMAGE_REGISTRIES = {"docker.io", "ghcr.io", "quay.io", "mcr.microsoft.com", "lscr.io"}
FROM_RE = re.compile(r"(?im)^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?")
ARG_IMAGE_RE = re.compile(r"(?im)^\s*ARG\s+\w*IMAGE\w*=(\S+)")


def _registry(reference):
    """Registry host of an image reference, following Docker's reference grammar."""
    first, _, rest = reference.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        return first.lower()
    return "docker.io"


def _image_references():
    roots = (LIBRARY, ODS / "extensions/services")
    for root in roots:
        for path in sorted(root.rglob("Dockerfile*")):
            text = path.read_text(encoding="utf-8")
            stages = {alias.lower() for _, alias in FROM_RE.findall(text) if alias}
            for base, _ in FROM_RE.findall(text):
                if not base.startswith("$") and base != "scratch" and base.lower() not in stages:
                    yield path, base
            for default in ARG_IMAGE_RE.findall(text):
                yield path, default
    composes = [*ODS.glob("docker-compose*.yml"),
                *(path for root in roots for path in root.rglob("compose*.y*ml"))]
    for path in sorted(composes):
        services = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("services") or {}
        for service in services.values():
            image = re.sub(r"^\$\{\w+:-(.*)\}$", r"\1", str((service or {}).get("image") or ""))
            if image and not image.startswith("$"):
                yield path, image
    for recipe in RECIPES:
        upstream = json.loads((recipe / "upstream.json").read_text(encoding="utf-8"))
        for key in ("image", "build_image", "runtime_image", "companion_images"):
            values = upstream.get(key) or []
            for value in [values] if isinstance(values, str) else values:
                yield recipe / "upstream.json", value


@pytest.mark.parametrize("reference,registry", [
    ("nginx:1.28-alpine@sha256:" + "a" * 64, "docker.io"),
    ("swaggerapi/swagger-ui:v5.33.0", "docker.io"),
    ("docker.swagger.io/swaggerapi/swagger-ui:v5.33.0", "docker.swagger.io"),
    ("localhost:5000/demo", "localhost:5000"),
    ("ods/swagger-ui:5.33.0-local-v1", "docker.io"),
])
def test_registry_follows_docker_reference_grammar(reference, registry):
    assert _registry(reference) == registry


def test_images_name_a_registry_that_serves_them():
    references = list(_image_references())
    assert len(references) > 200, "Image reference discovery unexpectedly found little"
    offenders = sorted(f"{path.relative_to(ODS)}: {reference}" for path, reference in references
                       if _registry(reference) not in IMAGE_REGISTRIES)
    assert not offenders, ("Name the registry of record (for Docker Hub images, docker.io/...), "
                           "not a vanity proxy host; see IMAGE_REGISTRIES:\n" + "\n".join(offenders))


def test_thin_build_base_is_the_reviewed_upstream_image():
    """A single-stage recipe build must start from exactly the image upstream.json records."""
    checked = 0
    for recipe in RECIPES:
        upstream = json.loads((recipe / "upstream.json").read_text(encoding="utf-8"))
        reviewed = {upstream.get(key) for key in ("image", "build_image", "runtime_image")}
        for dockerfile in sorted(recipe.glob("Dockerfile*")):
            bases = [base for base, _ in FROM_RE.findall(dockerfile.read_text(encoding="utf-8"))]
            if len(bases) == 1:
                assert bases[0] in reviewed, f"{dockerfile.relative_to(ODS)}: FROM {bases[0]}"
                checked += 1
    assert checked >= 70, "Thin-build discovery unexpectedly found little"


def _writable_relative_binds(service):
    """Relative bind sources, which the host agent pre-creates as its own non-root user."""
    sources = []
    for volume in service.get("volumes") or []:
        if isinstance(volume, dict):
            if volume.get("type") == "bind" and not volume.get("read_only"):
                sources.append(str(volume.get("source", "")))
            continue
        source, _, mount = str(volume).partition(":")
        if "ro" not in mount.partition(":")[2].split(","):
            sources.append(source)
    return [source for source in sources
            if "/" in source and not source.startswith(("/", "$", "~", "`", "\\"))]


def test_capability_free_root_services_do_not_write_owner_prepared_binds():
    """Root without CAP_DAC_OVERRIDE cannot create files in the owner's 0755 data dir.

    ods-host-agent pre-creates relative bind sources as the install owner and
    never chowns them. ntfy shipped as root with cap_drop ALL and crash-looped
    on SQLite creation; such services need a non-root user instead.
    """
    offenders, checked = [], 0
    for path in sorted(LIBRARY.glob("*/compose*.yaml")):
        compose = yaml.safe_load(path.read_text(encoding="utf-8"))
        manifest_path = path.parent / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        container_uid = ((manifest or {}).get("service") or {}).get("container_uid")
        services = (compose or {}).get("services") or {}
        for name, service in services.items():
            if not isinstance(service, dict):
                continue
            checked += 1
            dropped = {str(cap).upper().removeprefix("CAP_") for cap in service.get("cap_drop") or []}
            added = {str(cap).upper().removeprefix("CAP_") for cap in service.get("cap_add") or []}
            user = service.get("user")
            root = (str(user).split(":")[0] in ("0", "root") if user is not None
                    else str(container_uid or 0) == "0")
            binds = _writable_relative_binds(service)
            if root and "ALL" in dropped and "DAC_OVERRIDE" not in added and binds:
                offenders.append(f"{path.relative_to(ODS)} {name}: {', '.join(binds)}")
    assert checked > 150, "Library compose discovery unexpectedly found little"
    assert not offenders, ("Run as the install owner, e.g. user: \"${ODS_UID:-1000}:${ODS_GID:-1000}\":\n"
                           + "\n".join(offenders))
