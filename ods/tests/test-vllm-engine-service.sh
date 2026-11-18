#!/usr/bin/env bash
# LLM_BACKEND=vllm runs the model in the vllm-server container
# (docker-compose.vllm.yml) instead of the in-stack llama-server, on the one
# NVIDIA GPU the installer picked. This pins the service contract the other
# vLLM pieces build on (container, port, mounts, GPU, healthcheck, command,
# image pin, .env keys, backup exclusion) and the resolver selection: the
# overlay follows docker-compose.nvidia.yml only for LLM_BACKEND=vllm, every
# route it cannot replace is refused, and every other selection is unchanged.
# Needs no Docker daemon.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OVERLAY="${ODS_VLLM_COMPOSE_UNDER_TEST:-$ROOT_DIR/docker-compose.vllm.yml}"
RESOLVER="${ODS_RESOLVER_UNDER_TEST:-$ROOT_DIR/scripts/resolve-compose-stack.sh}"
PYTHON_CMD="${ODS_PYTHON_CMD:-python3}"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

for f in "$OVERLAY" "$RESOLVER" "$ROOT_DIR/docker-compose.base.yml" "$ROOT_DIR/docker-compose.nvidia.yml"; do
    [[ -f "$f" ]] || fail "missing $f"
done

# 1. The overlay's service contract, read as Compose will read it.
"$PYTHON_CMD" - "$OVERLAY" "$ROOT_DIR/docker-compose.base.yml" "$ROOT_DIR/config/dependency-lock.json" \
    "$ROOT_DIR/.env.schema.json" "$ROOT_DIR/config/core-service-ids.json" <<'PY' || fail "docker-compose.vllm.yml contract (see above)"
import http.server
import json
import os
import re
import shlex
import subprocess
import sys
import threading

import yaml

overlay_path, base_path, lock_path, schema_path, core_ids_path = sys.argv[1:6]
text = open(overlay_path, encoding="utf-8").read()
overlay = yaml.safe_load(text)
base = yaml.safe_load(open(base_path, encoding="utf-8"))
lock = json.load(open(lock_path, encoding="utf-8"))
schema = json.load(open(schema_path, encoding="utf-8"))["properties"]
core_ids = json.load(open(core_ids_path, encoding="utf-8"))
errors = []


def check(condition, message):
    if not condition:
        errors.append(message)


services = overlay.get("services") or {}
check(set(services) == {"llama-server", "vllm-server"},
      f"the overlay may only switch llama-server off and add vllm-server, got {sorted(services)}")
check(services.get("llama-server") == {"profiles": ["local-inference"], "restart": "no"},
      f"llama-server must be profiled out and not restarted: {services.get('llama-server')}")
vllm = services.get("vllm-server") or {}

# Image: the digest pin in config/dependency-lock.json, overridable by VLLM_IMAGE.
entry = next((e for e in lock["entries"] if e.get("id") == "vllm.vllm-server"), {})
pinned = str(entry.get("value", ""))
check(entry.get("path") == "docker-compose.vllm.yml"
      and re.fullmatch(r"vllm/vllm-openai:v[0-9][^@\s]*@sha256:[0-9a-f]{64}", pinned),
      f"dependency-lock.json needs a digest-pinned vllm.vllm-server entry, got {entry}")
check(vllm.get("image") == "${VLLM_IMAGE:-" + pinned + "}",
      f"vllm-server must run ${{VLLM_IMAGE}} defaulting to the locked pin, got {vllm.get('image')!r}")
allowed = [i for i in lock.get("allow_variable_refs", []) if i.get("path") == "docker-compose.vllm.yml"]
check(len(allowed) == 1 and allowed[0].get("value") == vllm.get("image") and allowed[0].get("default") == pinned,
      "allow_variable_refs must document the VLLM_IMAGE reference with the locked default")

check(vllm.get("container_name") == "ods-vllm-server", "the container must be ods-vllm-server")
check(vllm.get("restart") == "unless-stopped", "vllm-server must restart unless stopped")
check("profiles" not in vllm, "vllm-server must start whenever the overlay is selected")
check("ports" not in vllm, "vllm-server must not publish a host port")
check(vllm.get("security_opt") == ["no-new-privileges:true"], "vllm-server must set no-new-privileges")
check(str(vllm.get("shm_size")) == "16g", "vllm-server needs a 16g /dev/shm")
check(vllm.get("logging") == base.get("x-logging"), "vllm-server must use the stack's default logging policy")
llama = base["services"]["llama-server"]
check(sorted(vllm.get("networks") or ["default"]) == sorted(llama.get("networks") or ["default"]),
      "vllm-server must join the networks llama-server uses")

