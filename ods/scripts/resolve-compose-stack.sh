#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(pwd)"
TIER="1"
GPU_BACKEND="nvidia"
PROFILE_OVERLAYS=""
ENV_MODE="false"
SKIP_BROKEN="false"
GPU_COUNT="1"
ODS_MODE="${ODS_MODE:-local}"

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

"$PYTHON_CMD" - "$SCRIPT_DIR" "$TIER" "$GPU_BACKEND" "$PROFILE_OVERLAYS" "$ENV_MODE" "$SKIP_BROKEN" "$GPU_COUNT" "$ODS_MODE" <<'PY'
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
lemonade_external = (
    os.environ.get("LEMONADE_EXTERNAL", "").lower() in {"1", "true", "yes", "on"}
    or (
        os.environ.get("AMD_INFERENCE_RUNTIME", "").lower() == "lemonade"
        and os.environ.get("AMD_INFERENCE_MANAGED", "").lower() == "false"
    )
)

IS_DARWIN = platform.system() == "Darwin"
APPLE_OVERLAY = "installers/macos/docker-compose.macos.yml" if IS_DARWIN else "docker-compose.apple.yml"

def existing(overlays):
    return all((script_dir / f).exists() for f in overlays)

resolved = []
primary = "docker-compose.yml"

if profile_overlays and existing(profile_overlays):
    resolved = profile_overlays
    primary = profile_overlays[-1]
elif lemonade_external and ods_mode == "lemonade":
    if existing(["docker-compose.base.yml", "docker-compose.cloud.yml", "docker-compose.lemonade-external.yml"]):
        resolved = ["docker-compose.base.yml", "docker-compose.cloud.yml", "docker-compose.lemonade-external.yml"]
        primary = "docker-compose.lemonade-external.yml"
    elif existing(["docker-compose.base.yml", "docker-compose.cloud.yml"]):
        resolved = ["docker-compose.base.yml", "docker-compose.cloud.yml"]
        primary = "docker-compose.cloud.yml"
    elif existing(["docker-compose.base.yml"]):
        resolved = ["docker-compose.base.yml"]
        primary = "docker-compose.base.yml"
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

# If external LLM is configured, disable local llama-server via docker-compose.cloud.yml
external_llm = os.environ.get("EXTERNAL_LLM_URL", "")
if external_llm and "docker-compose.cloud.yml" not in resolved:
    if (script_dir / "docker-compose.cloud.yml").exists():
        resolved.append("docker-compose.cloud.yml")

if not resolved:
    resolved = [primary]

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

# Capabilities and security_opt strings that grant container escape primitives.
_DANGEROUS_CAPS = {
    "SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE", "NET_RAW",
    "DAC_OVERRIDE", "SETUID", "SETGID", "SYS_MODULE",
    "SYS_RAWIO", "ALL",
}
_DANGEROUS_SECURITY_OPTS = {
    "seccomp:unconfined", "apparmor:unconfined", "label:disable",
}

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


