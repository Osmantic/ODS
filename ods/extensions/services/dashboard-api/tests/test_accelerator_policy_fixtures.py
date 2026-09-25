"""Table-driven fixtures for the curated-library accelerator policy (PR #6717).

Every case is raw Compose YAML (not a dict passed through yaml.safe_dump), so
the tables can hold the structural YAML/Compose forms a validator can
mishandle: anchors, merge keys, duplicate keys, explicit tags, multiple
documents and ${VAR} interpolation. Each case runs through BOTH validators:
dashboard-api routers/extensions.py:_scan_compose_content and the Python
inside scripts/resolve-compose-stack.sh:_scan_user_compose_content.

Where Docker Compose is available, a second check renders each negative
fixture with `docker compose config` (never `up`): a fixture that a
validator accepts must at least be one that Compose itself refuses to load.
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from routers import extensions


ODS = Path(__file__).resolve().parents[4]
RESOLVER = ODS / "scripts/resolve-compose-stack.sh"
LIBRARY = ODS / "extensions/library/services"


def _resolver_scan(root):
    """The compose resolver's own user-extension scan (Python inside the Bash script)."""
    source = RESOLVER.read_text(encoding="utf-8")
    start = source.index("_LOOPBACK_VAR_DEFAULT_RE = re.compile(")
    end = source.index("def _extension_base_path(", start)
    namespace = {"script_dir": root, "pathlib": pathlib, "re": re, "os": os, "json": json, "yaml": yaml}
    exec(compile(source[start:end], str(RESOLVER), "exec"), namespace)
    return namespace["_scan_user_compose_content"], namespace["_library_recipe_trusted"]


def _accelerator(compose_name):
    if compose_name == "compose.yaml":
        return None
    return compose_name.removeprefix("compose.").removesuffix(".yaml")


def _verdicts(tmp_path, compose_name, trusted, text, extra_files=None):
    """(resolver accepts?, dashboard accepts?) for one raw compose file."""
    compose = tmp_path / compose_name
    compose.write_text(text, encoding="utf-8")
    for name, content in (extra_files or {}).items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    accelerator = _accelerator(compose_name)
    scan, _ = _resolver_scan(tmp_path)
    resolver_ok, _warnings = scan(compose, trusted, accelerator)
    try:
        extensions._scan_compose_content(compose, trusted=trusted, accelerator=accelerator)
        dashboard_ok = True
    except HTTPException as rejected:
        assert rejected.status_code == 400
        dashboard_ok = False
    return resolver_ok, dashboard_ok


# Shared YAML fragments. Service "recipe" is not a core service id.
NV = """\
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
"""
AMD = """\
    devices:
      - /dev/dri:/dev/dri
      - /dev/kfd:/dev/kfd
"""


def svc(body, head=""):
    return f"{head}services:\n  recipe:\n    image: example:fixture\n{body}"


# Open gaps observed on PR head e1e0d32d (tower2, Docker Compose v5.1.0).
# Each is a strict xfail: the test fails loudly once a validator starts
# rejecting the case, so the entry must then be deleted here.
_STRING_BOOL = "PyYAML sees a string; Compose casts it and renders privileged: true"
_INTERP = "value hidden behind ${VAR:-default}; Compose renders the default"
_UNCHECKED = "key is not inspected by either validator; Compose renders it"
BOTH_ACCEPT = {
    "tag-str-privileged": _STRING_BOOL,
    "quoted-string-privileged": _STRING_BOOL,
    "interp-privileged": _INTERP,
    "interp-network-mode": _INTERP,
    "interp-cap-add": _INTERP,
    "bind-dev-interpolated-source": _INTERP,
    "reservation-generic-resources": "sibling of reservations.devices is not inspected",
    "device-cgroup-rules": _UNCHECKED,
    "group-add-root": _UNCHECKED,
    "group-add-gid-0": _UNCHECKED,
    "group-add-disk": _UNCHECKED,
    "group-add-docker": _UNCHECKED,
    "security-opt-systempaths": "not in the security_opt denylist",
    "include-sibling-file": "top-level include: is not followed or rejected",
    "extends-sibling-file": "service extends: is not followed or rejected",
    "cap-add-prefixed": "resolver does not strip CAP_ (dashboard does)",
}
# Accepted by both validators (PyYAML keeps the last duplicate) but Compose
# refuses to load the file, so these do not reach the rendered project.
COMPOSE_REFUSES = {
    "duplicate-key-privileged-last-false": "PyYAML keeps the last duplicate key; Compose rejects the file",
    "duplicate-key-devices-in-nvidia": "PyYAML keeps the last duplicate key; Compose rejects the file",
}
VALIDATORS_DISAGREE = {"cap-add-prefixed": "dashboard strips CAP_ before the denylist; resolver does not"}
TRUST_DISAGREE = {
    "oversized-curated": "resolver fails closed above 512 KiB; dashboard staging has no size check",
}