env = vllm.get("environment") or []
env = dict(item.split("=", 1) for item in env) if isinstance(env, list) else {k: str(v) for k, v in env.items()}
for key, value in {"VLLM_ENGINE_READY_TIMEOUT_S": "3600", "HF_HUB_OFFLINE": "1",
                   "VLLM_NO_USAGE_STATS": "1", "DO_NOT_TRACK": "1"}.items():
    check(env.get(key) == value, f"vllm-server must set {key}={value}")
for key in ("CUDA_CACHE_PATH", "TILELANG_CACHE_DIR", "TRITON_CACHE_DIR", "TORCHINDUCTOR_CACHE_DIR"):
    check(env.get(key, "").startswith("/root/.cache/"),
          f"{key} must point into the mounted cache folder, or a recreate rebuilds that kernel cache")

# Mounts: one model folder read-only, the compile/autotune cache writable.
mounts = {}
for spec in vllm.get("volumes") or []:
    match = re.fullmatch(r"(?P<src>.+):(?P<dst>/[^:]*)(?::(?P<opts>[^:]+))?", str(spec))
    check(match is not None, f"unexpected volume {spec!r}")
    if match:
        mounts[match["dst"]] = (match["src"], set(filter(None, (match["opts"] or "").split(","))))
check(set(mounts) == {"/model", "/root/.cache"}, f"vllm-server must mount /model and /root/.cache only, got {sorted(mounts)}")
source, options = mounts.get("/model", ("", set()))
check(re.fullmatch(r"\./data/models/\$\{VLLM_MODEL_DIR:\?[^}]+\}", source),
      f"/model must be the data/models folder a required VLLM_MODEL_DIR names, got {source!r}")
check({"ro", "z"} <= options, "/model must be read-only with the SELinux z label")
source, options = mounts.get("/root/.cache", ("", set()))
check(source == "./data/vllm/cache" and "z" in options and "ro" not in options,
      "/root/.cache must be ./data/vllm/cache, writable, with the SELinux z label")

# GPU: exactly the device VLLM_GPU_UUID names.
devices = (((vllm.get("deploy") or {}).get("resources") or {}).get("reservations") or {}).get("devices")
device = devices[0] if isinstance(devices, list) and len(devices) == 1 else {}
check(device.get("driver") == "nvidia" and device.get("capabilities") == ["gpu"] and "count" not in device,
      f"vllm-server must reserve one NVIDIA GPU by id, got {devices}")
ids = device.get("device_ids")
check(isinstance(ids, list) and len(ids) == 1 and re.fullmatch(r"\$\{VLLM_GPU_UUID:\?[^}]+\}", str(ids[0])),
      f"the reservation must name only a required VLLM_GPU_UUID, got {ids}")

# Healthcheck: no curl, a long first-start grace period.
health = vllm.get("healthcheck") or {}
test = health.get("test") or []
check(test[:3] == ["CMD", "python3", "-c"] and len(test) == 4, f"the healthcheck must run python3 -c, got {test}")
check(not any(tool in " ".join(map(str, test)) for tool in ("curl", "wget")), "the healthcheck must not need curl or wget")


def seconds(value):
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", str(value))
    return None if not match or not any(match.groups()) else sum(
        int(part or 0) * unit for part, unit in zip(match.groups(), (3600, 60, 1)))


start_period, interval = seconds(health.get("start_period")), seconds(health.get("interval"))
check(start_period is not None and start_period >= 1800, "the healthcheck start_period must allow at least 30 minutes")
check(interval is not None and interval <= 60 and seconds(health.get("timeout")) is not None,
      "the healthcheck needs an interval of at most a minute and a timeout")
check(isinstance(health.get("retries"), int) and health["retries"] >= 3, "the healthcheck needs at least 3 retries")

# Run the probe itself: vLLM's ready /health is an empty 200; 503 and a
# refused connection are unhealthy.
probe = str(test[3]) if len(test) == 4 else ""
check("http://127.0.0.1:8000/health" in probe, "the probe must ask http://127.0.0.1:8000/health")
status = {"code": 200}


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(status["code"] if self.path == "/health" else 404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


server = http.server.HTTPServer(("127.0.0.1", 0), Health)
threading.Thread(target=server.serve_forever, daemon=True).start()
port = server.server_address[1]
plain_env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}