def _scan_user_compose_content(compose_path):
    """Reject compose fragments containing dangerous directives.

    Mirrors dashboard-api/routers/extensions.py:_scan_compose_content (without
    the FastAPI HTTPException dependency). Returns ``(ok, warnings)``: ``ok``
    is False on any rejection, ``warnings`` is a list of human-readable
    messages. User-extension contexts are always untrusted at the resolver
    layer — no ``trusted=True`` exemption.
    """
    try:
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as e:
        return (False, [f"invalid compose file {compose_path}: {e}"])

    if not isinstance(data, dict):
        return (False, [f"compose file {compose_path} must be a YAML mapping"])

    warnings = []
    services = data.get("services", {})
    if not isinstance(services, dict):
        return (True, warnings)

    ok = True

    def reject(msg):
        nonlocal ok
        ok = False
        warnings.append(msg)

    for svc_name, svc_def in services.items():
        if not isinstance(svc_def, dict):
            continue
        # Name-collision: user-extension service names must not shadow
        # built-in core services in the compose merge. Mirrors the
        # dashboard-api install endpoint's skip_name_collision=False path.
        if svc_name in _CORE_SERVICE_IDS:
            reject(f"service '{svc_name}' collides with a built-in core service name")
        if svc_def.get("privileged") is True:
            reject(f"service '{svc_name}' uses privileged mode")
        if "build" in svc_def:
            reject(f"service '{svc_name}' uses a local build — only pre-built images are allowed for user extensions")
        user = svc_def.get("user")
        if user is not None and str(user).split(":")[0] in ("root", "0"):
            reject(f"service '{svc_name}' runs as root")
        if svc_def.get("network_mode") == "host":
            reject(f"service '{svc_name}' uses host network mode")
        if svc_def.get("pid") == "host":
            reject(f"service '{svc_name}' uses host PID namespace")
        if svc_def.get("ipc") == "host":
            reject(f"service '{svc_name}' uses host IPC namespace")
        if svc_def.get("userns_mode") == "host":
            reject(f"service '{svc_name}' uses host user namespace")
        cap_add = svc_def.get("cap_add", [])
        if isinstance(cap_add, list):
            for cap in cap_add:
                if str(cap).upper() in _DANGEROUS_CAPS:
                    reject(f"service '{svc_name}' adds dangerous capability: {cap}")
        security_opt = svc_def.get("security_opt", [])
        if isinstance(security_opt, list):
            for opt in security_opt:
                opt_str = str(opt).lower().replace("=", ":")
                if opt_str in _DANGEROUS_SECURITY_OPTS:
                    reject(f"service '{svc_name}' uses dangerous security_opt '{opt}'")
        if svc_def.get("devices"):
            reject(f"service '{svc_name}' declares devices")
        deploy = svc_def.get("deploy")
        if isinstance(deploy, dict):
            resources = deploy.get("resources")
            if isinstance(resources, dict):
                reservations = resources.get("reservations")
                if isinstance(reservations, dict) and reservations.get("devices"):
                    reject(f"service '{svc_name}' requests GPU passthrough via deploy.resources.reservations.devices")
        volumes = svc_def.get("volumes", [])
        if isinstance(volumes, list):
            for vol in volumes:
                if isinstance(vol, dict):
                    source = str(vol.get("source", ""))
                    target = str(vol.get("target", ""))
                    if "docker.sock" in source or "docker.sock" in target:
                        reject(f"service '{svc_name}' mounts the Docker socket")
                    if source.startswith("/"):
                        reject(f"service '{svc_name}' bind-mounts absolute host path '{source}'")
                    continue
                vol_str = str(vol)
                if "docker.sock" in vol_str:
                    reject(f"service '{svc_name}' mounts the Docker socket")
                vol_parts = vol_str.split(":")
                if len(vol_parts) >= 2 and vol_parts[0].startswith("/"):
                    reject(f"service '{svc_name}' bind-mounts absolute host path '{vol_parts[0]}'")
        if svc_def.get("extra_hosts"):
            reject(f"service '{svc_name}' declares extra_hosts")
        if svc_def.get("sysctls"):
            reject(f"service '{svc_name}' declares sysctls")
        labels = svc_def.get("labels", [])
        if isinstance(labels, dict):
            label_keys = labels.keys()
        elif isinstance(labels, list):
            label_keys = [lbl.split("=", 1)[0] for lbl in labels if isinstance(lbl, str)]
        else:
            label_keys = []
        for lk in label_keys:
            if str(lk).startswith("com.docker.compose."):
                reject(f"service '{svc_name}' uses reserved Docker Compose label '{lk}'")
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

    top_volumes = data.get("volumes", {})
    if isinstance(top_volumes, dict):
        for vol_name, vol_def in top_volumes.items():
            if not isinstance(vol_def, dict):
                continue
            driver_opts = vol_def.get("driver_opts", {})
            if not isinstance(driver_opts, dict):
                continue
            vol_type = str(driver_opts.get("type", "")).lower()
            device = str(driver_opts.get("device", ""))
            if vol_type in ("none", "bind") and device.startswith("/"):
                reject(f"named volume '{vol_name}' uses driver_opts to bind-mount host path '{device}'")

    return (ok, warnings)


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
            # Get compose file from manifest
            compose_rel = service.get("compose_file", "")
            if compose_rel and not compose_rel.endswith(".disabled"):
                # Validate compose_file stays inside its extension's directory.
                # Path.relative_to() does lexical part-prefix matching, so a
                # `..`-traversal ("../../../etc/passwd") still string-matches
                # script_dir without escape detection; an absolute compose_file
                # ("/etc/shadow") replaces service_dir entirely under `/`. Both
                # would otherwise reach docker compose `-f`. Boundary-check on
                # fully resolved paths, but keep the unresolved compose_path
                # for the emit so the existing relative_to(script_dir) contract
                # still holds on systems where script_dir contains symlinks
                # (macOS /var -> /private/var would mismatch otherwise).
                compose_path = service_dir / compose_rel
                try:
                    compose_path.resolve().relative_to(service_dir.resolve())
                except ValueError:
                    print(f"WARNING: {service_dir.name}: compose_file '{compose_rel}' "
                          f"escapes the extension directory; skipping",
                          file=sys.stderr)
                    continue
                if compose_path.exists():
                    resolved.append(str(compose_path.relative_to(script_dir)))
                elif (service_dir / f"{compose_rel}.disabled").exists():
                    continue  # Service disabled — skip all overlays
                else:
                    print(f"WARNING: {service_dir.name}: compose_file '{compose_rel}' not found, skipping overlays", file=sys.stderr)
                    continue  # Base compose missing — skip GPU/mode overlays
            # GPU-specific overlay (filesystem discovery — not in manifest)
            gpu_overlay = service_dir / f"compose.{gpu_backend}.yaml"
            if gpu_overlay.exists():
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

