"""Table-driven fixtures for the extension compose policy (PRs #6717 and the
shared-policy hardening that closed its open gaps).

Every case is raw Compose YAML (not a dict passed through yaml.safe_dump), so
the tables can hold the structural YAML/Compose forms a validator can
mishandle: anchors, merge keys, duplicate keys, explicit tags, multiple
documents and ${VAR} interpolation. Each case runs through BOTH validators:
dashboard-api routers/extensions.py:_scan_compose_content and the Python
inside scripts/resolve-compose-stack.sh:_scan_user_compose_content.

Where Docker Compose is available, a second check renders each negative
fixture with `docker compose config` (never `up`): a fixture that a
validator accepts must at least be one that Compose itself refuses to load.
Every negative fixture below was rendered by Docker Compose v5.1.0 before the
fix, i.e. each was a real route to the listed privilege.
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
    if isinstance(text, bytes):
        compose.write_bytes(text)
    else:
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
ODS_NETWORK = "networks:\n  ods-network:\n    external: true\n    name: ods-network\n"


def svc(body, head="", tail=""):
    return f"{head}services:\n  recipe:\n    image: example:fixture\n{body}{tail}"


# (id, compose file, curated recipe?, raw YAML, extra sibling files)
# Every case MUST be rejected by BOTH validators. Before the shared policy,
# the cases marked "was: both accepted" passed both validators and Docker
# Compose rendered the privilege (tower2, Compose v5.1.0).
MUST_REJECT = [
    # --- anchors, aliases and merge keys -----------------------------------
    ("anchor-merge-privileged", "compose.nvidia.yaml", True,
     svc(NV + "    <<: *extra\n", head="x-extra: &extra\n  privileged: true\n"), None),
    ("anchor-merge-devices-into-nvidia", "compose.nvidia.yaml", True,
     svc("    <<: *amd\n", head="x-amd: &amd\n  devices: [/dev/dri:/dev/dri]\n"), None),
    ("anchor-merge-extra-key-into-reservation", "compose.nvidia.yaml", True,
     "x-opt: &opt\n  options: {virtualization: 'true'}\n" + svc(
         "    deploy:\n      resources:\n        reservations:\n          devices:\n"
         "            - <<: *opt\n              driver: nvidia\n              count: 1\n"
         "              capabilities: [gpu]\n"), None),
    ("alias-list-merge-cap-add", "compose.amd.yaml", True,
     svc(AMD + "    <<: [*a, *b]\n",
         head="x-a: &a\n  group_add: ['44']\nx-b: &b\n  cap_add: [SYS_ADMIN]\n"), None),
    ("recursive-alias", "compose.amd.yaml", True, "x-a: &a [*a]\n" + svc(AMD + "    cap_add: *a\n"), None),
    ("recursive-mapping-alias", "compose.yaml", True, "x-a: &a {b: *a}\n" + svc("    labels: *a\n"), None),
    # Nine levels of ten aliases expand to 10^9 nodes (used to exhaust memory
    # in the resolver and abort the whole stack, not just this extension).
    ("alias-bomb", "compose.yaml", True,
     "x-0: &x0 [a, a, a, a, a, a, a, a, a, a]\n"
     + "".join(f"x-{level}: &x{level} [{', '.join([f'*x{level - 1}'] * 10)}]\n" for level in range(1, 9))
     + svc("    labels: *x8\n"), None),
    # --- duplicate keys (was: both accepted, PyYAML keeps the last) --------
    ("duplicate-key-privileged-last-false", "compose.nvidia.yaml", True,
     svc(NV + "    privileged: true\n    privileged: false\n"), None),
    ("duplicate-key-devices-in-nvidia", "compose.nvidia.yaml", True,
     svc(NV + "    devices: [/dev/sda:/dev/sda]\n    devices: []\n"), None),
    ("duplicate-key-quoted-spelling", "compose.yaml", True,
     svc("    privileged: false\n    'privileged': true\n"), None),
    ("duplicate-service", "compose.yaml", True,
     "services:\n  recipe:\n    image: example:fixture\n  recipe:\n    image: example:other\n", None),
    ("duplicate-key-inline-merge", "compose.yaml", True,
     svc("    <<: {privileged: false, privileged: true}\n"), None),
    ("duplicate-key-in-layered-anchor", "compose.yaml", True,
     "x-a: &a {init: true}\nx-b: &b {<<: *a, privileged: false, privileged: true}\n" + svc("    <<: *b\n"), None),
    # --- explicit tags and Compose booleans (was: both accepted) -----------
    ("tag-bool-privileged", "compose.nvidia.yaml", True,
     svc(NV + "    privileged: !!bool 'true'\n"), None),
    ("tag-str-privileged", "compose.nvidia.yaml", True,
     svc(NV + "    privileged: !!str true\n"), None),
    ("quoted-string-privileged", "compose.nvidia.yaml", True,
     svc(NV + "    privileged: 'true'\n"), None),
    ("compose-bool-y", "compose.yaml", False, svc("    privileged: y\n"), None),
    ("compose-bool-On-quoted", "compose.yaml", False, svc("    privileged: 'On'\n"), None),
    ("privileged-int", "compose.yaml", False, svc("    privileged: 1\n"), None),
    ("use-api-socket", "compose.yaml", True, svc("    use_api_socket: true\n"), None),
    ("use-api-socket-string", "compose.yaml", False, svc("    use_api_socket: 'yes'\n"), None),
    ("compose-reset-tag", "compose.amd.yaml", True,
     svc(AMD + "    cap_add: !reset []\n"), None),
    ("compose-override-tag", "compose.amd.yaml", True,
     svc("    devices: !override [/dev/sda:/dev/sda]\n"), None),
    # --- multiple documents and unparseable files ---------------------------
    ("multi-document", "compose.nvidia.yaml", True,
     svc(NV) + "---\n" + svc("    privileged: true\n"), None),
    ("invalid-utf8", "compose.yaml", True,
     b"services:\n  recipe:\n    image: \xff\xfe\n", None),
    ("deeply-nested", "compose.yaml", True,
     svc("    labels: " + "[" * 5000 + "]" * 5000 + "\n"), None),
    # --- ${VAR} interpolation (was: both accepted, except the GPU fields) ---
    ("interp-amd-device-path", "compose.amd.yaml", True,
     svc("    devices: ['${GPU_DEV:-/dev/kfd}:/dev/kfd']\n"), None),
    ("interp-nvidia-driver", "compose.nvidia.yaml", True,
     svc(NV.replace("driver: nvidia", "driver: ${GPU_DRIVER:-nvidia}")), None),
    ("interp-nvidia-capabilities", "compose.nvidia.yaml", True,
     svc(NV.replace("[gpu]", "['${GPU_CAP:-gpu}']")), None),
    ("interp-nvidia-count", "compose.nvidia.yaml", True,
     svc(NV.replace("count: 1", "count: ${GPU_COUNT:-1}")), None),
    ("interp-privileged", "compose.nvidia.yaml", True,
     svc(NV + "    privileged: ${RECIPE_PRIVILEGED:-true}\n"), None),
    ("interp-privileged-plain-var", "compose.yaml", False,
     svc("    privileged: $RECIPE_PRIVILEGED\n"), None),
    ("interp-network-mode", "compose.nvidia.yaml", True,
     svc(NV + "    network_mode: ${RECIPE_NET:-host}\n"), None),
    ("interp-pid", "compose.yaml", False, svc("    pid: '${RECIPE_PID:-host}'\n"), None),
    ("interp-cap-add", "compose.amd.yaml", True,
     svc(AMD + "    cap_add: ['${RECIPE_CAP:-SYS_ADMIN}']\n"), None),
    ("interp-security-opt", "compose.yaml", False,
     svc("    security_opt: ['${RECIPE_OPT:-seccomp=unconfined}']\n"), None),
    ("interp-group-add-default-0", "compose.amd.yaml", True,
     svc(AMD + "    group_add: ['${VIDEO_GID:-0}']\n"), None),
    ("interp-user-root-default", "compose.yaml", False, svc("    user: '${RECIPE_UID:-0}'\n"), None),
    ("interp-user-other-variable", "compose.yaml", False, svc("    user: '${RECIPE_UID:-1000}:1000'\n"), None),
    ("interp-label-key", "compose.yaml", False,
     svc("    labels: ['${RECIPE_LABEL:-com.docker.compose.project}=ods']\n"), None),
    ("bind-dev-interpolated-source", "compose.amd.yaml", True,
     svc(AMD + "    volumes: ['${HOST_DEV:-/dev}:/host-dev']\n"), None),
    ("bind-home-variable", "compose.yaml", False, svc("    volumes: ['${HOME}:/owner-home']\n"), None),
    ("bind-relative-prefix-interpolated", "compose.yaml", False,
     svc("    volumes: ['./data/${RECIPE_DIR:-../../..}:/escape']\n"), None),
    ("bind-long-form-interpolated", "compose.yaml", False,
     svc("    volumes:\n      - {type: bind, source: '${RECIPE_SRC:-/}', target: /host}\n"), None),
    ("named-volume-interpolated-device", "compose.yaml", False,
     svc("    volumes: ['disk:/mnt']\n", tail="volumes:\n  disk:\n    driver_opts: {type: none, o: bind, device: '${X:-/}'}\n"),
     None),
    ("network-name-interpolated", "compose.yaml", False,
     svc("    networks: [net]\n", tail="networks:\n  net:\n    external: true\n    name: '${RECIPE_NET:-host}'\n"), None),
    # --- extra keys next to an allowed reservation --------------------------
    ("reservation-entry-options", "compose.nvidia.yaml", True,
     svc(NV + "              options: {x: y}\n"), None),
    ("reservation-generic-resources", "compose.nvidia.yaml", True,
     svc(NV + "          generic_resources:\n            - discrete_resource_spec: {kind: gpu, value: 1}\n"),
     None),
    # --- device and host access outside the allowance -----------------------
    ("device-cgroup-rules", "compose.amd.yaml", True,
     svc(AMD + "    device_cgroup_rules: ['c 1:1 rwm']\n"), None),
    ("bind-dev-short", "compose.amd.yaml", True,
     svc(AMD + "    volumes: ['/dev:/dev']\n"), None),
    ("bind-dev-long", "compose.nvidia.yaml", True,
     svc(NV + "    volumes:\n      - {type: bind, source: /dev/dri, target: /dev/dri}\n"), None),
    ("bind-home-tilde", "compose.yaml", True, svc("    volumes: ['~/.ssh:/stolen:ro']\n"), None),
    # Relative binds resolve against the ODS install directory (the first -f
    # file), not the extension's own directory.
    ("bind-install-env", "compose.yaml", True, svc("    volumes: ['./.env:/secrets/.env:ro']\n"), None),
    ("bind-install-env-long-form", "compose.yaml", True,
     svc("    volumes:\n      - {type: bind, source: ./.env, target: /secrets/.env}\n"), None),
    ("bind-install-dir", "compose.yaml", True, svc("    volumes: ['.:/install']\n"), None),
    ("bind-all-service-data", "compose.yaml", True, svc("    volumes: ['./data:/all-data']\n"), None),
    ("bind-parent-escape", "compose.yaml", True, svc("    volumes: ['../../..:/escape']\n"), None),
    ("bind-windows-drive", "compose.yaml", True, svc("    volumes: ['C:\\\\Users:/users']\n"), None),
    ("bind-docker-npipe", "compose.yaml", True,
     svc("    volumes:\n      - {type: npipe, source: '\\\\\\\\.\\\\pipe\\\\docker_engine', target: /pipe}\n"), None),
    ("named-volume-bind-dev", "compose.amd.yaml", True,
     svc(AMD + "    volumes: ['devs:/host-dev']\n")
     + "volumes:\n  devs:\n    driver_opts: {type: none, o: bind, device: /dev}\n", None),
    ("named-volume-block-device", "compose.yaml", True,
     svc("    volumes: ['disk:/mnt']\n", tail="volumes:\n  disk:\n    driver_opts: {type: ext4, device: /dev/sda1}\n"),
     None),
    ("named-volume-other-project", "compose.yaml", True,
     svc("    volumes: ['core:/mnt']\n", tail="volumes:\n  core:\n    external: true\n    name: ods_lemonade-cache\n"),
     None),
    ("group-add-root", "compose.amd.yaml", True, svc(AMD + "    group_add: [root]\n"), None),
    ("group-add-gid-0", "compose.amd.yaml", True, svc(AMD + "    group_add: ['0']\n"), None),
    ("group-add-disk", "compose.amd.yaml", True, svc(AMD + "    group_add: [disk]\n"), None),
    ("group-add-docker", "compose.nvidia.yaml", True, svc(NV + "    group_add: [docker]\n"), None),
    ("group-add-gpu-groups-outside-amd-overlay", "compose.yaml", True,
     svc("    group_add: ['${VIDEO_GID:-44}', '${RENDER_GID:-992}']\n"), None),
    ("group-add-gpu-groups-imported", "compose.amd.yaml", False,
     svc("    group_add: ['${VIDEO_GID:-44}', '${RENDER_GID:-992}']\n"), None),
    ("security-opt-seccomp", "compose.amd.yaml", True,
     svc(AMD + "    security_opt: ['seccomp=unconfined']\n"), None),
    ("security-opt-systempaths", "compose.amd.yaml", True,
     svc(AMD + "    security_opt: ['systempaths=unconfined']\n"), None),
    ("security-opt-apparmor-colon", "compose.yaml", True, svc("    security_opt: ['apparmor:unconfined']\n"), None),
    ("security-opt-label-disable", "compose.yaml", True, svc("    security_opt: ['label=disable']\n"), None),
    ("security-opt-spc-t", "compose.yaml", True, svc("    security_opt: ['label=type:spc_t']\n"), None),
    ("security-opt-seccomp-profile", "compose.yaml", True,
     svc("    security_opt: ['seccomp=./allow-all.json']\n"),
     {"allow-all.json": '{"defaultAction": "SCMP_ACT_ALLOW"}'}),
    ("security-opt-writable-cgroups", "compose.yaml", True,
     svc("    security_opt: ['writable-cgroups=true']\n"), None),
    ("privileged", "compose.nvidia.yaml", True, svc(NV + "    privileged: true\n"), None),
    ("cap-add-sys-admin", "compose.amd.yaml", True, svc(AMD + "    cap_add: [SYS_ADMIN]\n"), None),
    ("cap-add-prefixed", "compose.amd.yaml", True, svc(AMD + "    cap_add: [CAP_SYS_ADMIN]\n"), None),
    ("cap-add-lower-prefixed", "compose.yaml", True, svc("    cap_add: [cap_sys_admin]\n"), None),
    ("cap-add-dac-read-search", "compose.yaml", True, svc("    cap_add: [DAC_READ_SEARCH]\n"), None),
    ("cap-add-bpf", "compose.yaml", True, svc("    cap_add: [BPF]\n"), None),
    ("cap-add-not-a-list", "compose.yaml", True, svc("    cap_add: SYS_ADMIN\n"), None),
    ("user-root-zero-padded", "compose.yaml", False, svc("    user: '00'\n"), None),
    ("user-root-signed", "compose.yaml", False, svc("    user: '+0:0'\n"), None),
    ("user-root-int", "compose.yaml", False, svc("    user: 0\n"), None),
    ("label-io-docker", "compose.yaml", True, svc("    labels: {io.docker.example: x}\n"), None),
    ("label-compose-upper", "compose.yaml", True, svc("    labels: ['COM.DOCKER.COMPOSE.PROJECT=ods']\n"), None),
    # --- namespaces ----------------------------------------------------------
    ("network-mode-host", "compose.amd.yaml", True, svc(AMD + "    network_mode: host\n"), None),
    ("pid-host", "compose.nvidia.yaml", True, svc(NV + "    pid: host\n"), None),
    ("ipc-host", "compose.nvidia.yaml", True, svc(NV + "    ipc: host\n"), None),
    ("uts-host", "compose.yaml", True, svc("    uts: host\n"), None),
    ("cgroup-host", "compose.yaml", True, svc("    cgroup: host\n"), None),
    ("userns-host", "compose.yaml", True, svc("    userns_mode: host\n"), None),
    ("pid-core-container", "compose.yaml", True, svc("    pid: 'container:ods-dashboard-api'\n"), None),
    ("network-mode-core-container", "compose.yaml", True, svc("    network_mode: 'container:ods-litellm'\n"), None),
    ("ipc-core-service", "compose.yaml", True, svc("    ipc: 'service:dashboard-api'\n"), None),
    ("network-mode-core-service", "compose.yaml", True, svc("    network_mode: 'service:litellm'\n"), None),
    # --- networks ------------------------------------------------------------
    ("external-host-network", "compose.yaml", True,
     svc("    networks: [hostnet]\n", tail="networks:\n  hostnet:\n    external: true\n    name: host\n"), None),
    ("named-host-network", "compose.yaml", True,
     svc("    networks: [hostnet]\n", tail="networks:\n  hostnet:\n    name: host\n"), None),
    ("external-network-key-host", "compose.yaml", True,
     svc("    networks: [host]\n", tail="networks:\n  host:\n    external: true\n"), None),
    ("default-bridge-network", "compose.yaml", True,
     svc("    networks: [legacy]\n", tail="networks:\n  legacy:\n    external: true\n    name: bridge\n"), None),
    ("project-default-network-redefined", "compose.yaml", True,
     svc("", tail="networks:\n  default:\n    name: host\n"), None),
    ("macvlan-network", "compose.yaml", True,
     svc("    networks: [lan]\n", tail="networks:\n  lan:\n    driver: macvlan\n    driver_opts: {parent: eth0}\n"),
     None),
    ("network-alias-core-name", "compose.yaml", True,
     svc("    networks:\n      ods-network:\n        aliases: [litellm, ods-dashboard-api]\n", tail=ODS_NETWORK), None),
    # --- other files, containers and host code pulled in by Compose ----------
    ("include-sibling-file", "compose.nvidia.yaml", True,
     "include:\n  - extra.yml\n" + svc(NV),
     {"extra.yml": "services:\n  helper:\n    image: example:fixture\n    privileged: true\n"}),
    ("extends-sibling-file", "compose.nvidia.yaml", True,
     svc(NV + "    extends: {file: base.yml, service: base}\n"),
     {"base.yml": "services:\n  base:\n    image: example:fixture\n    privileged: true\n"}),
    ("extends-same-file", "compose.yaml", True,
     svc("    extends: {service: helper}\n", tail="  helper:\n    image: example:fixture\n"), None),
    ("env-file", "compose.yaml", True, svc("    env_file: ['./recipe.env']\n"), {"recipe.env": "SECRET=x\n"}),
    ("label-file", "compose.yaml", True, svc("    label_file: ['./labels']\n"),
     {"labels": "com.docker.compose.project=ods\n"}),
    ("volumes-from-core-container", "compose.yaml", True,
     svc("    volumes_from: ['container:ods-dashboard-api']\n"), None),
    ("top-level-secrets-host-file", "compose.yaml", True,
     svc("    secrets: [hostfile]\n", tail="secrets:\n  hostfile:\n    file: /etc/hostname\n"), None),
    ("top-level-configs-host-file", "compose.yaml", True,
     svc("    configs: [hostfile]\n", tail="configs:\n  hostfile:\n    file: /etc/hostname\n"), None),
    ("top-level-name", "compose.yaml", True, svc("", head="name: renamed-project\n"), None),
    ("top-level-models", "compose.yaml", True,
     svc("    models: [m]\n", tail="models:\n  m:\n    model: ai/smollm2\n"), None),
    ("post-start-privileged", "compose.yaml", True,
     svc("    post_start:\n      - command: id\n        user: root\n        privileged: true\n"), None),
    ("pre-stop-privileged", "compose.yaml", True,
     svc("    pre_stop:\n      - command: id\n        privileged: true\n"), None),
    ("develop-watch-host-root", "compose.yaml", True,
     svc("    develop:\n      watch:\n        - {action: sync, path: /, target: /host}\n"), None),
    ("provider-plugin", "compose.yaml", True, svc("    provider: {type: example}\n"), None),
    ("annotations-cdi", "compose.yaml", True, svc("    annotations: {cdi.k8s.io/gpu: nvidia.com/gpu=all}\n"), None),
    ("cgroup-parent", "compose.yaml", True, svc("    cgroup_parent: /\n"), None),
    ("sysctls", "compose.yaml", True, svc("    sysctls: {net.ipv4.ip_forward: 1}\n"), None),
    # --- profiles do not exempt a service from the scan ---------------------
    ("profiled-service-privileged", "compose.nvidia.yaml", True,
     svc(NV + "    profiles: [debug]\n    privileged: true\n"), None),
    # --- allowance in the wrong file ----------------------------------------
    ("nvidia-in-compose-yaml", "compose.yaml", True, svc(NV), None),
    ("amd-in-compose-yaml", "compose.yaml", True, svc(AMD), None),
    ("nvidia-in-amd-overlay", "compose.amd.yaml", True, svc(NV), None),
    ("amd-in-nvidia-overlay", "compose.nvidia.yaml", True, svc(AMD), None),
    ("nvidia-in-multigpu-nvidia-overlay", "compose.multigpu-nvidia.yaml", True, svc(NV), None),
    # --- service-level gpus: and runtime: -----------------------------------
    ("gpus-all-trusted-compose-yaml", "compose.yaml", True, svc("    gpus: all\n"), None),
    ("gpus-all-trusted-amd-overlay", "compose.amd.yaml", True, svc("    gpus: all\n"), None),
    ("gpus-all-imported", "compose.yaml", False, svc("    gpus: all\n"), None),
    ("runtime-nvidia-trusted-compose-yaml", "compose.yaml", True, svc("    runtime: nvidia\n"), None),
    ("runtime-nvidia-imported", "compose.yaml", False, svc("    runtime: nvidia\n"), None),
    # --- imported recipes get no allowance at all ---------------------------
    ("nvidia-imported", "compose.nvidia.yaml", False, svc(NV), None),
    ("amd-imported", "compose.amd.yaml", False, svc(AMD), None),
]

# The legitimate shapes the repository's recipes and ODS core use, each of
# which MUST pass BOTH validators. (id, compose file, curated recipe?, raw YAML)
_HARDENED = """\
    container_name: ods-recipe
    restart: unless-stopped
    user: "1000:1000"
    read_only: true
    init: true
    tmpfs: ["/tmp:rw,noexec,nosuid,size=64m"]
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    privileged: false
    environment:
      - TZ=${TZ:-UTC}
      - RECIPE_URL=http://localhost:${RECIPE_PORT:-8080}
    command: ["sh", "-c", "echo $$HOME && exec recipe"]
    ports:
      - "${BIND_ADDRESS:-127.0.0.1}:${RECIPE_PORT:-8080}:8080"
      - "127.0.0.1:9090:9090/udp"
    volumes:
      - ./data/recipe:/data
      - ./config/recipe/settings.yaml:/etc/recipe/settings.yaml:ro
      - recipe-cache:/cache
      - {type: bind, source: ./data/recipe/uploads, target: /uploads}
      - {type: tmpfs, target: /run}
    networks: [ods-network, recipe-internal]
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:8080/"]
      interval: 30s
    deploy:
      resources:
        limits: {cpus: "2.0", memory: 2G}
        reservations: {cpus: "0.5", memory: 512M}
    logging:
      driver: json-file
      options: {max-size: 10m, max-file: "3"}
    ulimits:
      nofile: {soft: 262144, hard: 262144}
    shm_size: 256m
    platform: linux/amd64
    stop_grace_period: 30s