def _xfail(reasons, case_id):
    reason = reasons.get(case_id)
    return (pytest.mark.xfail(strict=True, reason=reason),) if reason else ()


# (id, compose file, curated recipe?, raw YAML, extra sibling files, marks)
# Every case MUST be rejected by BOTH validators.
MUST_REJECT = [
    # --- anchors, aliases and merge keys -----------------------------------
    ("anchor-merge-privileged", "compose.nvidia.yaml", True,
     svc(NV + "    <<: *extra\n", head="x-extra: &extra\n  privileged: true\n"), None, ()),
    ("anchor-merge-devices-into-nvidia", "compose.nvidia.yaml", True,
     svc("    <<: *amd\n", head="x-amd: &amd\n  devices: [/dev/dri:/dev/dri]\n"), None, ()),
    ("anchor-merge-extra-key-into-reservation", "compose.nvidia.yaml", True,
     "x-opt: &opt\n  options: {virtualization: 'true'}\n" + svc(
         "    deploy:\n      resources:\n        reservations:\n          devices:\n"
         "            - <<: *opt\n              driver: nvidia\n              count: 1\n"
         "              capabilities: [gpu]\n"), None, ()),
    ("alias-list-merge-cap-add", "compose.amd.yaml", True,
     svc(AMD + "    <<: [*a, *b]\n",
         head="x-a: &a\n  group_add: ['44']\nx-b: &b\n  cap_add: [SYS_ADMIN]\n"), None, ()),
    # --- duplicate keys -----------------------------------------------------
    ("duplicate-key-privileged-last-false", "compose.nvidia.yaml", True,
     svc(NV + "    privileged: true\n    privileged: false\n"), None, ()),
    ("duplicate-key-devices-in-nvidia", "compose.nvidia.yaml", True,
     svc(NV + "    devices: [/dev/sda:/dev/sda]\n    devices: []\n"), None, ()),
    # --- explicit tags ------------------------------------------------------
    ("tag-bool-privileged", "compose.nvidia.yaml", True,
     svc(NV + "    privileged: !!bool 'true'\n"), None, ()),
    ("tag-str-privileged", "compose.nvidia.yaml", True,
     svc(NV + "    privileged: !!str true\n"), None, ()),
    ("quoted-string-privileged", "compose.nvidia.yaml", True,
     svc(NV + "    privileged: 'true'\n"), None, ()),
    ("compose-reset-tag", "compose.amd.yaml", True,
     svc(AMD + "    cap_add: !reset []\n"), None, ()),
    ("compose-override-tag", "compose.amd.yaml", True,
     svc("    devices: !override [/dev/sda:/dev/sda]\n"), None, ()),
    # --- multiple documents -------------------------------------------------
    ("multi-document", "compose.nvidia.yaml", True,
     svc(NV) + "---\n" + svc("    privileged: true\n"), None, ()),
    # --- ${VAR} interpolation -----------------------------------------------
    ("interp-amd-device-path", "compose.amd.yaml", True,
     svc("    devices: ['${GPU_DEV:-/dev/kfd}:/dev/kfd']\n"), None, ()),
    ("interp-nvidia-driver", "compose.nvidia.yaml", True,
     svc(NV.replace("driver: nvidia", "driver: ${GPU_DRIVER:-nvidia}")), None, ()),
    ("interp-nvidia-capabilities", "compose.nvidia.yaml", True,
     svc(NV.replace("[gpu]", "['${GPU_CAP:-gpu}']")), None, ()),
    ("interp-nvidia-count", "compose.nvidia.yaml", True,
     svc(NV.replace("count: 1", "count: ${GPU_COUNT:-1}")), None, ()),
    ("interp-privileged", "compose.nvidia.yaml", True,
     svc(NV + "    privileged: ${RECIPE_PRIVILEGED:-true}\n"), None, ()),
    ("interp-network-mode", "compose.nvidia.yaml", True,
     svc(NV + "    network_mode: ${RECIPE_NET:-host}\n"), None, ()),
    ("interp-cap-add", "compose.amd.yaml", True,
     svc(AMD + "    cap_add: ['${RECIPE_CAP:-SYS_ADMIN}']\n"), None, ()),
    # --- extra keys next to an allowed reservation --------------------------
    ("reservation-entry-options", "compose.nvidia.yaml", True,
     svc(NV + "              options: {x: y}\n"), None, ()),
    ("reservation-generic-resources", "compose.nvidia.yaml", True,
     svc(NV + "          generic_resources:\n            - discrete_resource_spec: {kind: gpu, value: 1}\n"),
     None, ()),
    # --- device and host access outside the allowance -----------------------
    ("device-cgroup-rules", "compose.amd.yaml", True,
     svc(AMD + "    device_cgroup_rules: ['c 1:1 rwm']\n"), None, ()),
    ("bind-dev-short", "compose.amd.yaml", True,
     svc(AMD + "    volumes: ['/dev:/dev']\n"), None, ()),
    ("bind-dev-long", "compose.nvidia.yaml", True,
     svc(NV + "    volumes:\n      - {type: bind, source: /dev/dri, target: /dev/dri}\n"), None, ()),
    ("bind-dev-interpolated-source", "compose.amd.yaml", True,
     svc(AMD + "    volumes: ['${HOST_DEV:-/dev}:/host-dev']\n"), None, ()),
    ("named-volume-bind-dev", "compose.amd.yaml", True,
     svc(AMD + "    volumes: ['devs:/host-dev']\n")
     + "volumes:\n  devs:\n    driver_opts: {type: none, o: bind, device: /dev}\n", None, ()),
    ("group-add-root", "compose.amd.yaml", True, svc(AMD + "    group_add: [root]\n"), None, ()),
    ("group-add-gid-0", "compose.amd.yaml", True, svc(AMD + "    group_add: ['0']\n"), None, ()),
    ("group-add-disk", "compose.amd.yaml", True, svc(AMD + "    group_add: [disk]\n"), None, ()),
    ("group-add-docker", "compose.nvidia.yaml", True, svc(NV + "    group_add: [docker]\n"), None, ()),
    ("security-opt-seccomp", "compose.amd.yaml", True,
     svc(AMD + "    security_opt: ['seccomp=unconfined']\n"), None, ()),
    ("security-opt-systempaths", "compose.amd.yaml", True,
     svc(AMD + "    security_opt: ['systempaths=unconfined']\n"), None, ()),
    ("privileged", "compose.nvidia.yaml", True, svc(NV + "    privileged: true\n"), None, ()),
    ("cap-add-sys-admin", "compose.amd.yaml", True, svc(AMD + "    cap_add: [SYS_ADMIN]\n"), None, ()),
    ("cap-add-prefixed", "compose.amd.yaml", True, svc(AMD + "    cap_add: [CAP_SYS_ADMIN]\n"), None, ()),
    ("network-mode-host", "compose.amd.yaml", True, svc(AMD + "    network_mode: host\n"), None, ()),
    ("pid-host", "compose.nvidia.yaml", True, svc(NV + "    pid: host\n"), None, ()),
    ("ipc-host", "compose.nvidia.yaml", True, svc(NV + "    ipc: host\n"), None, ()),
    # --- other files pulled in by Compose -----------------------------------
    ("include-sibling-file", "compose.nvidia.yaml", True,
     "include:\n  - extra.yml\n" + svc(NV),
     {"extra.yml": "services:\n  helper:\n    image: example:fixture\n    privileged: true\n"}, ()),
    ("extends-sibling-file", "compose.nvidia.yaml", True,
     svc(NV + "    extends: {file: base.yml, service: base}\n"),
     {"base.yml": "services:\n  base:\n    image: example:fixture\n    privileged: true\n"}, ()),
    # --- profiles do not exempt a service from the scan ---------------------
    ("profiled-service-privileged", "compose.nvidia.yaml", True,
     svc(NV + "    profiles: [debug]\n    privileged: true\n"), None, ()),
    # --- allowance in the wrong file ----------------------------------------
    ("nvidia-in-compose-yaml", "compose.yaml", True, svc(NV), None, ()),
    ("amd-in-compose-yaml", "compose.yaml", True, svc(AMD), None, ()),
    ("nvidia-in-amd-overlay", "compose.amd.yaml", True, svc(NV), None, ()),
    ("amd-in-nvidia-overlay", "compose.nvidia.yaml", True, svc(AMD), None, ()),
    ("nvidia-in-multigpu-nvidia-overlay", "compose.multigpu-nvidia.yaml", True, svc(NV), None, ()),
    # --- service-level gpus: and runtime: -----------------------------------
    ("gpus-all-trusted-compose-yaml", "compose.yaml", True, svc("    gpus: all\n"), None, ()),
    ("gpus-all-trusted-amd-overlay", "compose.amd.yaml", True, svc("    gpus: all\n"), None, ()),
    ("gpus-all-imported", "compose.yaml", False, svc("    gpus: all\n"), None, ()),
    ("runtime-nvidia-trusted-compose-yaml", "compose.yaml", True, svc("    runtime: nvidia\n"), None, ()),
    ("runtime-nvidia-imported", "compose.yaml", False, svc("    runtime: nvidia\n"), None, ()),
    # --- imported recipes get no allowance at all ---------------------------
    ("nvidia-imported", "compose.nvidia.yaml", False, svc(NV), None, ()),
    ("amd-imported", "compose.amd.yaml", False, svc(AMD), None, ()),
]

