#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TIER="1"
GPU_BACKEND="nvidia"
PROFILE_OVERLAYS=""
ENV_MODE="false"
SKIP_BROKEN="false"
GPU_COUNT="1"
ODS_MODE="${ODS_MODE:-local}"
SKIP_GPU_OVERLAYS="${ODS_SKIP_GPU_OVERLAYS:-${ODS_SKIP_GPU_OVERLAYS_FOR:-}}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --script-dir)
            SCRIPT_DIR="${2:-$SCRIPT_DIR}"
            shift 2
            ;;
        --tier)
            TIER="${2:-$TIER}"
            shift 2
            ;;
        --gpu-backend)
            GPU_BACKEND="${2:-$GPU_BACKEND}"
            shift 2
            ;;
        --profile-overlays)
            PROFILE_OVERLAYS="${2:-$PROFILE_OVERLAYS}"
            shift 2
            ;;
        --skip-broken)
            SKIP_BROKEN="true"
            shift
            ;;
        --env)
            ENV_MODE="true"
            shift
            ;;
        --gpu-count)
            GPU_COUNT="${2:-$GPU_COUNT}"
            shift 2
            ;;
        --ods-mode)
            ODS_MODE="${2:-$ODS_MODE}"
            shift 2
            ;;
        --skip-gpu-overlays|--skip-gpu-overlays-for)
            SKIP_GPU_OVERLAYS="${2:-$SKIP_GPU_OVERLAYS}"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

ROOT_DIR="$SCRIPT_DIR"
PYTHON_CMD="python3"
if [[ -f "$ROOT_DIR/lib/python-cmd.sh" ]]; then
    . "$ROOT_DIR/lib/python-cmd.sh"
    PYTHON_CMD="$(ods_detect_python_cmd_with_module yaml 2>/dev/null || ods_detect_python_cmd)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

if ! "$PYTHON_CMD" -c 'import yaml' >/dev/null 2>&1; then
    echo "ERROR: PyYAML is required by resolve-compose-stack.sh for compose validation." >&2
    echo "       Active Python: $PYTHON_CMD ($(command -v "$PYTHON_CMD" 2>/dev/null || echo "$PYTHON_CMD"))" >&2
    echo "       If Conda/venv is active, run 'conda deactivate' before installing ODS." >&2
    echo "       Or install manually: $PYTHON_CMD -m pip install pyyaml" >&2
    exit 2
fi

"$PYTHON_CMD" - "$SCRIPT_DIR" "$TIER" "$GPU_BACKEND" "$PROFILE_OVERLAYS" "$ENV_MODE" "$SKIP_BROKEN" "$GPU_COUNT" "$ODS_MODE" "$SKIP_GPU_OVERLAYS" <<'PY'
import os
import pathlib
import platform
import sys
import json

script_dir = pathlib.Path(sys.argv[1])
tier = (sys.argv[2] or "1").upper()
gpu_backend = (sys.argv[3] or "nvidia").lower()
profile_overlays = [x.strip() for x in (sys.argv[4] or "").split(",") if x.strip()]
env_mode = (sys.argv[5] or "false").lower() == "true"
skip_broken = (sys.argv[6] or "false").lower() == "true"
gpu_count = int(sys.argv[7] or "1")
ods_mode = (sys.argv[8] or os.environ.get("ODS_MODE", "local")).lower()
skip_gpu_overlays = {
    x.strip().lower()
    for x in (sys.argv[9] or os.environ.get("ODS_SKIP_GPU_OVERLAYS", "")).split(",")
    if x.strip()
}
if os.environ.get("WHISPER_ACCELERATION", "").strip().lower() == "cpu":
    skip_gpu_overlays.add("whisper")
lemonade_external = (
    os.environ.get("LEMONADE_EXTERNAL", "").lower() in {"1", "true", "yes", "on"}
    or (
        os.environ.get("AMD_INFERENCE_RUNTIME", "").lower() == "lemonade"
        and os.environ.get("AMD_INFERENCE_MANAGED", "").lower() == "false"
    )
)
external_llm = bool(os.environ.get("EXTERNAL_LLM_URL", "").strip())

IS_DARWIN = platform.system() == "Darwin"
APPLE_OVERLAY = "installers/macos/docker-compose.macos.yml" if IS_DARWIN else "docker-compose.apple.yml"
macos_cloud_auth = script_dir / "data" / "generated" / "docker-compose.macos-cloud-auth.yml"

def existing(overlays):
    return all((script_dir / f).exists() for f in overlays)

resolved = []
primary = "docker-compose.yml"

# An explicit external runtime owns inference selection, even when hardware
# detection supplied a local CPU/AMD/NVIDIA profile to the installer.
if lemonade_external and ods_mode == "lemonade":
    # External Lemonade is still a local, switchable runtime. The cloud
    # overlay profiles model-router out and can leave a stale router container
    # serving Pixel after reinstall. The external overlay disables only the
    # managed llama-server, preserving a freshly built model-router.
    if existing(["docker-compose.base.yml", "docker-compose.lemonade-external.yml"]):
        resolved = ["docker-compose.base.yml", "docker-compose.lemonade-external.yml"]
        primary = "docker-compose.lemonade-external.yml"
    elif existing(["docker-compose.base.yml", "docker-compose.cloud.yml"]):
        resolved = ["docker-compose.base.yml", "docker-compose.cloud.yml"]
        primary = "docker-compose.cloud.yml"
    elif existing(["docker-compose.base.yml"]):
        resolved = ["docker-compose.base.yml"]
        primary = "docker-compose.base.yml"
elif profile_overlays and existing(profile_overlays):
    resolved = profile_overlays
    primary = profile_overlays[-1]
elif ods_mode == "cloud" or tier == "CLOUD":
    if existing(["docker-compose.base.yml", "docker-compose.cloud.yml"]):
        resolved = ["docker-compose.base.yml", "docker-compose.cloud.yml"]
        primary = "docker-compose.cloud.yml"
    elif existing(["docker-compose.base.yml"]):
        resolved = ["docker-compose.base.yml"]
        primary = "docker-compose.base.yml"
elif tier in {"AP_ULTRA", "AP_PRO", "AP_BASE"}:
    if existing(["docker-compose.base.yml", APPLE_OVERLAY]):
        resolved = ["docker-compose.base.yml", APPLE_OVERLAY]
        primary = APPLE_OVERLAY
    elif existing(["docker-compose.base.yml"]):
        resolved = ["docker-compose.base.yml"]
        primary = "docker-compose.base.yml"
elif gpu_backend == "cpu":
    if existing(["docker-compose.base.yml", "docker-compose.cpu.yml"]):
        resolved = ["docker-compose.base.yml", "docker-compose.cpu.yml"]
        primary = "docker-compose.cpu.yml"
    elif existing(["docker-compose.base.yml"]):
        resolved = ["docker-compose.base.yml"]
        primary = "docker-compose.base.yml"
elif tier in {"SH_LARGE", "SH_COMPACT"}:
    if existing(["docker-compose.base.yml", "docker-compose.amd.yml"]):
        resolved = ["docker-compose.base.yml", "docker-compose.amd.yml"]
        primary = "docker-compose.amd.yml"
elif gpu_backend == "apple":
    if existing(["docker-compose.base.yml", APPLE_OVERLAY]):
        resolved = ["docker-compose.base.yml", APPLE_OVERLAY]
        primary = APPLE_OVERLAY
    elif existing(["docker-compose.base.yml"]):
        resolved = ["docker-compose.base.yml"]
        primary = "docker-compose.base.yml"
elif gpu_backend == "amd":
    if existing(["docker-compose.base.yml", "docker-compose.amd.yml"]):
        resolved = ["docker-compose.base.yml", "docker-compose.amd.yml"]
        primary = "docker-compose.amd.yml"
elif gpu_backend in ("intel", "sycl") or tier in ("ARC", "ARC_LITE"):
    if existing(["docker-compose.base.yml", "docker-compose.arc.yml"]):
        resolved = ["docker-compose.base.yml", "docker-compose.arc.yml"]
        primary = "docker-compose.arc.yml"
    elif existing(["docker-compose.base.yml", "docker-compose.intel.yml"]):
        resolved = ["docker-compose.base.yml", "docker-compose.intel.yml"]
        primary = "docker-compose.intel.yml"
    elif existing(["docker-compose.base.yml"]):
        resolved = ["docker-compose.base.yml"]
        primary = "docker-compose.base.yml"
else:
    if existing(["docker-compose.base.yml", "docker-compose.nvidia.yml"]):
        resolved = ["docker-compose.base.yml", "docker-compose.nvidia.yml"]
        primary = "docker-compose.nvidia.yml"
    elif (script_dir / "docker-compose.yml").exists():
        resolved = ["docker-compose.yml"]
        primary = "docker-compose.yml"