def probe_rc():
    code = probe.replace("127.0.0.1:8000", f"127.0.0.1:{port}")
    return subprocess.run([sys.executable, "-c", code], env=plain_env, capture_output=True, timeout=30).returncode


if probe:
    check(probe_rc() == 0, "the probe must accept vLLM's empty 200 /health")
    status["code"] = 503
    check(probe_rc() != 0, "the probe must fail on a 503 /health")
    server.shutdown()
    server.server_close()
    check(probe_rc() != 0, "the probe must fail when nothing listens on the port")

# Command: the string form Compose splits like a shell, in the contract order.
command = vllm.get("command")
check(isinstance(command, str), "vllm-server's command must be the string form")
VAR = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:-|:\?)(?P<arg>[^}]*))?\}")


def interpolate(value, values):
    def replace(match):
        found = values.get(match["name"], "")
        if not found and match["op"] == ":?":
            raise KeyError(match["name"])
        return found or (match["arg"] if match["op"] == ":-" else "")
    return VAR.sub(replace, value)


if isinstance(command, str):
    args = "--max-model-len 131072 --speculative-config '{\"method\":\"mtp\",\"num_speculative_tokens\":3}'"
    argv = shlex.split(interpolate(command, {"VLLM_SERVED_MODEL": "GLM-5.3-Flash-NVFP4", "VLLM_ARGS": args}))
    check(argv == ["--model", "/model", "--served-model-name", "GLM-5.3-Flash-NVFP4", "--host", "0.0.0.0",
                   "--port", "8000", "--gpu-memory-utilization", "0.90", "--max-model-len", "131072",
                   "--speculative-config", '{"method":"mtp","num_speculative_tokens":3}'],
          f"vllm-server's command breaks the contract: {argv}")
    argv = shlex.split(interpolate(command, {"VLLM_SERVED_MODEL": "m", "VLLM_GPU_MEMORY_UTILIZATION": "0.5"}))
    check(argv[-2:] == ["--gpu-memory-utilization", "0.5"], f"VLLM_GPU_MEMORY_UTILIZATION is not applied: {argv}")
    try:
        interpolate(command, {})
        errors.append("the command must require VLLM_SERVED_MODEL")
    except KeyError:
        pass

# Every variable is a documented .env key; the GPU, folder and model id are required.
names = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", text))
check(names == {"VLLM_IMAGE", "VLLM_MODEL_DIR", "VLLM_SERVED_MODEL", "VLLM_ARGS", "VLLM_GPU_UUID",
                "VLLM_GPU_MEMORY_UTILIZATION"}, f"unexpected variables in the overlay: {sorted(names)}")
check(set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?", text)) == {"VLLM_MODEL_DIR", "VLLM_SERVED_MODEL", "VLLM_GPU_UUID"},
      "VLLM_MODEL_DIR, VLLM_SERVED_MODEL and VLLM_GPU_UUID must stop Compose when unset")
undocumented = sorted(name for name in names if name not in schema)
check(not undocumented, f".env.schema.json does not document {undocumented}")
utilization = schema.get("VLLM_GPU_MEMORY_UTILIZATION", {})
check(utilization.get("type") == "number" and utilization.get("maximum") == 1 and utilization.get("default") == 0.9,
      "VLLM_GPU_MEMORY_UTILIZATION must be a number at most 1, default 0.9")
backend = schema.get("LLM_BACKEND", {})
check("vllm" in backend["enum"] if "enum" in backend else "vllm" in backend.get("description", ""),
      "LLM_BACKEND in .env.schema.json must list vllm")
check("vllm-server" in core_ids, "config/core-service-ids.json must protect vllm-server from shadowing extensions")

for error in errors:
    print(f"  - {error}")
sys.exit(1 if errors else 0)
PY
pass "docker-compose.vllm.yml runs one pinned vLLM on the installer's GPU, mounts, probe and command as contracted"

# 2. The cache is a deliberate backup decision.
# shellcheck source=/dev/null
. "$ROOT_DIR/lib/backup-paths.sh"
[[ " ${ODS_BACKUP_EXCLUDED_DATA_PATHS[*]} " == *" data/vllm "* ]] \
    || fail "lib/backup-paths.sh must exclude data/vllm (rebuilt compile cache) from backups"
[[ " ${ODS_USER_DATA_PATHS[*]} " != *" data/vllm "* ]] || fail "data/vllm is a cache, not user data"
pass "backups skip the vLLM compile cache"

