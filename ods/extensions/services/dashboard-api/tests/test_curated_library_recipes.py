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
from fastapi import HTTPException

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
    # A resolvable core stack (base + backend overlay) declaring the core
    # service curated recipes wait on (continue, localai). The resolver drops
    # an extension that needs a service no resolved file declares, because
    # Compose would refuse the whole project.
    (root / "docker-compose.base.yml").write_text(
        "services:\n  llama-server:\n    image: example:llama-server\n", encoding="utf-8")
    for backend in ("nvidia", "amd", "cpu"):
        (root / f"docker-compose.{backend}.yml").write_text("services: {}\n", encoding="utf-8")
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


def _accelerator(compose_name):
    """What both callers pass: compose.nvidia.yaml / compose.amd.yaml name their
    backend (the resolver loads them as compose.<backend>.yaml); others none."""
    return extensions._LIBRARY_ACCELERATOR_OVERLAYS.get(compose_name)


# Curated recipes whose backend overlay reserves the accelerator. The resolver
# used to drop exactly these overlays, so the services started without a GPU.
NVIDIA_GPU_RECIPES = ["audiocraft", "bark", "forge", "frigate", "invokeai", "ollama",
                      "rvc", "text-generation-webui", "xtts"]
AMD_GPU_RECIPES = ["invokeai", "ollama", "rvc", "text-generation-webui", "xtts"]


def _requests_accelerator(service):
    reservations = ((service.get("deploy") or {}).get("resources") or {}).get("reservations") or {}
    return bool(service.get("devices") or reservations.get("devices"))


@pytest.mark.parametrize("backend, recipes", [("nvidia", NVIDIA_GPU_RECIPES), ("amd", AMD_GPU_RECIPES)])
def test_gpu_recipe_inventory_is_complete(backend, recipes):
    """Every accelerator overlay in the library is named, so the checks below cover it."""
    found = []
    for recipe in INSTALLABLE:
        overlay = recipe / f"compose.{backend}.yaml"
        if overlay.is_file():
            services = yaml.safe_load(overlay.read_text(encoding="utf-8"))["services"]
            if any(_requests_accelerator(service) for service in services.values()):
                found.append(recipe.name)
    assert found == recipes


@pytest.mark.parametrize("recipe", INSTALLABLE, ids=lambda path: path.name)
def test_installed_library_recipe_passes_the_compose_resolver(recipe, tmp_path, monkeypatch):
    """dashboard-api accepting a recipe is not enough: every `ods` command and the
    host agent's install build resolve the stack through resolve-compose-stack.sh,
    which drops a user extension whose compose its own scan rejects (gaia's
    extra_hosts made the install fail with "Invalid installation Compose
    dependency graph"; GPU overlays were dropped, so services ran without the GPU).
    No file of any curated recipe may be rejected."""
    root = _install_root(tmp_path, monkeypatch)
    extensions._install_from_library(recipe.name)
    installed = root / "data/user-extensions" / recipe.name
    scan, trusted = _resolver_scan(root)
    library_trust = trusted(installed)
    for compose in [installed / "compose.yaml", *sorted(installed.glob("compose.*.yaml"))]:
        ok, warnings = scan(compose, library_trust, _accelerator(compose.name))
        assert ok and not warnings, f"{compose.name}: {warnings}"


def _resolve(root, backend):
    env = {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
           "HOME": str(root), "ODS_MODE": "local"}
    result = subprocess.run(["bash", str(RESOLVER), "--script-dir", str(root),
                             "--gpu-backend", backend, "--tier", "1"],
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return shlex.split(result.stdout)[1::2], result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="the resolver is a Bash script")
@pytest.mark.parametrize("backend", ["nvidia", "amd", "cpu"])
def test_resolver_keeps_every_installed_library_recipe(backend, tmp_path, monkeypatch):
    """End to end: install every recipe, run the real resolver, and find each
    one together with its backend overlay, without any rejection."""
    root = _install_root(tmp_path, monkeypatch)
    expected = []
    for recipe in INSTALLABLE:
        extensions._install_from_library(recipe.name)
        manifest = yaml.safe_load((recipe / "manifest.yaml").read_text(encoding="utf-8"))
        backends = manifest["service"].get("gpu_backends", ["all"])
        if backend in backends or "all" in backends or "none" in backends:
            expected.append(f"data/user-extensions/{recipe.name}/compose.yaml")
            if (recipe / f"compose.{backend}.yaml").is_file():
                expected.append(f"data/user-extensions/{recipe.name}/compose.{backend}.yaml")
    assert "data/user-extensions/gaia/compose.yaml" in expected
    gpu_recipes = {"nvidia": NVIDIA_GPU_RECIPES, "amd": AMD_GPU_RECIPES}.get(backend, [])
    for name in gpu_recipes:
        assert f"data/user-extensions/{name}/compose.{backend}.yaml" in expected
    files, stderr = _resolve(root, backend)
    assert [path for path in expected if path not in files] == [], stderr
    assert not [line for line in stderr.splitlines() if line.startswith("WARNING")], stderr