if not resolved:
    resolved = [primary]

# A generated auth file is an override, never a primary/profile input. Remove
# stale cached occurrences before extension discovery so it cannot make a
# partial core service appear to exist or influence overlay eligibility.
macos_cloud_auth_resolved = macos_cloud_auth.resolve()
resolved = [
    compose_rel
    for compose_rel in resolved
    if (script_dir / compose_rel).resolve() != macos_cloud_auth_resolved
]
if not resolved:
    print("ERROR: compose resolution produced no usable base files", file=sys.stderr)
    sys.exit(1)
if (script_dir / primary).resolve() == macos_cloud_auth_resolved:
    primary = resolved[-1]

# Multi-GPU overlay if we have more than 1 GPU.
if gpu_count > 1:
    multigpu_file = f"docker-compose.multigpu-{gpu_backend}.yml"
    if (script_dir / multigpu_file).exists():
        resolved.append(multigpu_file)

# PyYAML is a hard requirement — extensions and overlays are YAML and must be
# parsed for the compose security scan. Silent fallback used to hide install
# breakage on systems without PyYAML (Arch/Alpine/Void/some macOS) and let
# user-extension composes through unscanned. Fail loud here instead.
import yaml
import re

_LOOPBACK_VAR_DEFAULT_RE = re.compile(
    r"^\$\{[A-Za-z_][A-Za-z0-9_]*:-127\.0\.0\.1\}$",
)

# Core service IDs — user extensions must not declare services with these names
# (would shadow the built-in services in the compose merge). Mirrors the
# dashboard-api install endpoint's CORE_SERVICE_IDS / skip_name_collision check
# so a hand-dropped or backup-restored extension under the user-extensions dir
# can't override e.g. dashboard-api or llama-server. Loaded best-effort: if
# config/core-service-ids.json is missing or unparseable, fall back to a
# hardcoded list matching helpers.py CORE_SERVICE_IDS_FALLBACK.
import json as _json_mod
try:
    _CORE_SERVICE_IDS = set(
        _json_mod.loads((script_dir / "config" / "core-service-ids.json").read_text(encoding="utf-8"))
    )
except (OSError, ValueError):
    _CORE_SERVICE_IDS = {
        "ape", "comfyui", "dashboard", "dashboard-api",
        "embeddings", "langfuse", "litellm", "llama-server", "n8n",
        "open-webui", "openclaw", "perplexica", "privacy-shield", "qdrant",
        "remote-provider-egress", "remote-provider-ssh-tunnel",
        "searxng", "token-spy", "tts", "whisper",
    }


def _host_part_is_loopback(host):
    if host == "127.0.0.1":
        return True
    return bool(_LOOPBACK_VAR_DEFAULT_RE.fullmatch(host))


def _split_port_host(port_str):
    """Mirror dashboard-api/_split_port_host: handle ${VAR:-127.0.0.1}: prefix."""
    if port_str.startswith("${"):
        end = port_str.find("}")
        if end == -1 or end + 1 >= len(port_str) or port_str[end + 1] != ":":
            return port_str, ""
        return port_str[: end + 1], port_str[end + 2:]
    if ":" not in port_str:
        return None, port_str
    host, _, rest = port_str.partition(":")
    if host.isdigit():
        return None, port_str
    return host, rest


_extension_build_contexts = {}