# 3. Resolver selection. Each run starts from a clean model route; ROUTE_ENV
# adds the selector a case needs.
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
ERR_FILE="$TMP_DIR/resolver.err"
ROUTE_ENV=()
FILES=""
# resolve LLM_BACKEND|<unset> RESOLVER_ARGS... -> FILES, one Compose file per line
resolve() {
    local backend="$1" out rc=0
    shift
    local -a route=(-u LLM_BACKEND -u NATIVE_LLM_BASE_URL -u EXTERNAL_LLM_URL -u ODS_EXTERNAL_LLM_SELECTED
                    -u AMD_INFERENCE_BACKEND -u ODS_SKIP_GPU_OVERLAYS -u ODS_SKIP_GPU_OVERLAYS_FOR)
    [[ "$backend" == "<unset>" ]] || route+=("LLM_BACKEND=$backend")
    out="$(env "${route[@]}" ${ROUTE_ENV[@]+"${ROUTE_ENV[@]}"} "$RESOLVER" "$@" 2>"$ERR_FILE")" || rc=$?
    FILES="$(printf '%s\n' "$out" | tr '\\' '/' | awk '{ for (i = 1; i <= NF; i++) if ($i != "-f") print $i }')"
    return "$rc"
}
has_file() { grep -qxF -- "$1" <<<"$FILES"; }
first_files() { head -n "$1" <<<"$FILES" | paste -sd, -; }
without() { awk -v drop="$1" 'index($0, drop) == 0' <<<"$2"; }

real=(--script-dir "$ROOT_DIR" --tier NV_ULTRA --gpu-backend nvidia --ods-mode local)
resolve "<unset>" "${real[@]}" || fail "default NVIDIA resolution failed: $(cat "$ERR_FILE")"
default_files="$FILES"
if has_file docker-compose.vllm.yml; then fail "the default NVIDIA stack selected the vLLM overlay"; fi
has_file extensions/services/litellm/compose.local.yaml \
    || fail "the default NVIDIA stack lost LiteLLM's llama-server health overlay"
resolve llama-server "${real[@]}" || fail "LLM_BACKEND=llama-server resolution failed: $(cat "$ERR_FILE")"
[[ "$FILES" == "$default_files" ]] || fail "LLM_BACKEND=llama-server changed the default selection"
pass "unset and llama-server LLM_BACKEND keep today's NVIDIA stack"

resolve vllm "${real[@]}" || fail "LLM_BACKEND=vllm resolution failed: $(cat "$ERR_FILE")"
[[ "$(first_files 3)" == "docker-compose.base.yml,docker-compose.nvidia.yml,docker-compose.vllm.yml" ]] \
    || fail "the vLLM overlay must follow the NVIDIA overlay: $(first_files 3)"
if grep -qF compose.local.yaml <<<"$FILES"; then
    fail "the vLLM stack kept a local-mode overlay that waits on the disabled llama-server"
fi
[[ "$(without docker-compose.vllm.yml "$FILES")" == "$(without /compose.local.yaml "$default_files")" ]] \
    || fail "LLM_BACKEND=vllm must only add the overlay and drop the llama-server health overlays"
pass "LLM_BACKEND=vllm layers base + nvidia + vllm and drops the llama-server waits"

resolve vllm "${real[@]}" --gpu-count 2 || fail "multi-GPU vLLM resolution failed: $(cat "$ERR_FILE")"
[[ "$(first_files 4)" == "docker-compose.base.yml,docker-compose.nvidia.yml,docker-compose.vllm.yml,docker-compose.multigpu-nvidia.yml" ]] \
    || fail "multi-GPU vLLM stack order: $(first_files 4)"
resolve vllm "${real[@]}" --profile-overlays docker-compose.base.yml,docker-compose.nvidia.yml \
    || fail "installer-profile vLLM resolution failed: $(cat "$ERR_FILE")"
[[ "$(first_files 3)" == "docker-compose.base.yml,docker-compose.nvidia.yml,docker-compose.vllm.yml" ]] \
    || fail "an installer hardware profile lost the vLLM overlay: $(first_files 3)"
pass "the vLLM overlay follows the NVIDIA overlay on multi-GPU and installer-profile stacks"