# Every case MUST be accepted by BOTH validators (trusted recipe, own overlay).
MUST_ACCEPT = [
    ("nvidia-count-1", "compose.nvidia.yaml", svc(NV)),
    ("nvidia-count-all", "compose.nvidia.yaml", svc(NV.replace("count: 1", "count: all"))),
    ("nvidia-device-ids-interp", "compose.nvidia.yaml",
     svc(NV.replace("count: 1", "device_ids: ['${RECIPE_GPU_UUID:-0}']"))),
    ("nvidia-flow-style", "compose.nvidia.yaml", svc(
        "    deploy: {resources: {reservations: {devices: "
        "[{driver: nvidia, count: 1, capabilities: [gpu]}]}}}\n")),
    ("nvidia-via-anchor", "compose.nvidia.yaml",
     "x-gpu: &gpu\n  driver: nvidia\n  count: 1\n  capabilities: [gpu]\n" + svc(
         "    deploy:\n      resources:\n        reservations:\n          devices: [*gpu]\n")),
    ("amd-kfd-dri", "compose.amd.yaml", svc(AMD)),
    ("amd-with-group-add-interp", "compose.amd.yaml",
     svc(AMD + "    group_add: ['${VIDEO_GID:-44}', '${RENDER_GID:-992}']\n")),
    ("amd-dri-only", "compose.amd.yaml", svc("    devices: [/dev/dri:/dev/dri]\n")),
]