def _extension_build_context(compose_path, build):
    """Accept bounded build inputs; never forward arbitrary host paths/options."""
    root = script_dir / "data" / "user-extensions"
    directory = compose_path.parent
    if directory.parent.resolve() != root.resolve() or directory.is_symlink():
        raise ValueError("build must belong to an installed extension")
    if isinstance(build, str):
        build = {"context": build}
    if not isinstance(build, dict):
        raise ValueError("unsupported build options")
    context = build.get("context", ".")
    if isinstance(context, str) and context.startswith("https://github.com/"):
        # The API publishes commit-bound GitHub recipes. Preserve their remote
        # context across Windows/Linux/macOS rather than treating it as a path.
        # Revalidate the complete installed recipe before forwarding to Docker.
        import hashlib
        from urllib.parse import urlsplit
        if set(build) - {"context", "dockerfile", "dockerfile_inline", "target"}:
            raise ValueError("unsupported remote build options")
        parsed = urlsplit(context)
        match = re.fullmatch(r"/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9][A-Za-z0-9._-]{0,99})\.git", parsed.path)
        revision, separator, subdir = parsed.fragment.partition(":")
        if (parsed.netloc != "github.com" or parsed.query or not match
                or not re.fullmatch(r"[a-f0-9]{40}", revision) or (separator and not subdir)):
            raise ValueError("remote build requires an immutable public GitHub commit")
        repository = "https://github.com/" + match[1] + "/" + match[2]
        for name in ("upstream.json", "manifest.yaml", compose_path.name):
            source = directory / name
            if source.is_symlink() or not source.is_file() or source.stat().st_size > 524288:
                raise ValueError("invalid installed recipe file")
        upstream = json.loads((directory / "upstream.json").read_text(encoding="utf-8"))
        manifest = yaml.safe_load((directory / "manifest.yaml").read_text(encoding="utf-8"))
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        if (not isinstance(upstream, dict) or upstream.get("origin") != "github-proposal"
                or not isinstance(upstream.get("repository"), str)
                or upstream["repository"].rstrip("/").removesuffix(".git").lower() != repository.lower()
                or upstream.get("commit") != revision):
            raise ValueError("remote build does not match installed provenance")
        if not isinstance(manifest, dict) or not isinstance(compose, dict) or not isinstance(compose.get("services"), dict):
            raise ValueError("invalid source recipe documents")
        candidate = {"repository": upstream["repository"], "commit": revision, "manifest": manifest, "compose": compose}
        digest = hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        if upstream.get("recipeDigest") != digest:
            raise ValueError("installed source recipe changed")
        dockerfile = build.get("dockerfile", "Dockerfile")
        for value, required in ((subdir, False), (dockerfile, True)):
            if (not isinstance(value, str) or len(value) > 256 or (required and not value)
                    or (value and any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
                                      or part in {".", ".."} for part in value.split("/")))):
                raise ValueError("Dockerfile must stay inside the source context")
        target = build.get("target")
        if target is not None and (not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", target)):
            raise ValueError("invalid build target")
        inline = build.get("dockerfile_inline")
        if "dockerfile_inline" in build and ("dockerfile" in build or not isinstance(inline, str)
                or not inline.strip() or len(inline.encode("utf-8")) > 24576
                or any(ord(char) < 32 and char not in "\n\r\t" for char in inline)
                or "$" in re.findall(r"\$\$|\$", inline)):
            raise ValueError("invalid inline Dockerfile")
        receipts = upstream.get("sourceFiles")
        if not isinstance(receipts, list):
            raise ValueError("missing source build evidence")
        matched = False
        for service_name, service in compose["services"].items():
            if not isinstance(service, dict) or service.get("build") != build:
                continue
            matched = True
            if service.get("image") != f"ods-source-{service_name}:{revision}" or service.get("pull_policy") != "never":
                raise ValueError("source image must be owned and commit-bound")
            receipt = next((item for item in receipts if isinstance(item, dict) and item.get("service") == service_name), None)
            if inline is not None:
                expected = {"service": service_name, "kind": "proposed-dockerfile", "sha256": hashlib.sha256(inline.encode("utf-8")).hexdigest()}
                if receipt != expected:
                    raise ValueError("inline Dockerfile evidence changed")
            elif (not isinstance(receipt, dict) or receipt.get("path") != "/".join(filter(None, (subdir, dockerfile)))
                    or not isinstance(receipt.get("blob"), str) or not re.fullmatch(r"[a-f0-9]{40}", receipt["blob"])):
                raise ValueError("upstream Dockerfile evidence changed")
        if not matched:
            raise ValueError("source build absent from installed recipe")
        return context
    if set(build) - {"context", "dockerfile", "target", "args"}:
        raise ValueError("unsupported build options")
    if not isinstance(context, str) or "$" in context or ("\\" in context and os.name != "nt"):
        raise ValueError("invalid build context")
    # The API stages files in its /data mount. Resolve that precise alias on
    # the host; Linux, macOS and Windows do not share the container's root.
    alias = "/data/user-extensions/" + directory.name
    if context == alias or context.startswith(alias + "/"):
        context = "." + context[len(alias):]
    candidate = pathlib.Path(context)
    if not candidate.is_absolute():
        candidate = directory / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(directory.resolve()) or not resolved.is_dir():
        raise ValueError("build context escapes extension or is missing")
    dockerfile = build.get("dockerfile", "Dockerfile")
    if not isinstance(dockerfile, str) or "$" in dockerfile or ("\\" in dockerfile and os.name != "nt"):
        raise ValueError("invalid Dockerfile path")
    source = resolved / dockerfile
    if not source.resolve().is_relative_to(resolved) or not source.is_file():
        raise ValueError("Dockerfile escapes context or is missing")
    # Build contexts may otherwise follow links outside their permitted tree.
    for entry in resolved.rglob("*"):
        if entry.is_symlink() or not entry.resolve().is_relative_to(resolved):
            raise ValueError("symlinks are not supported in extension builds")
    args = build.get("args", {})
    if not isinstance(args, dict) or any(not isinstance(k, str) or not isinstance(v, (str, int, float, bool)) for k, v in args.items()):
        raise ValueError("build arguments must have explicit values")
    target = build.get("target")
    if target is not None and (not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", target)):
        raise ValueError("invalid build target")
    return str(resolved)


# The only extra_hosts entry a curated library recipe may declare. Mirrors
# dashboard-api _scan_compose_content(allowed_trusted_extra_hosts): GAIA needs
# it to reach a host-run Lemonade Server on Linux Docker.
_TRUSTED_LIBRARY_EXTRA_HOSTS = {"host.docker.internal:host-gateway"}

# Accelerator access a curated library recipe may request, only from its own
# backend overlay and only in the shapes ODS core uses for GPU workloads:
# docker-compose.amd.yml passes /dev/kfd and /dev/dri through unchanged, and
# docker-compose.nvidia.yml plus the comfyui/whisper overlays reserve driver
# nvidia with capabilities [gpu] by count or device_ids. Mirrors dashboard-api
# _TRUSTED_LIBRARY_AMD_DEVICES / _is_ods_nvidia_gpu_reservation.
_TRUSTED_LIBRARY_AMD_DEVICES = {"/dev/kfd:/dev/kfd", "/dev/dri:/dev/dri"}
_NVIDIA_DEVICE_ID_RE = re.compile(r"[A-Za-z0-9_.:${}-]+")


def _is_ods_nvidia_gpu_reservation(entry):
    """True for one reservations.devices entry in the shape ODS core writes."""
    if not isinstance(entry, dict) or set(entry) - {"driver", "capabilities", "count", "device_ids"}:
        return False
    if entry.get("driver") != "nvidia" or entry.get("capabilities") != ["gpu"]:
        return False
    if "count" in entry and "device_ids" in entry:
        return False
    count = entry.get("count", "all")
    if count != "all" and not (type(count) is int and count >= 1):
        return False
    device_ids = entry.get("device_ids", ["all"])
    return isinstance(device_ids, list) and bool(device_ids) and all(
        isinstance(item, str) and _NVIDIA_DEVICE_ID_RE.fullmatch(item) for item in device_ids)


# >>> shared compose policy >>>
# One rule set for both extension compose validators: dashboard-api
# routers/extensions.py:_scan_compose_content (install and enable time) and
# scripts/resolve-compose-stack.sh:_scan_user_compose_content (every `ods`
# command). This block is byte-identical in the two files, and
# dashboard-api tests/test_compose_policy_parity.py fails when they drift.
#
# The validators read PyYAML values, but Docker Compose decides what runs, so
# every rule judges a value the way Compose resolves it and fails closed where
# the file alone cannot decide:
#   * Compose casts the strings true/yes/y/on (any case) to boolean true, so
#     only an explicit false passes a boolean guard.
#   * Compose substitutes ${VAR}, ${VAR:-default} and $VAR from the owner's
#     environment when it renders the project. A guarded value that
#     interpolates is rejected; the only exceptions are exact shapes ODS core
#     itself uses (the GPU group ids, the install owner's uid:gid, and the
#     accelerator and port shapes checked by each validator).
#   * include:, extends:, env_file, volumes_from, secrets and configs pull
#     other files, host paths or containers into a service outside this scan.
#   * Compose resolves every relative bind source against the project
#     directory, which is the ODS install directory (the first -f file), not
#     the extension's own directory: ./.env there is the owner's secrets and
#     ./scripts is code the ods CLI runs on the host. An imported recipe may
#     bind only its own ./data/<id> and ./config/<id>.
#   * PyYAML keeps the last of two duplicate keys and Compose refuses them;
#     the loader refuses them too instead of judging a value Compose never
#     sees.
_COMPOSE_POLICY_FALSE = frozenset({"false", "no", "n", "off"})
# Top-level keys an extension compose file may declare (plus x-* fields).
_COMPOSE_POLICY_TOP_LEVEL = frozenset({"services", "volumes", "networks", "version"})
_COMPOSE_POLICY_TOP_LEVEL_REASONS = {
    "include": "pulls in other Compose files",
    "name": "renames the whole Compose project",
    "secrets": "reads host files as secrets",
    "configs": "reads host files as configs",
}
# Service keys that reach host files, other containers, host code or the
# runtime outside what this scan can judge. No shipped recipe uses them.
_COMPOSE_POLICY_DENIED_SERVICE_KEYS = {
    "extends": "extends another service definition",
    "env_file": "reads an env_file from the host",
    "label_file": "reads a label_file from the host",
    "volumes_from": "mounts another container's volumes",
    "secrets": "mounts Compose secrets",
    "configs": "mounts Compose configs",
    "device_cgroup_rules": "declares device_cgroup_rules",
    "cgroup_parent": "sets cgroup_parent",
    "credential_spec": "reads a credential_spec",
    "annotations": "sets runtime annotations",
    "develop": "syncs host files with develop",
    "post_start": "runs a post_start lifecycle hook",
    "pre_stop": "runs a pre_stop lifecycle hook",
    "provider": "runs a host provider plugin",
    "models": "attaches Docker models",
}
# Namespace modes: "host" shares the host's namespace; container:/service:
# joins another container's (a core service's secrets and loopback ports).
_COMPOSE_POLICY_NAMESPACES = (
    ("network_mode", "network mode"),
    ("pid", "PID namespace"),
    ("ipc", "IPC namespace"),
    ("uts", "UTS namespace"),
    ("userns_mode", "user namespace"),
    ("cgroup", "cgroup namespace"),
)
# Docker's default capability set, minus the defaults ODS always refused
# (DAC_OVERRIDE, NET_RAW, SETGID, SETUID). Adding one of these is harmless;
# any other capability (SYS_ADMIN, DAC_READ_SEARCH, BPF, ...) is refused.
_COMPOSE_POLICY_ALLOWED_CAPS = frozenset({
    "AUDIT_WRITE", "CHOWN", "FOWNER", "FSETID", "KILL", "MKNOD",
    "NET_BIND_SERVICE", "SETFCAP", "SETPCAP", "SYS_CHROOT",
})
# Every other security_opt (seccomp/apparmor/systempaths=unconfined,
# label=disable or type:spc_t, a seccomp profile path, writable-cgroups)
# relaxes confinement.
_COMPOSE_POLICY_SECURITY_OPTS = frozenset({
    "no-new-privileges", "no-new-privileges:true", "no-new-privileges=true",
    "no-new-privileges:false", "no-new-privileges=false",
})
# The render/video groups ODS core adds beside its AMD /dev/kfd and /dev/dri
# passthrough (docker-compose.amd.yml). A curated recipe may add them only in
# the overlay that holds that passthrough.
_COMPOSE_POLICY_GPU_GROUPS = frozenset({"${VIDEO_GID:-44}", "${RENDER_GID:-992}"})
# The install owner, as ODS core and curated recipes write it.
_COMPOSE_POLICY_OWNER_USER_RE = re.compile(r"\$\{ODS_UID:-[1-9][0-9]*\}(?::\$\{ODS_GID:-[0-9]+\})?")
_COMPOSE_POLICY_ROOT_UID_RE = re.compile(r"[+-]?[0-9]+")
_COMPOSE_POLICY_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/]|[\\/]{2}")
_COMPOSE_POLICY_RESERVATION_KEYS = frozenset({"cpus", "memory", "devices"})
_COMPOSE_POLICY_NETWORK_KEYS = frozenset({"external", "name", "internal", "labels"})
_COMPOSE_POLICY_VOLUME_KEYS = frozenset({"labels"})
_COMPOSE_POLICY_MARKER_MAX_BYTES = 524288
# A Compose file with every alias expanded; ODS's largest is a few hundred
# nodes. Bounds alias bombs before anything walks the parsed document.
_COMPOSE_POLICY_MAX_NODES = 100000


class _ComposePolicyLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys, as Compose does."""

    def _refuse_duplicate_keys(self, node):
        # SafeConstructor.flatten_mapping rewrites a mapping node in place
        # (merged keys first, then its own), so judge each node once, on the
        # keys its author wrote, before that happens. A layered merge
        # (x-b: {<<: *a, restart: always}) then merged again is not a
        # duplicate.
        checked = self.__dict__.setdefault("_compose_policy_checked", set())
        if id(node) in checked:
            return
        checked.add(id(node))
        seen = set()
        for key_node, value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                merged = value_node.value if isinstance(value_node, yaml.SequenceNode) else [value_node]
                for item in merged:
                    if isinstance(item, yaml.MappingNode):
                        self._refuse_duplicate_keys(item)
                continue
            if not isinstance(key_node, yaml.ScalarNode):
                continue
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    f"found duplicate key {key!r}", key_node.start_mark)
            seen.add(key)

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            self._refuse_duplicate_keys(node)
        return super().construct_mapping(node, deep=deep)


def _compose_policy_bound_expansion(data):
    """ValueError when data refers to itself or, with every alias expanded,
    exceeds _COMPOSE_POLICY_MAX_NODES. Linear in the parsed (shared) size."""
    sizes, active, pending = {}, set(), [(data, False)]
    while pending:
        item, finished = pending.pop()
        if not isinstance(item, (dict, list)) or (not finished and id(item) in sizes):
            continue
        children = [*item.keys(), *item.values()] if isinstance(item, dict) else item
        if finished:
            active.discard(id(item))
            sizes[id(item)] = 1 + sum(sizes[id(child)] if isinstance(child, (dict, list)) else 1
                                      for child in children)
            if sizes[id(item)] > _COMPOSE_POLICY_MAX_NODES:
                raise ValueError("document expands beyond %d nodes (excessive aliasing)"
                                 % _COMPOSE_POLICY_MAX_NODES)
            continue
        if id(item) in active:
            raise ValueError("self-referencing anchor")
        active.add(id(item))
        pending.append((item, True))
        pending.extend((child, False) for child in children if isinstance(child, (dict, list)))


def _compose_policy_load(text):
    """Parse one Compose document; ValueError for anything not judgeable.

    That is invalid YAML, several documents, a duplicate key, Compose's own
    !reset/!override tags (unknown to SafeLoader), self-referencing anchors,
    alias bombs and unboundedly nested structures.
    """
    try:
        data = yaml.load(text, Loader=_ComposePolicyLoader)  # noqa: S506 - SafeLoader subclass
        _compose_policy_bound_expansion(data)
    except (yaml.YAMLError, RecursionError, MemoryError, ValueError) as exc:
        raise ValueError(str(exc) or type(exc).__name__) from None
    return data


def _compose_policy_interpolates(value):
    """True when Compose would substitute into any string within value."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if "$" in item.replace("$$", ""):
                return True
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return False


def _compose_policy_false(value):
    """True when Compose reads value as boolean false (absent counts)."""
    if value is None or value is False:
        return True
    return (isinstance(value, str) and not _compose_policy_interpolates(value)
            and value.strip().lower() in _COMPOSE_POLICY_FALSE)


def _compose_policy_list(name, key, value, problems):
    """value as a list, or None after recording that Compose would not see one."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    problems.append(f"service '{name}' {key} must be a list")
    return None


def _compose_policy_volume_problems(name, volumes, *, builtin, namespace):
    problems = []
    for volume in _compose_policy_list(name, "volumes", volumes, problems) or []:
        if isinstance(volume, dict):
            source = volume.get("source")
            source = "" if source is None else str(source)
            spec = volume
            host_path = volume.get("type") == "bind"
        elif isinstance(volume, str):
            source = volume.split(":", 1)[0]
            spec = volume
            host_path = source.startswith(".")
        else:
            problems.append(f"service '{name}' has an unsupported volume entry")
            continue
        text = str(spec)
        if "docker.sock" in text or "docker_engine" in text:
            problems.append(f"service '{name}' has a Docker socket mount")
        # Built-in extensions ship with ODS; litellm selects its config file
        # from the owner's ODS_MODE. User and library files may not choose a
        # host path at render time.
        if not builtin and _compose_policy_interpolates(spec):
            problems.append(f"service '{name}' volume '{text}' chooses its source with ${{...}} interpolation")
            continue
        if isinstance(volume, str) and _COMPOSE_POLICY_WINDOWS_PATH_RE.match(volume):
            source = volume.rsplit(":", 1)[0] if volume.count(":") > 1 else volume
        if source.startswith(("/", "~")) or _COMPOSE_POLICY_WINDOWS_PATH_RE.match(source):
            problems.append(f"service '{name}' bind-mounts absolute host path '{source}'")
        elif ".." in source.replace("\\", "/").split("/"):
            problems.append(
                f"service '{name}' bind-mounts relative host path '{source}' escaping the project directory")
        elif host_path:
            parts = [part for part in source.replace("\\", "/").split("/") if part not in ("", ".")]
            if not parts or parts[0].startswith(".") or parts in (["data"], ["config"]):
                problems.append(f"service '{name}' bind-mounts the ODS install directory or its "
                                f"secrets ('{source}')")
            elif namespace is not None and (len(parts) < 2 or parts[0] not in ("data", "config")
                                            or parts[1] != namespace):
                problems.append(f"service '{name}' bind-mounts '{source}' outside its own "
                                f"./data/{namespace} and ./config/{namespace}")
    return problems


def _compose_policy_service_problems(name, service, *, own_services, accelerator=None,
                                     builtin=False, check_root_user=True, namespace=None):
    """Policy problems of one service definition (an empty list passes).

    ``accelerator`` is the backend whose GPU this file may request: the
    caller passes it only for a curated recipe's own compose.<backend>.yaml.
    ``builtin`` is for ODS's own extensions (read-only EXTENSIONS_DIR).
    ``namespace`` is the extension id of an untrusted (imported) recipe,
    whose relative bind mounts must stay in ./data/<id> or ./config/<id>.
    """
    problems = []
    for key, reason in _COMPOSE_POLICY_DENIED_SERVICE_KEYS.items():
        if key in service:
            problems.append(f"service '{name}' {reason}")
    if not _compose_policy_false(service.get("privileged")):
        problems.append(f"service '{name}' uses privileged mode")
    if not _compose_policy_false(service.get("use_api_socket")):
        problems.append(f"service '{name}' mounts the Docker API socket (use_api_socket)")
    for key, label in _COMPOSE_POLICY_NAMESPACES:
        mode = service.get(key)
        if mode is None:
            continue
        if not isinstance(mode, str) or _compose_policy_interpolates(mode):
            problems.append(f"service '{name}' {key} must be a literal string")
            continue
        normalized = mode.strip().lower()
        kind, _, target = mode.strip().partition(":")
        if normalized == "host":
            problems.append(f"service '{name}' uses host {label}")
        elif kind.lower() == "container":
            problems.append(f"service '{name}' joins another container's {label}")
        elif kind.lower() == "service" and target not in own_services:
            problems.append(f"service '{name}' joins the {label} of service '{target}' outside this file")
    for cap in _compose_policy_list(name, "cap_add", service.get("cap_add"), problems) or []:
        if str(cap).strip().upper().removeprefix("CAP_") not in _COMPOSE_POLICY_ALLOWED_CAPS:
            problems.append(f"service '{name}' adds dangerous capability: {cap}")
    for opt in _compose_policy_list(name, "security_opt", service.get("security_opt"), problems) or []:
        if str(opt).strip().lower() not in _COMPOSE_POLICY_SECURITY_OPTS:
            problems.append(f"service '{name}' uses dangerous security_opt '{opt}'")
    groups = _compose_policy_list(name, "group_add", service.get("group_add"), problems) or []
    if groups and accelerator != "amd":
        problems.append(f"service '{name}' adds supplementary groups (group_add); only a curated "
                        f"recipe's compose.amd.yaml may add the GPU video/render groups")
    elif any(not isinstance(group, str) or group not in _COMPOSE_POLICY_GPU_GROUPS for group in groups):
        problems.append(f"service '{name}' adds groups other than the GPU video/render groups")
    if service.get("sysctls"):
        problems.append(f"service '{name}' declares sysctls")
    if check_root_user and service.get("user") is not None:
        user = str(service["user"]).strip()
        uid = user.split(":", 1)[0].strip()
        if _compose_policy_interpolates(user):
            if not _COMPOSE_POLICY_OWNER_USER_RE.fullmatch(user):
                problems.append(f"service '{name}' chooses its user with ${{...}} interpolation")
        elif uid.lower() == "root" or (_COMPOSE_POLICY_ROOT_UID_RE.fullmatch(uid) and int(uid) == 0):
            problems.append(f"service '{name}' runs as root")
    labels = service.get("labels")
    if isinstance(labels, dict):
        label_keys = list(labels)
    elif isinstance(labels, list):
        label_keys = [str(label).split("=", 1)[0] for label in labels]
    else:
        label_keys = []
        if labels is not None:
            problems.append(f"service '{name}' labels must be a mapping or a list")
    for label in label_keys:
        if (_compose_policy_interpolates(label)
                or str(label).strip().lower().startswith(("com.docker.compose.", "io.docker."))):
            problems.append(f"service '{name}' uses reserved Docker Compose label '{label}'")
    problems.extend(_compose_policy_volume_problems(name, service.get("volumes"), builtin=builtin,
                                                    namespace=namespace))
    networks = service.get("networks")
    if isinstance(networks, dict) and any(options not in (None, {}) for options in networks.values()):
        problems.append(f"service '{name}' sets per-network options (aliases, addresses)")
    deploy = service.get("deploy")
    resources = deploy.get("resources") if isinstance(deploy, dict) else None
    reservations = resources.get("reservations") if isinstance(resources, dict) else None
    if isinstance(reservations, dict) and set(reservations) - _COMPOSE_POLICY_RESERVATION_KEYS:
        extra = ", ".join(sorted(map(str, set(reservations) - _COMPOSE_POLICY_RESERVATION_KEYS)))
        problems.append(f"service '{name}' reserves unsupported resources: {extra}")
    return problems


def _compose_policy_document_problems(data):
    """Policy problems of the top level: keys, named networks and volumes."""
    problems = []
    for key in data:
        if not isinstance(key, str) or not (key in _COMPOSE_POLICY_TOP_LEVEL or key.startswith("x-")):
            reason = _COMPOSE_POLICY_TOP_LEVEL_REASONS.get(key, "is not permitted in an extension")
            problems.append(f"top-level '{key}' {reason}")
    networks = data.get("networks")
    if networks is not None and not isinstance(networks, dict):
        problems.append("top-level networks must be a mapping")
    for key, network in (networks.items() if isinstance(networks, dict) else ()):
        if network is None:
            continue
        if not isinstance(network, dict) or set(network) - _COMPOSE_POLICY_NETWORK_KEYS:
            problems.append(f"network '{key}' sets a driver or options")
            continue
        external = network.get("external")
        named = network.get("name")
        if isinstance(external, dict):
            named = external.get("name", named)
        if named is None and not _compose_policy_false(external):
            named = key
        # Only ODS's own network may be joined by name; any other name can be
        # Docker's host or default bridge network or another project's.
        if named is not None and named != "ods-network":
            problems.append(f"network '{key}' joins Docker network '{named}' outside ODS")
    volumes = data.get("volumes")
    if volumes is not None and not isinstance(volumes, dict):
        problems.append("top-level volumes must be a mapping")
    for key, volume in (volumes.items() if isinstance(volumes, dict) else ()):
        if volume is None:
            continue
        if not isinstance(volume, dict):
            problems.append(f"named volume '{key}' must be a mapping")
            continue
        options = volume.get("driver_opts")
        if isinstance(options, dict) and str(options.get("device", "")).startswith("/"):
            problems.append(f"named volume '{key}' uses driver_opts to bind-mount host path "
                            f"'{options.get('device')}'")
        elif set(volume) - _COMPOSE_POLICY_VOLUME_KEYS:
            problems.append(f"named volume '{key}' sets a driver, driver_opts, name or external")
    return problems


def _compose_policy_unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _compose_policy_library_origin(extension_dir):
    """Classify an extension by its upstream.json provenance marker.

    "curated": no marker, or one without origin github-proposal;
    "imported": origin github-proposal (a proposed GitHub recipe);
    "invalid": a link or other non-regular file, over 512 KiB, not UTF-8
    JSON, or with a duplicate key. Trust is the absence of the imported
    marker, so an invalid marker must never read as curated.
    """
    import stat

    marker = extension_dir / "upstream.json"
    try:
        if marker.is_symlink():
            return "invalid"
        if not marker.exists():
            return "curated"
        descriptor = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return "invalid"
            raw = stream.read(_COMPOSE_POLICY_MARKER_MAX_BYTES + 1)
        if len(raw) > _COMPOSE_POLICY_MARKER_MAX_BYTES:
            return "invalid"
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_compose_policy_unique_object)
    except (OSError, ValueError, RecursionError):
        return "invalid"
    if isinstance(value, dict) and value.get("origin") == "github-proposal":
        return "imported"
    return "curated"
# <<< shared compose policy <<<


def _library_recipe_trusted(extension_dir):
    """Mirror dashboard-api's install-time trust decision for one extension.

    ``_staged_library_extension`` treats a library recipe as curated unless
    its upstream.json records ``origin: github-proposal`` (an imported GitHub
    recipe). An upstream.json that is a link, oversized, not JSON or has a
    duplicate key was not written by that install path, so it fails closed
    (see ``_compose_policy_library_origin``; the dashboard refuses to install
    such a recipe at all).
    """
    return _compose_policy_library_origin(extension_dir) == "curated"


def _scan_user_compose_content(compose_path, trusted_library=False, accelerator=None, extension_id=None):
    """Reject compose fragments containing dangerous directives.

    Mirrors dashboard-api/routers/extensions.py:_scan_compose_content (without
    the FastAPI HTTPException dependency). Returns ``(ok, warnings)``: ``ok``
    is False on any rejection, ``warnings`` is a list of human-readable
    messages. User-extension contexts are untrusted at the resolver layer; the
    exemptions are the dashboard's own and need ``trusted_library`` (see
    ``_library_recipe_trusted``): an ``extra_hosts`` list may contain exactly
    ``host.docker.internal:host-gateway``, and when ``accelerator`` names the
    backend of the overlay being scanned ("nvidia" or "amd"), that backend's
    GPU in exactly the ODS core shape (see ``_TRUSTED_LIBRARY_AMD_DEVICES``
    and ``_is_ods_nvidia_gpu_reservation``). Nothing else grants a device;
    ``gpus`` and ``runtime`` are rejected for every user extension. Every
    other rule is the shared compose policy (``_compose_policy_*``), the same
    code dashboard-api runs. ``extension_id`` names the user extension being
    scanned: an untrusted one may bind-mount only its own ./data/<id> and
    ./config/<id> (the override file passes none).
    """
    if not trusted_library:
        accelerator = None
    namespace = None if trusted_library else extension_id
    try:
        data = _compose_policy_load(compose_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return (False, [f"invalid compose file {compose_path}: {e}"])

    if not isinstance(data, dict):
        return (False, [f"compose file {compose_path} must be a YAML mapping"])

    warnings = []
    ok = True
    build_contexts = {}

    def reject(msg):
        nonlocal ok
        ok = False
        warnings.append(msg)

    for problem in _compose_policy_document_problems(data):
        reject(problem)
    services = data.get("services", {})
    if not isinstance(services, dict):
        return (ok, warnings)
    own_services = {str(name) for name in services}

    for svc_name, svc_def in services.items():
        if not isinstance(svc_def, dict):
            continue
        # Name-collision: user-extension service names must not shadow
        # built-in core services in the compose merge. Mirrors the
        # dashboard-api install endpoint's skip_name_collision=False path.
        if svc_name in _CORE_SERVICE_IDS:
            reject(f"service '{svc_name}' collides with a built-in core service name")
        for problem in _compose_policy_service_problems(
                svc_name, svc_def, own_services=own_services, accelerator=accelerator,
                namespace=namespace):
            reject(problem)
        if "build" in svc_def:
            try:
                build_contexts[svc_name] = {"build": {"context": _extension_build_context(compose_path, svc_def["build"])}}
            except (ValueError, OSError, yaml.YAMLError) as exc:
                reject(f"service '{svc_name}' build rejected: {exc}")
        # gpus: and runtime: are other routes to a GPU (Compose `gpus: all`,
        # the legacy NVIDIA runtime), and a runtime also swaps the container's
        # isolation. No user extension may set either, curated or imported.
        if "gpus" in svc_def:
            reject(f"service '{svc_name}' requests GPUs via gpus")
        if "runtime" in svc_def:
            reject(f"service '{svc_name}' sets a container runtime")
        devices = svc_def.get("devices")
        if devices:
            if accelerator != "amd":
                reject(f"service '{svc_name}' declares devices")
            elif not isinstance(devices, list) or any(
                    not isinstance(entry, str) or entry not in _TRUSTED_LIBRARY_AMD_DEVICES
                    for entry in devices):
                reject(f"service '{svc_name}' declares unsupported devices")
        deploy = svc_def.get("deploy")
        if isinstance(deploy, dict):
            resources = deploy.get("resources")
            if isinstance(resources, dict):
                reservations = resources.get("reservations")
                if isinstance(reservations, dict) and reservations.get("devices"):
                    requests = reservations["devices"]
                    if accelerator != "nvidia":
                        reject(f"service '{svc_name}' requests GPU passthrough via deploy.resources.reservations.devices")
                    elif not isinstance(requests, list) or not all(
                            _is_ods_nvidia_gpu_reservation(entry) for entry in requests):
                        reject(f"service '{svc_name}' requests an unsupported GPU reservation")
        extra_hosts = svc_def.get("extra_hosts")
        if extra_hosts:
            if not trusted_library:
                reject(f"service '{svc_name}' declares extra_hosts")
            elif not isinstance(extra_hosts, list) or any(
                    not isinstance(entry, str) or entry.strip() not in _TRUSTED_LIBRARY_EXTRA_HOSTS
                    for entry in extra_hosts):
                reject(f"service '{svc_name}' declares unsupported extra_hosts")
        ports = svc_def.get("ports", [])
        if isinstance(ports, list):
            for port in ports:
                if isinstance(port, dict):
                    host_ip = port.get("host_ip", "")
                    if port.get("published") and not _host_part_is_loopback(host_ip):
                        reject(f"service '{svc_name}' dict port binding must use host_ip 127.0.0.1 or '${{VAR:-127.0.0.1}}'")
                else:
                    port_str = str(port)
                    host_part, rest = _split_port_host(port_str)
                    if host_part is None:
                        reject(f"service '{svc_name}' port '{port_str}' must use 127.0.0.1:host:container format")
                        continue
                    if not _host_part_is_loopback(host_part):
                        reject(f"service '{svc_name}' port '{port_str}' must bind 127.0.0.1 (literal or '${{VAR:-127.0.0.1}}')")
                        continue
                    core = rest.split("/", 1)[0]
                    if ":" not in core:
                        reject(f"service '{svc_name}' port '{port_str}' must specify host:host_port:container_port")

    if ok:
        _extension_build_contexts[str(compose_path.resolve())] = build_contexts
    return (ok, warnings)


def _extension_base_path(service_dir, service, label):
    """Return an enabled extension base path, or None when it is disabled."""
    compose_rel = service.get("compose_file", "compose.yaml")
    if not isinstance(compose_rel, str) or not compose_rel:
        print(f"WARNING: {label}: manifest has no usable compose_file, skipping overlays", file=sys.stderr)
        return None
    if compose_rel.endswith(".disabled"):
        return None

    compose_path = service_dir / compose_rel
    try:
        compose_path.resolve().relative_to(service_dir.resolve())
    except ValueError:
        print(
            f"WARNING: {label}: compose_file '{compose_rel}' escapes the extension directory; skipping",
            file=sys.stderr,
        )
        return None

    if compose_path.exists():
        return compose_path
    if (service_dir / f"{compose_rel}.disabled").exists():
        return None

    print(
        f"WARNING: {label}: compose_file '{compose_rel}' not found, skipping overlays",
        file=sys.stderr,
    )
    return None


def _load_compose_mapping(compose_path, label):
    try:
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as e:
        print(f"ERROR: {label} is not valid Compose YAML: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict):
        print(f"ERROR: {label} must be a YAML mapping", file=sys.stderr)
        sys.exit(1)
    return data


_LOCAL_INFERENCE_DEPENDENCIES = {
    "llama-server",
    "llama-server-ready",
    "model-router",
}


def _compose_requires_local_inference(compose_path):
    """Return true when an overlay explicitly waits on a local model service.

    Backend-named user overlays predate mode-specific overlays. Some of them
    use ``compose.nvidia.yaml`` or ``compose.cpu.yaml`` only to add a
    ``depends_on: llama-server`` readiness edge, not to request accelerator
    access. Retaining that edge in cloud, external-LLM, or external Lemonade
    mode makes the complete Compose project invalid because managed local
    inference is profiled out.
    """
    data = _load_compose_mapping(compose_path, f"Compose file {compose_path}")
    services = data.get("services", {})
    if not isinstance(services, dict):
        return False
    for service in services.values():
        if not isinstance(service, dict):
            continue
        depends_on = service.get("depends_on", {})
        if isinstance(depends_on, dict):
            dependency_names = depends_on.keys()
        elif isinstance(depends_on, list):
            dependency_names = depends_on
        else:
            continue
        if _LOCAL_INFERENCE_DEPENDENCIES.intersection(
            str(name) for name in dependency_names
        ):
            return True
    return False


def _declared_compose_services(files, strict=True):
    declared_services = set()
    for compose_rel in files:
        compose_path = pathlib.Path(compose_rel)
        if not compose_path.is_absolute():
            compose_path = script_dir / compose_path
        if strict:
            compose_data = _load_compose_mapping(compose_path, f"Compose file {compose_rel}")
        else:
            try:
                compose_data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
            except (yaml.YAMLError, OSError):
                continue
            if not isinstance(compose_data, dict):
                continue
        compose_services = compose_data.get("services", {})
        if isinstance(compose_services, dict):
            declared_services.update(compose_services)
    return declared_services


def _append_macos_cloud_auth_overlay(files, overlay_path):
    """Append the generated auth override without allowing a partial service."""
    label = "generated macOS cloud-auth overlay"
    data = _load_compose_mapping(overlay_path, label)
    if set(data) != {"services"}:
        print(f"ERROR: {label} may contain only the services mapping", file=sys.stderr)
        sys.exit(1)

    services = data.get("services")
    if not isinstance(services, dict) or set(services) != {"open-webui"}:
        print(
            f"ERROR: {label} may target only the always-present open-webui service",
            file=sys.stderr,
        )
        sys.exit(1)

    open_webui = services.get("open-webui")
    if not isinstance(open_webui, dict) or set(open_webui) != {"environment"}:
        print(f"ERROR: {label} may only override open-webui environment", file=sys.stderr)
        sys.exit(1)
    environment = open_webui.get("environment")
    if not isinstance(environment, dict):
        print(f"ERROR: {label} environment must be a mapping", file=sys.stderr)
        sys.exit(1)
    api_key = environment.get("OPENAI_API_KEY")
    if not isinstance(api_key, str) or not api_key.startswith("${LITELLM_KEY:"):
        print(f"ERROR: {label} must source OPENAI_API_KEY from LITELLM_KEY", file=sys.stderr)
        sys.exit(1)

    missing = set(services) - _declared_compose_services(files)
    if missing:
        print(
            f"ERROR: {label} would create partial service(s): {', '.join(sorted(missing))}",
            file=sys.stderr,
        )
        sys.exit(1)

    files.append(str(overlay_path.relative_to(script_dir)))


base_stack_services = _declared_compose_services(resolved, strict=False)


# Discover enabled extension compose fragments via manifests
ext_dir = script_dir / "extensions" / "services"
if ext_dir.exists():
    for service_dir in sorted(ext_dir.iterdir()):
        if not service_dir.is_dir():
            continue
        # Find manifest
        manifest_path = None
        for name in ("manifest.yaml", "manifest.yml", "manifest.json"):
            candidate = service_dir / name
            if candidate.exists():
                manifest_path = candidate
                break
        if not manifest_path:
            continue
        try:
            with open(manifest_path) as f:
                if manifest_path.suffix == ".json":
                    manifest = json.load(f)
                else:
                    manifest = yaml.safe_load(f)
            if not isinstance(manifest, dict):
                print(f"WARNING: empty/non-dict manifest for {service_dir.name} at {manifest_path}, skipping", file=sys.stderr)
                continue
            if manifest.get("schema_version") != "ods.services.v1":
                continue
            service = manifest.get("service", {})
            # Check GPU backend compatibility
            backends = service.get("gpu_backends", ["amd", "nvidia"])
            # "none" means CPU-only — compatible with any GPU backend
            if gpu_backend not in backends and "all" not in backends and "none" not in backends:
                continue
            compose_rel = service.get("compose_file")
            if compose_rel:
                compose_path = _extension_base_path(service_dir, service, service_dir.name)
                if compose_path is None:
                    continue
                resolved.append(str(compose_path.relative_to(script_dir)))
            else:
                # Core services may keep their base definition in the primary
                # stack and ship only specialized fragments here. Permit that
                # shape only when the service already exists; otherwise an
                # overlay would create the same partial-service failure.
                service_id = service.get("id")
                if service_id not in base_stack_services:
                    continue
            # GPU-specific overlay (filesystem discovery — not in manifest)
            gpu_overlay = service_dir / f"compose.{gpu_backend}.yaml"
            if service_dir.name.lower() not in skip_gpu_overlays and gpu_overlay.exists():
                resolved.append(str(gpu_overlay.relative_to(script_dir)))

            # Mode-specific overlay — depends_on for local/hybrid mode only.
            # Skip on Apple Silicon: macOS runs llama-server natively on the host
            # (Docker service has replicas: 0), so `depends_on: llama-server:
            # service_healthy` inside compose.local.yaml overlays can never be
            # satisfied and deadlocks the stack. The real LLM-ready gate on macOS
            # is the `llama-server-ready` sidecar defined in the macOS overlay.
            # External Lemonade is also a host process. Its stack layers the
            # cloud overlay to profile out ODS's managed llama-server, so
            # local-mode overlays that wait on `llama-server: service_healthy`
            # would point at a disabled service and break lifecycle commands.
            if ods_mode in ("local", "hybrid", "lemonade") and tier != "CLOUD" and gpu_backend != "apple" and not lemonade_external and not external_llm:
                local_mode_overlay = service_dir / "compose.local.yaml"
                if local_mode_overlay.exists():
                    resolved.append(str(local_mode_overlay.relative_to(script_dir)))

            # Multi-GPU overlay if we have more than 1 GPU
            if gpu_count > 1:
                multi_gpu_overlay = service_dir / f"compose.multigpu-{gpu_backend}.yaml"
                if multi_gpu_overlay.exists():
                    resolved.append(str(multi_gpu_overlay.relative_to(script_dir)))

        except Exception as e:
            # Narrow exception handling to specific parse/structure errors
            yaml_error = isinstance(e, yaml.YAMLError)
            json_error = isinstance(e, json.JSONDecodeError)
            structure_error = isinstance(e, (KeyError, TypeError))

            if yaml_error or json_error or structure_error:
                print(f"ERROR: Failed to parse manifest for {service_dir.name}: {e}", file=sys.stderr)
                print(f"  Manifest path: {manifest_path}", file=sys.stderr)
                print(f"  This service will be skipped. Fix the manifest or disable the service.", file=sys.stderr)
                if skip_broken:
                    continue
                else:
                    sys.exit(1)
            else:
                # Unexpected error — re-raise to crash visibly
                raise

# Services a refused user-extension fragment would have declared, with the
# reason, so dependents dropped by _drop_unresolvable_user_extensions() can say
# why their dependency is missing.
_refused_services = {}


def _note_refused_fragment(service_dir, compose_path, warnings):
    try:
        data = _compose_policy_load(compose_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    services = data.get("services") if isinstance(data, dict) else None
    reason = (f"which {service_dir.name}/{compose_path.name} declares but was refused: "
              f"{warnings[0] if warnings else 'compose policy'}")
    for name in (services if isinstance(services, dict) else ()):
        _refused_services.setdefault(str(name), reason)


# Discover enabled user-installed extensions (from dashboard portal)
user_ext_dir = script_dir / "data" / "user-extensions"
if user_ext_dir.exists():
    try:
        for service_dir in sorted(user_ext_dir.iterdir()):
            if not service_dir.is_dir():
                continue
            # Extension IDs never start with a dot. The dashboard keeps its
            # staging (.tmp) and definition backups (.backups) here; they are
            # not legacy manifest-less extensions and must not warn.
            if service_dir.name.startswith("."):
                continue
            # Find manifest
            manifest_path = None
            for name in ("manifest.yaml", "manifest.yml", "manifest.json"):
                candidate = service_dir / name
                if candidate.exists():
                    manifest_path = candidate
                    break
            try:
                manifest = None  # init so the gpu_backends gate below is safe in the manifest-less branch
                if manifest_path is not None:
                    with open(manifest_path) as f:
                        if manifest_path.suffix == ".json":
                            manifest = json.load(f)
                        else:
                            manifest = yaml.safe_load(f)
                    if manifest is not None and not isinstance(manifest, dict):
                        print(f"WARNING: empty/non-dict manifest for {service_dir.name} at {manifest_path}, skipping", file=sys.stderr)
                        continue
                    if isinstance(manifest, dict) and manifest.get("schema_version") != "ods.services.v1":
                        continue
                    service = manifest.get("service", {}) if isinstance(manifest, dict) else {}
                else:
                    service = {}
                # Imported recipes without GPU metadata are unrestricted, as
                # in the catalog. Explicit backend restrictions still apply.
                # Gated on isinstance(manifest, dict) so the manifest-less compat
                # carve-out (legacy user extensions that pre-date the manifest convention)
                # falls through unfiltered.
                if isinstance(manifest, dict):
                    backends = service.get("gpu_backends", ["all"])
                    # "none" means CPU-only — compatible with any GPU backend
                    if gpu_backend not in backends and "all" not in backends and "none" not in backends:
                        continue
                # The base file is also the enable/disable marker for user
                # extensions. Resolve it before considering any overlays.
                compose_path = _extension_base_path(service_dir, service, service_dir.name)
                if compose_path is None:
                    continue
                # Scan content before appending — user-ext composes are
                # untrusted. The dashboard-api scans at install time; the
                # resolver runs every `ods` invocation, so a tampered-with
                # compose dropped under data/user-extensions without going
                # through the install API would otherwise bypass scanning.
                # Curated library recipes keep the dashboard's narrow
                # exemptions: host.docker.internal:host-gateway in every file,
                # and the backend's ODS-shaped GPU only in compose.<backend>.yaml.
                trusted_library = _library_recipe_trusted(service_dir)
                ok, warnings = _scan_user_compose_content(compose_path, trusted_library,
                                                          extension_id=service_dir.name)
                for w in warnings:
                    print(f"WARNING: {service_dir.name}: {w}", file=sys.stderr)
                if not ok:
                    _note_refused_fragment(service_dir, compose_path, warnings)
                    continue
                resolved.append(str(compose_path.relative_to(script_dir)))
                # GPU-specific overlay (filesystem discovery — not in manifest)
                gpu_overlay = service_dir / f"compose.{gpu_backend}.yaml"
                if service_dir.name.lower() not in skip_gpu_overlays and gpu_overlay.exists():
                    # Fixed filename so traversal isn't possible, but the same
                    # security checks apply to the overlay's content.
                    ok, warnings = _scan_user_compose_content(gpu_overlay, trusted_library, gpu_backend,
                                                              extension_id=service_dir.name)
                    for w in warnings:
                        print(f"WARNING: {service_dir.name}: {w}", file=sys.stderr)
                    if not ok:
                        _note_refused_fragment(service_dir, gpu_overlay, warnings)
                    else:
                        managed_local_inference = (
                            ods_mode in ("local", "hybrid")
                            and tier != "CLOUD"
                            and not lemonade_external
                            and not external_llm
                        )
                        if (
                            not managed_local_inference
                            and _compose_requires_local_inference(gpu_overlay)
                        ):
                            print(
                                f"WARNING: {service_dir.name}: skipping "
                                f"{gpu_overlay.name} because this model mode has "
                                "no managed local inference service",
                                file=sys.stderr,
                            )
                        else:
                            resolved.append(str(gpu_overlay.relative_to(script_dir)))

                # Mode-specific overlay — depends_on for local/hybrid mode only.
                # Skip on Apple Silicon: macOS runs llama-server natively on the host
                # (Docker service has replicas: 0), so `depends_on: llama-server:
                # service_healthy` inside compose.local.yaml overlays can never be
                # satisfied and deadlocks the stack. The real LLM-ready gate on macOS
                # is the `llama-server-ready` sidecar defined in the macOS overlay.
                # External Lemonade likewise runs on the host and uses the cloud
                # overlay to disable ODS's managed llama-server, so user-local
                # overlays must not add local llama-server health dependencies.
                # Mirrors the same guard in the built-in loop above (PR #1004).
                if ods_mode in ("local", "hybrid", "lemonade") and tier != "CLOUD" and gpu_backend != "apple" and not lemonade_external and not external_llm:
                    local_mode_overlay = service_dir / "compose.local.yaml"
                    if local_mode_overlay.exists():
                        # Same content scan as compose.yaml/gpu overlay above —
                        # without it, a malicious user extension can put
                        # privileged: true / docker.sock mounts in compose.local.yaml
                        # and reach the host since ODS_MODE defaults to "local".
                        ok, warnings = _scan_user_compose_content(local_mode_overlay, trusted_library,
                                                                  extension_id=service_dir.name)
                        for w in warnings:
                            print(f"WARNING: {service_dir.name}: {w}", file=sys.stderr)
                        if ok:
                            resolved.append(str(local_mode_overlay.relative_to(script_dir)))
                        else:
                            _note_refused_fragment(service_dir, local_mode_overlay, warnings)

                # Multi-GPU overlay if we have more than 1 GPU
                if gpu_count > 1:
                    multi_gpu_overlay = service_dir / "compose.multigpu.yaml"
                    if multi_gpu_overlay.exists():
                        # Fixed filename, but same content scan applies — see
                        # the gpu/local-mode overlay scans above.
                        ok, warnings = _scan_user_compose_content(multi_gpu_overlay, trusted_library,
                                                                  extension_id=service_dir.name)
                        for w in warnings:
                            print(f"WARNING: {service_dir.name}: {w}", file=sys.stderr)
                        if ok:
                            resolved.append(str(multi_gpu_overlay.relative_to(script_dir)))
                        else:
                            _note_refused_fragment(service_dir, multi_gpu_overlay, warnings)

            except Exception as e:
                # Narrow exception handling to specific parse/structure errors
                yaml_error = isinstance(e, yaml.YAMLError)
                json_error = isinstance(e, json.JSONDecodeError)
                structure_error = isinstance(e, (KeyError, TypeError))

                if yaml_error or json_error or structure_error:
                    print(f"ERROR: Failed to parse manifest for {service_dir.name}: {e}", file=sys.stderr)
                    print(f"  Manifest path: {manifest_path}", file=sys.stderr)
                    print(f"  This service will be skipped. Fix the manifest or disable the service.", file=sys.stderr)
                    if skip_broken:
                        continue
                    else:
                        sys.exit(1)
                else:
                    # Unexpected error — re-raise to crash visibly
                    raise
    except OSError as e:
        print(f"WARNING: Could not scan user-extensions: {e}", file=sys.stderr)

# External host runtimes disable ODS-managed inference and add the Linux
# host-gateway alias to always-on clients. Append this before the operator
# override so explicit local customization remains the final authority.
external_llm_overlay = script_dir / "docker-compose.external-llm.yml"
if external_llm:
    if not external_llm_overlay.exists():
        print("ERROR: EXTERNAL_LLM_URL is set but docker-compose.external-llm.yml is missing", file=sys.stderr)
        sys.exit(1)
    resolved.append("docker-compose.external-llm.yml")

# Optional owner-registered model directories. Unlike untrusted extension
# mounts, these explicitly authorized absolute roots must match the bounded
# registry exactly; no extra Compose keys or writable mounts are accepted.
model_stores_overlay = script_dir / ".model-stores.compose.json"
if model_stores_overlay.exists():
    sys.path.insert(0, str(script_dir / "extensions/services/dashboard-api"))
    try:
        from model_stores import validated_compose_overlay, active_compose_overlay
        from env_values import parse_env_value
        validated_compose_overlay(script_dir)
        active_store_id = "default"
        env_path = script_dir / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip() == "ODS_ACTIVE_MODEL_STORE":
                    active_store_id = parse_env_value(value)
        active_mount = active_compose_overlay(script_dir, active_store_id)
    except (ImportError, ValueError) as exc:
        print(f"ERROR: registered model mounts: {exc}", file=sys.stderr)
        sys.exit(1)
    resolved.append(".model-stores.compose.json")
    resolved = [item for item in resolved if pathlib.Path(item).name != ".active-model-store.compose.json"]
    if active_mount:
        resolved.append(str(active_mount.relative_to(script_dir)))

# Include docker-compose.override.yml if it exists (user customizations).
# Even though the operator placed this file themselves, the resolver runs
# under installer/CI and may handle composes from sources the operator
# trusts less than themselves (cloned repo, restored backup). Apply the
# same content scan as user extensions.
override = script_dir / "docker-compose.override.yml"
if override.exists():
    ok, warnings = _scan_user_compose_content(override)
    for w in warnings:
        print(f"WARNING: docker-compose.override.yml: {w}", file=sys.stderr)
    if ok:
        resolved.append("docker-compose.override.yml")

# macOS cloud installs generate this secret-free, core-only overlay so Open
# WebUI authenticates to LiteLLM with the same LITELLM_KEY as the gateway.
# Strip any caller-supplied occurrence, then append the validated overlay last
# only for Apple cloud mode. This prevents stale profile flags from applying it
# to local/non-Apple stacks or placing it before a user override.
if ods_mode == "cloud" and gpu_backend == "apple" and macos_cloud_auth.exists():
    _append_macos_cloud_auth_overlay(resolved, macos_cloud_auth)

# A successful native Pixel installation keeps these fragments disabled for
# generic extension discovery. Restore their explicit selection after cache
# invalidation, with the same final override order as the macOS installer.
native_activation = script_dir / 'data/pixel-native/preparation/activation.json'
if os.path.lexists(native_activation):
    import importlib.util
    try:
        native_spec = importlib.util.spec_from_file_location('ods_native_stack',
            script_dir / 'installers/macos/lib/pixel-native-stack.py')
        native_stack = importlib.util.module_from_spec(native_spec)
        native_spec.loader.exec_module(native_stack)
        resolved = native_stack.resolve_files(script_dir, resolved)
    except (ValueError, OSError, ImportError):
        print('ERROR: Native Pixel Compose selection needs recovery; retain its installation receipts.', file=sys.stderr)
        sys.exit(1)

def _service_references(service):
    """Services Compose requires to be declared for this service to load."""
    references = set()
    depends_on = service.get("depends_on")
    if isinstance(depends_on, (dict, list)):
        references.update(str(name) for name in depends_on if isinstance(name, str))
    for key in ("network_mode", "pid", "ipc"):
        mode = service.get(key)
        if isinstance(mode, str) and mode.startswith("service:"):
            references.add(mode.split(":", 1)[1])
    for key in ("links", "volumes_from"):
        entries = service.get(key)
        for entry in entries if isinstance(entries, list) else ():
            if isinstance(entry, str) and not entry.startswith("container:"):
                references.add(entry.split(":", 1)[0])
    return references


def _drop_unresolvable_user_extensions(files):
    """Drop user extensions that need a service no remaining file declares.

    Compose refuses the WHOLE project when one service depends on an
    undefined service (required or not), so a refused provider would
    otherwise take every `ods` command down with its dependents. Drops
    cascade transitively; each is reported with the chain back to the
    refusal. ODS's own files are never dropped here. Profiles are not
    modelled: a declared but profiled-out dependency still fails in Compose.
    """
    def extension_of(rel):
        parts = pathlib.PurePath(rel).parts
        return parts[2] if len(parts) > 3 and parts[:2] == ("data", "user-extensions") else None

    declared_by, needs = {}, {}
    for rel in files:
        owner = extension_of(rel)
        try:
            text = (script_dir / rel).read_text(encoding="utf-8")
        except FileNotFoundError:
            continue  # declares nothing; Compose reports the missing file itself
        except OSError:
            if owner is None:
                return files  # the stack itself is unreadable; Compose reports it
            continue
        try:
            data = _compose_policy_load(text) if owner else yaml.safe_load(text)
        except (ValueError, yaml.YAMLError):
            if owner is None:
                return files
            data = None
        services = data.get("services") if isinstance(data, dict) else None
        for name, service in (services.items() if isinstance(services, dict) else ()):
            declared_by.setdefault(str(name), set()).add(owner)
            if owner is not None and isinstance(service, dict):
                for reference in _service_references(service):
                    needs.setdefault(owner, {}).setdefault(reference, str(name))
    unavailable = dict(_refused_services)
    dropped = set()
    changed = True
    while changed:
        changed = False
        for owner in sorted(needs):
            if owner in dropped:
                continue
            for reference, dependent in sorted(needs[owner].items()):
                if declared_by.get(reference, set()) - dropped:
                    continue
                why = unavailable.get(reference, "which no enabled extension or ODS service declares")
                cause = f"service '{dependent}' needs '{reference}', {why}"
                print(f"WARNING: {owner}: skipped because {cause}", file=sys.stderr)
                dropped.add(owner)
                changed = True
                for name, owners in declared_by.items():
                    if owner in owners:
                        unavailable.setdefault(name, f"which {owner} declares but was skipped because {cause}")
                break
    return [rel for rel in files if extension_of(rel) not in dropped]


resolved = _drop_unresolvable_user_extensions(resolved)

# Each extension owns its projection so narrowed installs cannot accidentally
# include unrelated services or require their missing configuration.
import tempfile
projected = []
for fragment in resolved:
    projected.append(fragment)
    path = (script_dir / fragment).resolve()
    contexts = _extension_build_contexts.get(str(path))
    if not contexts:
        continue
    overlay = path.parent / (".ods-build-context-" + path.name + ".json")
    if overlay.is_symlink():
        raise ValueError("Invalid build context overlay")
    fd, temporary = tempfile.mkstemp(prefix=".build-context-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"services": contexts}, stream)
        os.replace(temporary, overlay)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    projected.append(str(overlay.relative_to(script_dir)))
resolved = projected

def to_flags(files):
    return " ".join(f"-f {f}" for f in files)

resolved_flags = to_flags(resolved)

if env_mode:
    def out(key, value):
        safe = str(value).replace("\\", "\\\\").replace('"', '\\"')
        print(f'{key}="{safe}"')
    out("COMPOSE_PRIMARY_FILE", primary)
    out("COMPOSE_FILE_LIST", ",".join(resolved))
    out("COMPOSE_FLAGS", resolved_flags)
else:
    print(resolved_flags)
PY