# The Docker CLI's own environment; nothing that interpolates recipe settings.
_DOCKER_CLI_ENV = {"PATH", "HOME", "DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT",
                   "SYSTEMROOT", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"}


def _rendered_accelerator(service, backend):
    if backend == "nvidia":
        requests = service["deploy"]["resources"]["reservations"]["devices"]
        return [(item["driver"], item["capabilities"]) for item in requests]
    # Compose prints short or long device syntax depending on its version.
    return sorted((item["source"], item["target"]) if isinstance(item, dict)
                  else tuple(item.split(":")[:2]) for item in service["devices"])


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("docker") is None,
                    reason="needs the Bash resolver and the Docker CLI")
@pytest.mark.parametrize("backend, recipes", [("nvidia", NVIDIA_GPU_RECIPES), ("amd", AMD_GPU_RECIPES)])
def test_resolved_gpu_recipes_render_their_accelerator(backend, recipes, tmp_path, monkeypatch):
    """`docker compose config` (never `up`) of the resolved stack gives each GPU
    recipe's service its backend accelerator."""
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        pytest.skip("Docker Compose v2 is unavailable")
    root = _install_root(tmp_path, monkeypatch)
    (root / f"docker-compose.{backend}.yml").write_text("services: {}\n", encoding="utf-8")
    for name in recipes:
        extensions._install_from_library(name)
    files, stderr = _resolve(root, backend)
    assert files[:2] == ["docker-compose.base.yml", f"docker-compose.{backend}.yml"], files
    assert not [line for line in stderr.splitlines() if line.startswith("WARNING")], stderr
    # Placeholders for `${NAME:?...}` settings the owner supplies at install.
    required = {name for path in files
                for name in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*):?\?", (root / path).read_text(encoding="utf-8"))}
    env = {key: value for key, value in os.environ.items() if key in _DOCKER_CLI_ENV}
    env.update({name: "compose-config-fixture" for name in required})
    flags = [argument for path in files for argument in ("-f", path)]
    result = subprocess.run(["docker", "compose", "-p", "ods-library-gpu-overlays", "--project-directory",
                             str(root), *flags, "config", "--format", "json"],
                            cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    expected = ([("nvidia", ["gpu"])] if backend == "nvidia"
                else [("/dev/dri", "/dev/dri"), ("/dev/kfd", "/dev/kfd")])
    for name in recipes:
        assert _rendered_accelerator(services[name], backend) == expected, name


def _reserve(*requests):
    return {"deploy": {"resources": {"reservations": {"devices": list(requests)}}}}


_NVIDIA_GPU = {"driver": "nvidia", "capabilities": ["gpu"]}
_AMD_GPU = {"devices": ["/dev/dri:/dev/dri", "/dev/kfd:/dev/kfd"],
            "group_add": ["${VIDEO_GID:-44}", "${RENDER_GID:-992}"]}

# (case, compose file, curated recipe?, service fragment, allowed). A curated
# recipe's own backend overlay may reserve that backend's GPU exactly as ODS
# core does; nothing else grants a device, and no other privilege changes.
ACCELERATOR_POLICY = [
    ("nvidia-count-1", "compose.nvidia.yaml", True, _reserve({**_NVIDIA_GPU, "count": 1}), True),
    ("nvidia-count-all", "compose.nvidia.yaml", True, _reserve({**_NVIDIA_GPU, "count": "all"}), True),
    ("nvidia-count-omitted", "compose.nvidia.yaml", True, _reserve(_NVIDIA_GPU), True),
    ("nvidia-device-ids", "compose.nvidia.yaml", True,
     _reserve({**_NVIDIA_GPU, "device_ids": ["${OLLAMA_GPU_UUID}"]}), True),
    ("amd-kfd-dri", "compose.amd.yaml", True, _AMD_GPU, True),
    ("amd-dri-only", "compose.amd.yaml", True, {"devices": ["/dev/dri:/dev/dri"]}, True),
    # Capabilities: ODS workloads reserve [gpu] only ([utility] is dashboard-api telemetry).
    ("nvidia-gpu-utility", "compose.nvidia.yaml", True,
     _reserve({**_NVIDIA_GPU, "capabilities": ["gpu", "utility"]}), False),
    ("nvidia-gpu-compute", "compose.nvidia.yaml", True,
     _reserve({**_NVIDIA_GPU, "capabilities": ["gpu", "compute"]}), False),
    ("nvidia-gpu-utility-compute", "compose.nvidia.yaml", True,
     _reserve({**_NVIDIA_GPU, "capabilities": ["gpu", "utility", "compute"]}), False),
    ("nvidia-utility", "compose.nvidia.yaml", True, _reserve({**_NVIDIA_GPU, "capabilities": ["utility"]}), False),
    ("nvidia-other-driver", "compose.nvidia.yaml", True, _reserve({**_NVIDIA_GPU, "driver": "cdi"}), False),
    ("nvidia-driver-options", "compose.nvidia.yaml", True,
     _reserve({**_NVIDIA_GPU, "options": {"virtualization": "true"}}), False),
    ("nvidia-count-0", "compose.nvidia.yaml", True, _reserve({**_NVIDIA_GPU, "count": 0}), False),
    ("nvidia-count-true", "compose.nvidia.yaml", True, _reserve({**_NVIDIA_GPU, "count": True}), False),
    ("nvidia-count-and-ids", "compose.nvidia.yaml", True,
     _reserve({**_NVIDIA_GPU, "count": 1, "device_ids": ["0"]}), False),
    ("nvidia-empty-ids", "compose.nvidia.yaml", True, _reserve({**_NVIDIA_GPU, "device_ids": []}), False),
    ("nvidia-second-request", "compose.nvidia.yaml", True,
     _reserve({**_NVIDIA_GPU, "count": 1}, {"driver": "amd", "capabilities": ["gpu"]}), False),
    ("nvidia-requests-not-list", "compose.nvidia.yaml", True,
     {"deploy": {"resources": {"reservations": {"devices": {"driver": "nvidia"}}}}}, False),
    # Devices: /dev/kfd and /dev/dri, passed through unchanged, and nothing else.
    ("amd-dev-mem", "compose.amd.yaml", True, {"devices": ["/dev/mem:/dev/mem"]}, False),
    ("amd-dev-sda", "compose.amd.yaml", True, {"devices": ["/dev/sda:/dev/sda"]}, False),
    ("amd-with-dev-sda", "compose.amd.yaml", True,
     {"devices": ["/dev/dri:/dev/dri", "/dev/kfd:/dev/kfd", "/dev/sda:/dev/sda"]}, False),
    ("amd-host-mem-as-dri", "compose.amd.yaml", True, {"devices": ["/dev/mem:/dev/dri"]}, False),
    ("amd-dri-renamed", "compose.amd.yaml", True, {"devices": ["/dev/dri:/dev/gpu"]}, False),
    ("amd-dri-permissions", "compose.amd.yaml", True, {"devices": ["/dev/dri:/dev/dri:rwm"]}, False),
    ("amd-render-node", "compose.amd.yaml", True,
     {"devices": ["/dev/dri/renderD128:/dev/dri/renderD128"]}, False),
    ("amd-host-path-only", "compose.amd.yaml", True, {"devices": ["/dev/kfd"]}, False),
    ("amd-cdi-name", "compose.amd.yaml", True, {"devices": ["amd.com/gpu=all"]}, False),
    ("amd-long-syntax", "compose.amd.yaml", True,
     {"devices": [{"source": "/dev/dri", "target": "/dev/dri", "permissions": "rwm"}]}, False),
    ("amd-devices-not-list", "compose.amd.yaml", True, {"devices": "/dev/dri:/dev/dri"}, False),
    # Every other privilege stays rejected beside an allowed accelerator.
    ("amd-privileged", "compose.amd.yaml", True, {**_AMD_GPU, "privileged": True}, False),
    ("nvidia-privileged", "compose.nvidia.yaml", True,
     {**_reserve({**_NVIDIA_GPU, "count": 1}), "privileged": True}, False),
    ("amd-cap-add", "compose.amd.yaml", True, {**_AMD_GPU, "cap_add": ["SYS_ADMIN"]}, False),
    ("nvidia-cap-add", "compose.nvidia.yaml", True,
     {**_reserve({**_NVIDIA_GPU, "count": 1}), "cap_add": ["SYS_RAWIO"]}, False),
    ("amd-host-network", "compose.amd.yaml", True, {**_AMD_GPU, "network_mode": "host"}, False),
    ("nvidia-host-pid", "compose.nvidia.yaml", True, {**_reserve(_NVIDIA_GPU), "pid": "host"}, False),
    ("amd-host-ipc", "compose.amd.yaml", True, {**_AMD_GPU, "ipc": "host"}, False),
    # Only the curated recipe's overlay for that same backend.
    ("nvidia-in-amd-overlay", "compose.amd.yaml", True, _reserve({**_NVIDIA_GPU, "count": 1}), False),
    ("amd-in-nvidia-overlay", "compose.nvidia.yaml", True, _AMD_GPU, False),
    ("nvidia-in-compose-yaml", "compose.yaml", True, _reserve({**_NVIDIA_GPU, "count": 1}), False),
    ("amd-in-compose-yaml", "compose.yaml", True, _AMD_GPU, False),
    ("amd-in-local-overlay", "compose.local.yaml", True, _AMD_GPU, False),
    ("nvidia-in-multigpu-overlay", "compose.multigpu.yaml", True, _reserve(_NVIDIA_GPU), False),
    ("amd-in-cpu-overlay", "compose.cpu.yaml", True, _AMD_GPU, False),
    ("nvidia-imported-recipe", "compose.nvidia.yaml", False, _reserve({**_NVIDIA_GPU, "count": 1}), False),
    ("amd-imported-recipe", "compose.amd.yaml", False, _AMD_GPU, False),
    # gpus: and runtime: are other routes to a GPU; no recipe may set them anywhere.
    ("gpus-all-compose-yaml", "compose.yaml", True, {"gpus": "all"}, False),
    ("gpus-all-nvidia-overlay", "compose.nvidia.yaml", True, {"gpus": "all"}, False),
    ("gpus-request-nvidia-overlay", "compose.nvidia.yaml", True,
     {"gpus": [{"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}]}, False),
    ("gpus-all-amd-overlay", "compose.amd.yaml", True, {**_AMD_GPU, "gpus": "all"}, False),
    ("gpus-all-imported-recipe", "compose.yaml", False, {"gpus": "all"}, False),
    ("runtime-nvidia-compose-yaml", "compose.yaml", True, {"runtime": "nvidia"}, False),
    ("runtime-nvidia-nvidia-overlay", "compose.nvidia.yaml", True,
     {**_reserve({**_NVIDIA_GPU, "count": 1}), "runtime": "nvidia"}, False),
    ("runtime-runc-amd-overlay", "compose.amd.yaml", True, {**_AMD_GPU, "runtime": "runc"}, False),
    ("runtime-nvidia-imported-recipe", "compose.yaml", False, {"runtime": "nvidia"}, False),
]


@pytest.mark.parametrize("compose_name, trusted, fragment, allowed",
                         [case[1:] for case in ACCELERATOR_POLICY],
                         ids=[case[0] for case in ACCELERATOR_POLICY])
def test_accelerator_policy_is_the_same_in_dashboard_and_resolver(tmp_path, compose_name, trusted,
                                                                   fragment, allowed):
    """The install scan and the compose resolver enforce one policy."""
    compose = tmp_path / compose_name
    compose.write_text(yaml.safe_dump({"services": {"recipe": {"image": "example:fixture", **fragment}}}),
                       encoding="utf-8")
    accelerator = _accelerator(compose_name)
    scan, _ = _resolver_scan(tmp_path)
    ok, warnings = scan(compose, trusted, accelerator)
    assert ok is allowed and bool(warnings) is not allowed, warnings
    if allowed:
        extensions._scan_compose_content(compose, trusted=trusted, accelerator=accelerator)
    else:
        with pytest.raises(HTTPException) as rejected:
            extensions._scan_compose_content(compose, trusted=trusted, accelerator=accelerator)
        assert rejected.value.status_code == 400


def test_library_install_rejects_an_overlay_the_resolver_would_drop(tmp_path, monkeypatch):
    """A curated overlay outside the policy fails the install instead of running
    the service without its accelerator."""
    library = tmp_path / "library"
    recipe = library / "ollama"
    shutil.copytree(LIBRARY / "ollama", recipe)
    (recipe / "compose.amd.yaml").write_text(yaml.safe_dump(
        {"services": {"ollama": {"devices": ["/dev/dri:/dev/dri", "/dev/mem:/dev/mem"]}}}), encoding="utf-8")
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", library)
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    with pytest.raises(HTTPException) as rejected:
        with extensions._staged_library_extension("ollama", tmp_path / "user" / "ollama"):
            pass
    assert rejected.value.status_code == 400
    assert "unsupported devices" in rejected.value.detail
    assert not (tmp_path / "user" / "ollama").exists()


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


def test_library_install_names_the_overlay_that_may_hold_the_gpu(tmp_path, monkeypatch):
    """The resolver never loads compose.multigpu-nvidia.yaml for an extension;
    the install error says where a curated recipe may reserve its GPU."""
    library = tmp_path / "library"
    recipe = library / "ollama"
    shutil.copytree(LIBRARY / "ollama", recipe)
    shutil.copy2(recipe / "compose.nvidia.yaml", recipe / "compose.multigpu-nvidia.yaml")
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", library)
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    with pytest.raises(HTTPException) as rejected:
        with extensions._staged_library_extension("ollama", tmp_path / "user" / "ollama"):
            pass
    assert rejected.value.status_code == 400
    assert "(compose.multigpu-nvidia.yaml)" in rejected.value.detail
    assert "only from compose.nvidia.yaml" in rejected.value.detail