def _cases(*gap_tables):
    return [pytest.param(*entry[1:5], id=entry[0],
                         marks=[mark for table in gap_tables for mark in _xfail(table, entry[0])])
            for entry in MUST_REJECT]


@pytest.mark.parametrize("compose_name, trusted, text, extra_files", _cases(BOTH_ACCEPT, COMPOSE_REFUSES))
def test_both_validators_reject(tmp_path, compose_name, trusted, text, extra_files):
    assert _verdicts(tmp_path, compose_name, trusted, text, extra_files) == (False, False)


@pytest.mark.parametrize("compose_name, text", [pytest.param(*entry[1:], id=entry[0]) for entry in MUST_ACCEPT])
def test_both_validators_accept(tmp_path, compose_name, text):
    assert _verdicts(tmp_path, compose_name, True, text) == (True, True)


@pytest.mark.parametrize("compose_name, trusted, text, extra_files", _cases(VALIDATORS_DISAGREE))
def test_validators_agree(tmp_path, compose_name, trusted, text, extra_files):
    """Whatever the verdict, the install scan and the resolver give the same one."""
    resolver_ok, dashboard_ok = _verdicts(tmp_path, compose_name, trusted, text, extra_files)
    assert resolver_ok is dashboard_ok


def _compose_loads(directory, compose_name):
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "HOME", "DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT"}}
    result = subprocess.run(["docker", "compose", "-p", "v6717-fixture", "-f", compose_name,
                             "config", "--format", "json"],
                            cwd=directory, env=env, capture_output=True, text=True, timeout=60)
    return result.returncode == 0


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the Docker CLI")
@pytest.mark.parametrize("compose_name, trusted, text, extra_files", _cases(BOTH_ACCEPT))
def test_no_negative_fixture_passes_a_validator_and_compose(tmp_path, compose_name, trusted, text,
                                                            extra_files):
    """`docker compose config` only (no containers). A negative fixture that a
    validator accepts must at least be one Compose refuses to load."""
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        pytest.skip("Docker Compose v2 is unavailable")
    resolver_ok, dashboard_ok = _verdicts(tmp_path, compose_name, trusted, text, extra_files)
    if resolver_ok or dashboard_ok:
        assert not _compose_loads(tmp_path, compose_name)