"""
_HARDENED_TAIL = """\
networks:
  ods-network:
    external: true
    name: ods-network
  recipe-internal:
    internal: true
volumes:
  recipe-cache: {}
  recipe-other:
"""
MUST_ACCEPT = [
    # Accelerator shapes (curated recipe, its own backend overlay).
    ("nvidia-count-1", "compose.nvidia.yaml", True, svc(NV)),
    ("nvidia-count-all", "compose.nvidia.yaml", True, svc(NV.replace("count: 1", "count: all"))),
    ("nvidia-device-ids-interp", "compose.nvidia.yaml", True,
     svc(NV.replace("count: 1", "device_ids: ['${RECIPE_GPU_UUID:-0}']"))),
    ("nvidia-flow-style", "compose.nvidia.yaml", True, svc(
        "    deploy: {resources: {reservations: {devices: "
        "[{driver: nvidia, count: 1, capabilities: [gpu]}]}}}\n")),
    ("nvidia-via-anchor", "compose.nvidia.yaml", True,
     "x-gpu: &gpu\n  driver: nvidia\n  count: 1\n  capabilities: [gpu]\n" + svc(
         "    deploy:\n      resources:\n        reservations:\n          devices: [*gpu]\n")),
    ("amd-kfd-dri", "compose.amd.yaml", True, svc(AMD)),
    ("amd-with-group-add-interp", "compose.amd.yaml", True,
     svc(AMD + "    group_add: ['${VIDEO_GID:-44}', '${RENDER_GID:-992}']\n")),
    ("amd-dri-only", "compose.amd.yaml", True, svc("    devices: [/dev/dri:/dev/dri]\n")),
    ("amd-overlay-hsa-env", "compose.amd.yaml", True,
     svc(AMD + "    group_add: ['${VIDEO_GID:-44}', '${RENDER_GID:-992}']\n"
         "    environment:\n      - HSA_OVERRIDE_GFX_VERSION=${HSA_OVERRIDE_GFX_VERSION:-}\n")),
    # The hardened baseline every curated recipe follows, curated or imported.
    ("hardened-curated", "compose.yaml", True, svc(_HARDENED, tail=_HARDENED_TAIL)),
    ("hardened-imported", "compose.yaml", False, svc(_HARDENED, tail=_HARDENED_TAIL)),
    ("version-and-x-fields", "compose.yaml", False,
     "version: '3.8'\nx-logging: &logging\n  driver: json-file\n" + svc("    logging: *logging\n")),
    ("merge-key-then-explicit-override", "compose.yaml", False,
     "x-defaults: &defaults\n  restart: always\n  privileged: false\n"
     + svc("    <<: *defaults\n    restart: unless-stopped\n")),
    # PyYAML flattens a merged mapping in place; a layered anchor merged
    # again is not a duplicate key (Compose renders these).
    ("layered-merge-then-override", "compose.yaml", False,
     "x-a: &a {restart: 'no', init: true}\nx-b: &b {<<: *a, restart: always}\n" + svc("    <<: *b\n")),
    ("layered-merge-three-deep", "compose.yaml", False,
     "x-a: &a {restart: 'no'}\nx-b: &b {<<: *a, restart: always}\nx-c: &c {<<: *b, restart: on-failure}\n"
     + svc("    <<: *c\n    restart: unless-stopped\n")),
    ("environment-anchor-reused", "compose.yaml", False,
     "x-common: &common {TZ: UTC}\nservices:\n  recipe:\n    image: example:fixture\n"
     "    environment: &env\n      <<: *common\n      LOG: debug\n"
     "  recipe-worker:\n    image: example:fixture\n    environment: *env\n"),
    ("service-template-reused-twice", "compose.yaml", False,
     "x-svc: &svc\n  image: example:fixture\n  restart: unless-stopped\n"
     "services:\n  recipe: {<<: *svc}\n  recipe-worker: {<<: *svc, restart: always}\n"),
    ("ods-network-external-without-name", "compose.yaml", False,
     svc("    networks: [ods-network]\n", tail="networks:\n  ods-network:\n    external: true\n")),
    ("project-default-network-is-ods", "compose.yaml", False,
     svc("    networks: [default]\n", tail="networks:\n  default:\n    name: ods-network\n")),
    ("service-networks-mapping-without-options", "compose.yaml", False,
     svc("    networks:\n      ods-network: {}\n      recipe-internal:\n",
         tail=ODS_NETWORK + "  recipe-internal:\n    internal: true\n")),
    ("extra-hosts-host-gateway-curated", "compose.yaml", True,
     svc("    extra_hosts: ['host.docker.internal:host-gateway']\n")),
    ("owner-user-interpolated", "compose.yaml", False, svc("    user: '${ODS_UID:-1000}:${ODS_GID:-1000}'\n")),
    ("owner-uid-only-interpolated", "compose.yaml", False, svc("    user: '${ODS_UID:-1000}'\n")),
    ("user-root-group", "compose.yaml", False, svc("    user: '1000:0'\n")),
    ("user-named", "compose.yaml", False, svc("    user: www-data\n")),
    ("user-numeric", "compose.yaml", False, svc("    user: 65532\n")),
    ("privileged-false-forms", "compose.yaml", False,
     "services:\n"
     "  a: {image: example:fixture, privileged: 'false'}\n"
     "  b: {image: example:fixture, privileged: no}\n"
     "  c: {image: example:fixture, privileged: 'off', use_api_socket: false}\n"),
    ("cap-add-default-capabilities", "compose.yaml", False,
     svc("    cap_drop: [ALL]\n    cap_add: [CHOWN, FOWNER, NET_BIND_SERVICE, CAP_KILL, sys_chroot]\n")),
    ("security-opt-no-new-privileges-forms", "compose.yaml", False,
     svc("    security_opt: [no-new-privileges, 'no-new-privileges=true', 'NO-NEW-PRIVILEGES:true']\n")),
    ("network-mode-own-service", "compose.yaml", False,
     svc("    network_mode: 'service:recipe-vpn'\n", tail="  recipe-vpn:\n    image: example:vpn\n")),
    ("network-mode-none", "compose.yaml", False, svc("    network_mode: none\n")),
    ("ipc-private", "compose.yaml", False, svc("    ipc: private\n")),
    ("labels-plain", "compose.yaml", False,
     svc("    labels: {org.opencontainers.image.title: recipe, com.example.tier: web}\n")),
    ("escaped-dollar-in-command", "compose.yaml", False,
     svc("    entrypoint: [sh, -c, 'CONFIG=$$(cat /etc/recipe) && exec recipe \"$$CONFIG\"']\n")),
]


def _reject_cases():
    return [pytest.param(*entry[1:], id=entry[0]) for entry in MUST_REJECT]


@pytest.mark.parametrize("compose_name, trusted, text, extra_files", _reject_cases())
def test_both_validators_reject(tmp_path, compose_name, trusted, text, extra_files):
    assert _verdicts(tmp_path, compose_name, trusted, text, extra_files) == (False, False)


@pytest.mark.parametrize("compose_name, trusted, text",
                         [pytest.param(*entry[1:], id=entry[0]) for entry in MUST_ACCEPT])
def test_both_validators_accept(tmp_path, compose_name, trusted, text):
    assert _verdicts(tmp_path, compose_name, trusted, text) == (True, True)


# An imported recipe (untrusted) installed as extension "recipe". Its relative
# binds resolve against the ODS install directory, where ./.env holds the
# owner's secrets, ./scripts is code the ods CLI runs on the host and ./data
# holds every other service's state. (volume, curated?, allowed)
RECIPE_BINDS = [
    ("./data/recipe:/data", False, True),
    ("./data/recipe/uploads:/uploads", False, True),
    ("./config/recipe/app.yaml:/etc/app.yaml:ro", False, True),
    ("./data/n8n:/n8n", False, False),
    ("./data/recipe-other:/other", False, False),
    ("./data/token_counter.json:/counter.json", False, False),
    ("./scripts:/host-scripts", False, False),
    ("./docker-compose.base.yml:/core.yml", False, False),
    ("./upload:/upload", False, False),
    ("./config:/config", False, False),
    # Curated recipes keep their reviewed binds (label-studio's ./upload).
    ("./upload:/upload", True, True),
    ("./data/paperless/data:/data", True, True),
]


@pytest.mark.parametrize("volume, curated, allowed", RECIPE_BINDS,
                         ids=[f"{'curated' if c else 'imported'}:{v.split(':')[0]}" for v, c, _ in RECIPE_BINDS])
@pytest.mark.parametrize("long_form", [False, True], ids=["short", "long"])
def test_imported_recipe_binds_only_its_own_data_and_config(tmp_path, volume, curated, allowed, long_form):
    source, target = volume.split(":")[:2]
    entry = (f"      - {{type: bind, source: '{source}', target: '{target}'}}\n" if long_form
             else f"      - '{volume}'\n")
    compose = tmp_path / "compose.yaml"
    compose.write_text(svc("    volumes:\n" + entry), encoding="utf-8")
    scan, _ = _resolver_scan(tmp_path)
    resolver_ok, warnings = scan(compose, curated, None, extension_id="recipe")
    assert resolver_ok is allowed, warnings
    try:
        extensions._scan_compose_content(compose, trusted=curated, extension_id="recipe")
        dashboard_ok = True
    except HTTPException as rejected:
        assert rejected.status_code == 400
        dashboard_ok = False
    assert dashboard_ok is allowed


def test_fixture_ids_are_unique():
    ids = [entry[0] for entry in MUST_REJECT + MUST_ACCEPT]
    assert len(ids) == len(set(ids))


def _compose_loads(directory, compose_name):
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "HOME", "DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT"}}
    result = subprocess.run(["docker", "compose", "-p", "v6717-fixture", "-f", compose_name,
                             "config", "--format", "json"],
                            cwd=directory, env=env, capture_output=True, text=True, timeout=60)
    return result.returncode == 0


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the Docker CLI")
@pytest.mark.parametrize("compose_name, trusted, text, extra_files", _reject_cases())
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
# Markers publish_package never writes: the resolver treats them as untrusted
# and library staging refuses the install with a clean 400.
INVALID_MARKERS = [
    ("duplicate-origin-last-curated", '{"origin": "github-proposal", "origin": "curated"}'),
    ("duplicate-origin-last-proposal", '{"origin": "curated", "origin": "github-proposal"}'),
    ("invalid-json", '{"origin": '),
    ("oversized-curated", '{"origin": "curated", "pad": "' + "x" * 600000 + '"}'),
    ("not-utf8", b'{"origin": "\xff"}'),
]


def _resolver_trust(tmp_path, marker):
    _, trusted = _resolver_scan(tmp_path)
    extension = tmp_path / "data/user-extensions/recipe"
    extension.mkdir(parents=True)
    if isinstance(marker, bytes):
        (extension / "upstream.json").write_bytes(marker)
    elif marker is not None:
        (extension / "upstream.json").write_text(marker, encoding="utf-8")
    return trusted(extension)


def _stage_ollama(tmp_path, monkeypatch, marker, symlink=False):
    """Stage the curated ollama recipe (with its compose.nvidia.yaml GPU overlay)
    under the given upstream.json marker. Returns None or the HTTPException."""
    library = tmp_path / "library"
    recipe = library / "ollama"
    shutil.copytree(LIBRARY / "ollama", recipe)
    (recipe / "upstream.json").unlink(missing_ok=True)
    if marker is not None:
        target = tmp_path / "upstream-target.json" if symlink else recipe / "upstream.json"
        if isinstance(marker, bytes):
            target.write_bytes(marker)
        else:
            target.write_text(marker, encoding="utf-8")
        if symlink:
            (recipe / "upstream.json").symlink_to(target)
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", library)
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    try:
        with extensions._staged_library_extension("ollama", tmp_path / "user" / "ollama"):
            return None
    except HTTPException as rejected:
        return rejected


def _dashboard_stages_gpu_overlay(tmp_path, monkeypatch, marker, symlink=False):
    """True when library staging accepts ollama with its compose.nvidia.yaml GPU overlay."""
    return _stage_ollama(tmp_path, monkeypatch, marker, symlink) is None


@pytest.mark.parametrize("marker", [pytest.param(m, id=i) for i, m in TRUST_MARKERS])
def test_trust_marker_is_decided_the_same_way(tmp_path, monkeypatch, marker):
    resolver = _resolver_trust(tmp_path / "resolver", marker)
    dashboard = _dashboard_stages_gpu_overlay(tmp_path / "dashboard", monkeypatch, marker)
    assert resolver is dashboard


@pytest.mark.parametrize("marker", [pytest.param(m, id=i) for i, m in INVALID_MARKERS])
def test_invalid_trust_marker_fails_closed_with_a_clean_rejection(tmp_path, monkeypatch, marker):
    """Never curated, never a 500: staging answers 400 (it used to raise
    JSONDecodeError for invalid JSON and trust an oversized marker)."""
    assert _resolver_trust(tmp_path / "resolver", marker) is False
    rejected = _stage_ollama(tmp_path / "dashboard", monkeypatch, marker)
    assert rejected is not None and rejected.status_code == 400
    assert "upstream.json" in rejected.detail


def test_github_proposal_marker_is_never_trusted(tmp_path, monkeypatch):
    marker = '{"origin": "github-proposal"}'
    assert _resolver_trust(tmp_path / "resolver", marker) is False
    assert _dashboard_stages_gpu_overlay(tmp_path / "dashboard", monkeypatch, marker) is False


def test_symlinked_upstream_json_is_decided_the_same_way(tmp_path, monkeypatch):
    """Staging's copy drops links, so a linked marker used to vanish and the
    installed copy read as curated. Both layers now fail closed."""
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
    assert trusted(extension) is False
    rejected = _stage_ollama(tmp_path / "dashboard", monkeypatch, marker, symlink=True)
    assert rejected is not None and rejected.status_code == 400
    assert not (tmp_path / "dashboard" / "user" / "ollama").exists()


@pytest.mark.parametrize("marker", ['{"origin": "curated"}', '{"origin": "github-proposal"}'],
                         ids=["curated", "proposal"])
def test_symlinked_marker_is_refused_whatever_it_points_at(tmp_path, monkeypatch, marker):
    try:
        (tmp_path / "probe").symlink_to(tmp_path)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    rejected = _stage_ollama(tmp_path / "dashboard", monkeypatch, marker, symlink=True)
    assert rejected is not None and rejected.status_code == 400


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