# Discover enabled user-installed extensions (from dashboard portal)
user_ext_dir = script_dir / "data" / "user-extensions"
if user_ext_dir.exists():
    try:
        for service_dir in sorted(user_ext_dir.iterdir()):
            if not service_dir.is_dir():
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
                # Apply gpu_backends filter — same predicate as the built-in loop above.
                # Gated on isinstance(manifest, dict) so the manifest-less compat
                # carve-out (legacy user extensions that pre-date the manifest convention)
                # falls through unfiltered.
                if isinstance(manifest, dict):
                    backends = service.get("gpu_backends", ["amd", "nvidia"])
                    # "none" means CPU-only — compatible with any GPU backend
                    if gpu_backend not in backends and "all" not in backends and "none" not in backends:
                        continue
                # Get compose file from manifest, default to compose.yaml
                compose_rel = service.get("compose_file", "compose.yaml")
                if compose_rel and not compose_rel.endswith(".disabled"):
                    # Boundary check: a malicious manifest could point compose_file
                    # at "../../etc/passwd" or an absolute path. Resolve both sides
                    # so a `/var → /private/var` symlink on macOS doesn't false-flag.
                    compose_path = service_dir / compose_rel
                    try:
                        compose_path.resolve().relative_to(service_dir.resolve())
                    except ValueError:
                        print(f"WARNING: {service_dir.name}: compose_file '{compose_rel}' "
                              f"escapes the extension directory; skipping",
                              file=sys.stderr)
                        continue
                    if compose_path.exists():
                        # Scan content before appending — user-ext composes are
                        # untrusted. The dashboard-api scans at install time;
                        # the resolver runs every `ods` invocation, so a
                        # tampered-with compose dropped under data/user-extensions
                        # without going through the install API would otherwise
                        # bypass scanning entirely.
                        ok, warnings = _scan_user_compose_content(compose_path)
                        for w in warnings:
                            print(f"WARNING: {service_dir.name}: {w}", file=sys.stderr)
                        if not ok:
                            continue
                        resolved.append(str(compose_path.relative_to(script_dir)))
                    elif (service_dir / f"{compose_rel}.disabled").exists():
                        continue  # Service disabled — skip all overlays
                    else:
                        # No base compose — skip overlays for this user extension
                        continue
                # GPU-specific overlay (filesystem discovery — not in manifest)
                gpu_overlay = service_dir / f"compose.{gpu_backend}.yaml"
                if gpu_overlay.exists():
                    # Fixed filename so traversal isn't possible, but the same
                    # security checks apply to the overlay's content.
                    ok, warnings = _scan_user_compose_content(gpu_overlay)
                    for w in warnings:
                        print(f"WARNING: {service_dir.name}: {w}", file=sys.stderr)
                    if ok:
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
                        ok, warnings = _scan_user_compose_content(local_mode_overlay)
                        for w in warnings:
                            print(f"WARNING: {service_dir.name}: {w}", file=sys.stderr)
                        if ok:
                            resolved.append(str(local_mode_overlay.relative_to(script_dir)))

                # Multi-GPU overlay if we have more than 1 GPU
                if gpu_count > 1:
                    multi_gpu_overlay = service_dir / "compose.multigpu.yaml"
                    if multi_gpu_overlay.exists():
                        # Fixed filename, but same content scan applies — see
                        # the gpu/local-mode overlay scans above.
                        ok, warnings = _scan_user_compose_content(multi_gpu_overlay)
                        for w in warnings:
                            print(f"WARNING: {service_dir.name}: {w}", file=sys.stderr)
                        if ok:
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
    except OSError as e:
        print(f"WARNING: Could not scan user-extensions: {e}", file=sys.stderr)

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