# --- trust marker (upstream.json) ------------------------------------------
# Imported recipes are written only by extension_recipe_package.publish_package
# (json.dumps, origin "github-proposal"). Trust is the ABSENCE of that marker,
# so every form below decides whether the accelerator allowance applies.
TRUST_MARKERS = [
    ("missing", None),
    ("curated-no-origin", '{"repository": "https://github.com/owner/project"}'),
    ("github-proposal", '{"origin": "github-proposal"}'),
    ("origin-list", '{"origin": ["github-proposal"]}'),
    ("origin-null", '{"origin": null}'),
    ("origin-case", '{"origin": "GitHub-Proposal"}'),
    ("origin-trailing-space", '{"origin": "github-proposal "}'),
    ("duplicate-origin-last-curated", '{"origin": "github-proposal", "origin": "curated"}'),
    ("duplicate-origin-last-proposal", '{"origin": "curated", "origin": "github-proposal"}'),
    ("json-array", '["github-proposal"]'),
    ("invalid-json", '{"origin": '),
    ("oversized-curated", '{"origin": "curated", "pad": "' + "x" * 600000 + '"}'),
]


def _resolver_trust(tmp_path, marker):
    _, trusted = _resolver_scan(tmp_path)
    extension = tmp_path / "data/user-extensions/recipe"
    extension.mkdir(parents=True)
    if marker is not None:
        (extension / "upstream.json").write_text(marker, encoding="utf-8")
    return trusted(extension)


def _dashboard_stages_gpu_overlay(tmp_path, monkeypatch, marker, symlink=False):
    """True when library staging accepts ollama with its compose.nvidia.yaml GPU overlay."""
    library = tmp_path / "library"
    recipe = library / "ollama"
    shutil.copytree(LIBRARY / "ollama", recipe)
    (recipe / "upstream.json").unlink(missing_ok=True)
    if marker is not None:
        target = tmp_path / "upstream-target.json" if symlink else recipe / "upstream.json"
        target.write_text(marker, encoding="utf-8")
        if symlink:
            (recipe / "upstream.json").symlink_to(target)
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", library)
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    try:
        with extensions._staged_library_extension("ollama", tmp_path / "user" / "ollama"):
            return True
    except (HTTPException, ValueError):
        return False