# refuse REASON RESOLVER_ARGS...: LLM_BACKEND=vllm must stop with REASON and no stack.
refuse() {
    local reason="$1"
    shift
    if resolve vllm "$@"; then
        fail "LLM_BACKEND=vllm was accepted with $* ${ROUTE_ENV[*]-}"
    fi
    [[ -z "$FILES" ]] || fail "a refused vLLM selection still printed a stack"
    grep -qF "LLM_BACKEND=vllm cannot be used" "$ERR_FILE" && grep -qF -- "$reason" "$ERR_FILE" \
        || fail "refusal for $* ${ROUTE_ENV[*]-} does not say '$reason': $(cat "$ERR_FILE")"
}
ROUTE_ENV=(NATIVE_LLM_BASE_URL=http://localhost:8080); refuse NATIVE_LLM_BASE_URL "${real[@]}"
ROUTE_ENV=(EXTERNAL_LLM_URL=http://127.0.0.1:11434); refuse EXTERNAL_LLM_URL "${real[@]}"
ROUTE_ENV=(ODS_EXTERNAL_LLM_SELECTED=true); refuse EXTERNAL_LLM_URL "${real[@]}"
ROUTE_ENV=()
refuse "cloud mode" --script-dir "$ROOT_DIR" --tier NV_ULTRA --gpu-backend nvidia --ods-mode cloud
refuse "cloud mode" --script-dir "$ROOT_DIR" --tier CLOUD --gpu-backend nvidia --ods-mode local
for backend in amd cpu apple intel; do
    refuse "GPU backend is $backend" --script-dir "$ROOT_DIR" --tier 1 --gpu-backend "$backend" --ods-mode local
done
refuse "does not include docker-compose.nvidia.yml" --script-dir "$ROOT_DIR" --tier AP_PRO --gpu-backend nvidia --ods-mode local
pass "LLM_BACKEND=vllm is refused with host-native, external and cloud routes and non-NVIDIA GPUs"

# 4. An installed .env selects it too, and user-extension llama-server waits
# are dropped with it. A minimal install tree keeps this independent of the
# shipped extensions.
tree="$TMP_DIR/install"
mkdir -p "$tree/lib" "$tree/data/user-extensions/probe-ext"
cp "$ROOT_DIR/docker-compose.base.yml" "$ROOT_DIR/docker-compose.nvidia.yml" "$tree/"
cp "$OVERLAY" "$tree/docker-compose.vllm.yml"
cp "$ROOT_DIR/lib/python-cmd.sh" "$tree/lib/"
printf '%s\n' 'schema_version: ods.services.v1' 'service:' '  id: probe-ext' '  name: Probe' \
    '  compose_file: compose.yaml' > "$tree/data/user-extensions/probe-ext/manifest.yaml"
printf '%s\n' 'services:' '  probe-ext:' '    image: busybox:1.36.1' \
    > "$tree/data/user-extensions/probe-ext/compose.yaml"
printf '%s\n' 'services:' '  probe-ext:' '    depends_on:' '      llama-server:' \
    '        condition: service_healthy' > "$tree/data/user-extensions/probe-ext/compose.local.yaml"
installed=(--script-dir "$tree" --tier NV_ULTRA --gpu-backend nvidia --ods-mode local)

printf 'LLM_BACKEND=llama-server\n' > "$tree/.env"
resolve "<unset>" "${installed[@]}" || fail "installed llama-server resolution failed: $(cat "$ERR_FILE")"
if has_file docker-compose.vllm.yml; then fail "an installed LLM_BACKEND=llama-server selected the vLLM overlay"; fi
has_file data/user-extensions/probe-ext/compose.local.yaml || fail "the fixture's local-mode overlay was not selected"

printf 'LLM_BACKEND="vllm"\n' > "$tree/.env"
resolve "<unset>" "${installed[@]}" || fail "installed vLLM resolution failed: $(cat "$ERR_FILE")"
[[ "$(first_files 3)" == "docker-compose.base.yml,docker-compose.nvidia.yml,docker-compose.vllm.yml" ]] \
    || fail "an installed LLM_BACKEND=vllm did not select the overlay: $(first_files 3)"
if has_file data/user-extensions/probe-ext/compose.local.yaml; then
    fail "the vLLM stack kept a user extension's wait on the disabled llama-server"
fi
has_file data/user-extensions/probe-ext/compose.yaml || fail "the vLLM stack dropped the user extension itself"

rm "$tree/docker-compose.vllm.yml"
if resolve "<unset>" "${installed[@]}"; then
    fail "LLM_BACKEND=vllm resolved without docker-compose.vllm.yml"
fi
grep -qF "docker-compose.vllm.yml is missing" "$ERR_FILE" \
    || fail "a missing vLLM overlay is not named: $(cat "$ERR_FILE")"
pass "an installed .env selects the overlay, drops extension llama-server waits, and a missing overlay is named"

echo "[PASS] vLLM engine service"