@pytest.mark.parametrize("marker", [pytest.param(m, id=i, marks=_xfail(TRUST_DISAGREE, i))
                                    for i, m in TRUST_MARKERS])
def test_trust_marker_is_decided_the_same_way(tmp_path, monkeypatch, marker):
    resolver = _resolver_trust(tmp_path / "resolver", marker)
    dashboard = _dashboard_stages_gpu_overlay(tmp_path / "dashboard", monkeypatch, marker)
    assert resolver is dashboard


def test_github_proposal_marker_is_never_trusted(tmp_path, monkeypatch):
    marker = '{"origin": "github-proposal"}'
    assert _resolver_trust(tmp_path / "resolver", marker) is False
    assert _dashboard_stages_gpu_overlay(tmp_path / "dashboard", monkeypatch, marker) is False


@pytest.mark.xfail(strict=True, reason="staging skips the symlink, so the installed copy has no "
                                       "marker and both layers then treat it as curated")
def test_symlinked_upstream_json_is_decided_the_same_way(tmp_path, monkeypatch):
    marker = '{"origin": "github-proposal"}'
    root = tmp_path / "resolver"
    _, trusted = _resolver_scan(root)
    extension = root / "data/user-extensions/recipe"
    extension.mkdir(parents=True)
    (root / "target.json").write_text(marker, encoding="utf-8")
    try:
        (extension / "upstream.json").symlink_to(root / "target.json")
    except OSError:
        pytest.skip("symlink privilege unavailable")
    resolver = trusted(extension)
    dashboard = _dashboard_stages_gpu_overlay(tmp_path / "dashboard", monkeypatch, marker, symlink=True)
    assert resolver is dashboard


# --- multi-GPU overlay naming ----------------------------------------------
# Built-in services ship compose.multigpu-<backend>.yaml (loaded unscanned by
# the built-in loop). For user extensions the resolver loads only
# compose.multigpu.yaml (accelerator None), while library staging scans every
# compose.*.yaml and grants an accelerator only to compose.nvidia.yaml /
# compose.amd.yaml. Neither multi-GPU name can carry a GPU request for a
# curated recipe today.
def test_no_curated_recipe_ships_a_multigpu_overlay():
    """Adding one needs a decision on the naming gap first (see below)."""
    assert sorted(LIBRARY.glob("*/compose.multigpu*.yaml")) == []


def test_multigpu_backend_overlay_fails_staging_and_is_never_resolved(tmp_path, monkeypatch):
    library = tmp_path / "library"
    recipe = library / "ollama"
    shutil.copytree(LIBRARY / "ollama", recipe)
    (recipe / "compose.multigpu-nvidia.yaml").write_text(svc(NV).replace("recipe:", "ollama:"), encoding="utf-8")
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", library)
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    with pytest.raises(HTTPException) as rejected:
        with extensions._staged_library_extension("ollama", tmp_path / "user" / "ollama"):
            pass
    assert rejected.value.status_code == 400 and "GPU passthrough" in rejected.value.detail

    if shutil.which("bash") is None:
        pytest.skip("the resolver is a Bash script")
    root = tmp_path / "ods"
    (root / "config").mkdir(parents=True)
    shutil.copy2(ODS / "config/core-service-ids.json", root / "config/core-service-ids.json")
    (root / "docker-compose.base.yml").write_text("services: {}\n", encoding="utf-8")
    installed = root / "data/user-extensions/ollama"
    shutil.copytree(recipe, installed)
    env = {"PATH": os.environ["PATH"], "HOME": str(root), "ODS_MODE": "local"}
    result = subprocess.run(["bash", str(RESOLVER), "--script-dir", str(root), "--gpu-backend", "nvidia",
                             "--tier", "1", "--gpu-count", "2"],
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "data/user-extensions/ollama/compose.nvidia.yaml" in result.stdout
    assert "compose.multigpu-nvidia.yaml" not in result.stdout
