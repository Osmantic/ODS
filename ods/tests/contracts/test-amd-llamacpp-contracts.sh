#!/usr/bin/env bash
# AMD on upstream llama.cpp: compose, image pin and runtime contracts.
#
# AMD GPUs run the official ggml-org llama.cpp server image (Vulkan by default,
# ROCm opt-in) through the same base command, /health check and model path as
# NVIDIA and CPU. These checks keep the Lemonade-era build, entrypoint and
# routes from coming back and keep every AMD image copy in agreement.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

VULKAN_IMAGE="ghcr.io/ggml-org/llama.cpp:server-vulkan-b9014@sha256:15c30b560d61ead1e08bee837503203a776fd968736118e313240c32157fd973"
ROCM_IMAGE="ghcr.io/ggml-org/llama.cpp:server-rocm-b9014@sha256:68403f82fe496302bb1c681bab2ee569a04cc13da8fcf14456376b485562236d"

# Run a Python assertion block against the repository; prints FAIL lines.
py_check() {
    local label="$1" output
    shift
    if output="$(python3 - "$@" 2>&1)"; then
        pass "$label"
    else
        fail "$label"
        printf '%s\n' "$output" | sed 's/^/    /'
    fi
}

# ---------------------------------------------------------------------------
# 1. Compose files and retired Lemonade build inputs
# ---------------------------------------------------------------------------
echo "[contract] AMD compose files exist; the Lemonade build is gone"
for f in docker-compose.base.yml docker-compose.amd.yml docker-compose.amd-rocm.yml \
         docker-compose.multigpu-amd.yml docker-compose.host-native-llm.yml \
         extensions/services/litellm/compose.yaml \
         extensions/services/litellm/compose.amd.yaml \
         extensions/services/litellm/compose.local.yaml; do
    if [[ -f "$f" ]]; then
        pass "exists: $f"
    else
        fail "missing: $f"
    fi
done
for f in extensions/services/llama-server/Dockerfile.amd \
         extensions/services/llama-server/lemonade-entrypoint.sh \
         docker-compose.lemonade-external.yml; do
    if [[ -e "$f" ]]; then
        fail "retired file still shipped: $f"
    else
        pass "retired: $f"
    fi
done

# ---------------------------------------------------------------------------
# 2. The AMD overlays use the base llama.cpp launch with pinned images
# ---------------------------------------------------------------------------
echo "[contract] AMD overlays: official images, base command, /dev/dri only for Vulkan"
py_check "docker-compose.amd.yml: Vulkan image, /dev/dri + GPU groups, base command and /health" "$VULKAN_IMAGE" <<'PY'
import sys
import yaml

vulkan = sys.argv[1]
doc = yaml.safe_load(open("docker-compose.amd.yml", encoding="utf-8"))
errors = []
llama = doc["services"]["llama-server"]
if llama.get("image") != vulkan:
    errors.append(f"image must be the pinned Vulkan image, got {llama.get('image')!r}")
for key in ("build", "entrypoint", "command", "healthcheck", "expose", "volumes"):
    if key in llama:
        errors.append(f"llama-server must inherit {key!r} from the base service (found an override)")
if llama.get("devices") != ["/dev/dri:/dev/dri"]:
    errors.append(f"Vulkan needs /dev/dri only (no /dev/kfd), got {llama.get('devices')!r}")
if llama.get("group_add") != ["${VIDEO_GID:-44}", "${RENDER_GID:-992}"]:
    errors.append(f"group_add must carry the video and render GIDs, got {llama.get('group_add')!r}")
env = "\n".join(llama.get("environment") or [])
for retired in ("LEMONADE", "HSA_OVERRIDE_GFX_VERSION", "HSA_XNACK", "ROCBLAS"):
    if retired in env:
        errors.append(f"Vulkan llama-server must not receive {retired}")
if doc.get("volumes"):
    errors.append(f"the AMD overlay must not declare named volumes: {sorted(doc['volumes'])}")
api_env = doc["services"]["dashboard-api"]["environment"]
for required in ("GPU_BACKEND=amd", "LLM_BACKEND=llama-server", "LLM_API_BASE_PATH=/v1",
                 "AMD_INFERENCE_RUNTIME=${AMD_INFERENCE_RUNTIME:-llama-server}",
                 "AMD_INFERENCE_BACKEND=${AMD_INFERENCE_BACKEND:-vulkan}",
                 "AMD_INFERENCE_RUNTIME_MODE=${AMD_INFERENCE_RUNTIME_MODE:-linux-container}"):
    if required not in api_env:
        errors.append(f"dashboard-api must receive {required}")
if any("LLAMA_METRICS_PORT" in item for item in api_env):
    errors.append("llama.cpp serves /metrics on 8080; LLAMA_METRICS_PORT must not be set")
webui = doc["services"].get("open-webui", {}).get("environment", {})
if "OPENAI_API_KEY" in webui or "OPENAI_API_BASE_URL" in webui:
    errors.append("Open WebUI routing comes from the base service and the switchboard, not the AMD overlay")
for error in errors:
    print(error)
sys.exit(1 if errors else 0)
PY

py_check "docker-compose.amd-rocm.yml: ROCm image adds /dev/kfd and a valueless HSA override" "$ROCM_IMAGE" <<'PY'
import sys
import yaml

rocm = sys.argv[1]
doc = yaml.safe_load(open("docker-compose.amd-rocm.yml", encoding="utf-8"))
errors = []
llama = doc["services"]["llama-server"]
if llama.get("image") != rocm:
    errors.append(f"image must be the pinned ROCm image, got {llama.get('image')!r}")
if llama.get("devices") != ["/dev/kfd:/dev/kfd"]:
    errors.append(f"ROCm adds /dev/kfd to the Vulkan overlay's /dev/dri, got {llama.get('devices')!r}")
env = llama.get("environment") or []
if "HSA_OVERRIDE_GFX_VERSION" not in env:
    errors.append("HSA_OVERRIDE_GFX_VERSION must be a valueless passthrough (ROCm rejects an empty value)")
if any(item.startswith("HSA_OVERRIDE_GFX_VERSION=") for item in env):
    errors.append("HSA_OVERRIDE_GFX_VERSION must not get a default value")
# Parity with the ROCm setup ODS ran on Strix Halo before Lemonade.
if "ROCBLAS_USE_HIPBLASLT=${ROCBLAS_USE_HIPBLASLT:-0}" not in env:
    errors.append("ROCBLAS_USE_HIPBLASLT must default to 0, as the pre-Lemonade ROCm setup ran")
for key in ("command", "entrypoint", "build"):
    if key in llama:
        errors.append(f"ROCm must use the base {key!r}")
for error in errors:
    print(error)
sys.exit(1 if errors else 0)
PY

py_check "docker-compose.multigpu-amd.yml: layer split and per-backend device scoping" <<'PY'
import sys
import yaml

doc = yaml.safe_load(open("docker-compose.multigpu-amd.yml", encoding="utf-8"))
errors = []
llama = doc["services"]["llama-server"]
env = llama.get("environment") or {}
if "command" in llama:
    errors.append("multi-GPU AMD must keep the base command; split settings travel as LLAMA_ARG_*")
if env.get("LLAMA_ARG_SPLIT_MODE") != "${LLAMA_ARG_SPLIT_MODE:-layer}":
    errors.append("LLAMA_ARG_SPLIT_MODE must default to layer (Vulkan has no row split)")
if env.get("LLAMA_ARG_TENSOR_SPLIT") != "${LLAMA_ARG_TENSOR_SPLIT:-}":
    errors.append("LLAMA_ARG_TENSOR_SPLIT must pass through")
for name in ("GGML_VK_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"):
    value = env.get(name, "")
    if not value.startswith("${LLAMA_SERVER_GPU_INDICES:?"):
        errors.append(f"{name} must require LLAMA_SERVER_GPU_INDICES (an empty value hides every GPU)")
if any("LEMONADE" in str(key) for key in env):
    errors.append("Lemonade variables must not be passed")
devices = doc["services"]["dashboard-api"].get("devices") or []
if "/dev/kfd:/dev/kfd" in devices:
    errors.append("dashboard-api must not require /dev/kfd, which Vulkan-only hosts may not have")
for error in errors:
    print(error)
sys.exit(1 if errors else 0)
PY

py_check "docker-compose.base.yml: every launch serves the GGUF file name as the model id" <<'PY'
import sys
import yaml

errors = []
for name in ("docker-compose.base.yml", "docker-compose.cpu.yml", "docker-compose.intel.yml", "docker-compose.arc.yml"):
    command = yaml.safe_load(open(name, encoding="utf-8"))["services"]["llama-server"]["command"]
    if "--alias" not in command:
        errors.append(f"{name}: llama-server command must pass --alias")
        continue
    alias = command[command.index("--alias") + 1]
    model = command[command.index("--model") + 1]
    if not alias.startswith("${GGUF_FILE") or model != "/models/" + alias:
        errors.append(f"{name}: --alias {alias!r} must be the GGUF file that --model {model!r} loads")
base = yaml.safe_load(open("docker-compose.base.yml", encoding="utf-8"))["services"]
if base["llama-server"]["healthcheck"]["test"][-1] != "http://127.0.0.1:8080/health":
    errors.append("base llama-server healthcheck must use /health")
api_env = "\n".join(base["dashboard-api"]["environment"])
if "LEMONADE" in api_env:
    errors.append("dashboard-api must not receive Lemonade variables")
for name in ("NATIVE_LLM_BASE_URL", "NATIVE_LLM_CONTAINER_BASE_URL"):
    if f"{name}=${{{name}:-}}" not in api_env:
        errors.append(f"dashboard-api must receive {name}")
for error in errors:
    print(error)
sys.exit(1 if errors else 0)
PY

py_check "docker-compose.host-native-llm.yml: in-stack llama-server off, router keyed, /v1" <<'PY'
import sys
import yaml

doc = yaml.safe_load(open("docker-compose.host-native-llm.yml", encoding="utf-8"))["services"]
errors = []
if doc["llama-server"].get("profiles") != ["local-inference"]:
    errors.append("the in-stack llama-server must be profiled out")
for service in ("model-router", "litellm"):
    entry = doc[service]
    if "host.docker.internal:host-gateway" not in (entry.get("extra_hosts") or []):
        errors.append(f"{service} must reach the host through host-gateway")
    if "LLAMA_SERVER_API_KEY=${LLAMA_SERVER_API_KEY:-}" not in (entry.get("environment") or []):
        errors.append(f"{service} must receive LLAMA_SERVER_API_KEY to authenticate to the native server")
api_env = doc["dashboard-api"]["environment"]
for required in ("LLM_BACKEND=llama-server", "LLM_API_BASE_PATH=/v1"):
    if required not in api_env:
        errors.append(f"dashboard-api must receive {required}")
if any(item.startswith("LLAMA_SERVER_API_KEY") for item in api_env):
    errors.append("dashboard-api must not receive the native server key")
if any("LEMONADE" in item or "/api/v1" in item for item in api_env):
    errors.append("dashboard-api must not receive Lemonade variables or /api/v1 paths")
for error in errors:
    print(error)
sys.exit(1 if errors else 0)
PY

# ---------------------------------------------------------------------------
# 3. Backend contract and image copies agree
# ---------------------------------------------------------------------------
echo "[contract] amd.json describes the llama.cpp runtime"
py_check "config/backends/amd.json: llama-server engine, /health, digest-pinned images, Windows pin" "$VULKAN_IMAGE" "$ROCM_IMAGE" <<'PY'
import json
import sys

vulkan, rocm = sys.argv[1:3]
contract = json.load(open("config/backends/amd.json", encoding="utf-8"))
nvidia = json.load(open("config/backends/nvidia.json", encoding="utf-8"))
errors = []
for key in ("llm_engine", "public_health_url", "provider_name", "provider_url"):
    if contract.get(key) != nvidia.get(key):
        errors.append(f"{key} must match the NVIDIA llama-server contract: {contract.get(key)!r}")
runtime = contract.get("runtime", {})
if "lemonade" in runtime:
    errors.append("runtime.lemonade must be deleted")
pins = runtime.get("llama_server", {})
if pins.get("linux_image") != vulkan or pins.get("linux_rocm_image") != rocm:
    errors.append("runtime.llama_server linux images must be the pinned Vulkan and ROCm images")
windows = pins.get("windows", {})
if set(windows) != {"release_tag", "asset", "sha256", "size"}:
    errors.append(f"runtime.llama_server.windows keys must be release_tag/asset/sha256/size: {sorted(windows)}")
for error in errors:
    print(error)
sys.exit(1 if errors else 0)
PY

if grep -q 'BACKEND_LEMONADE' scripts/load-backend-contract.sh; then
    fail "load-backend-contract.sh: must not emit BACKEND_LEMONADE_* fields"
else
    pass "load-backend-contract.sh: no Lemonade fields"
fi

echo "[contract] Installer pulls the pinned AMD images and builds none"
if grep -qF "$VULKAN_IMAGE|LLAMA-SERVER" installers/phases/08-images.sh \
    && grep -qF "$ROCM_IMAGE|LLAMA-SERVER" installers/phases/08-images.sh \
    && grep -qF 'AMD_INFERENCE_BACKEND:-vulkan}" == "rocm"' installers/phases/08-images.sh; then
    pass "08-images.sh: pulls the Vulkan image, or the ROCm image when selected"
else
    fail "08-images.sh: must pull the pinned AMD Vulkan image, or the ROCm image when AMD_INFERENCE_BACKEND=rocm"
fi
if grep -q '_candidate_build_services+=(llama-server)' installers/phases/11-services.sh \
    || grep -q 'svcs+=(llama-server)' ods-cli; then
    fail "llama-server must not be built locally on AMD (installer or ods --rebuild-images)"
else
    pass "llama-server is a pulled image on AMD (no local build)"
fi

# ---------------------------------------------------------------------------
# 4. Health and resolver selection
# ---------------------------------------------------------------------------
echo "[contract] AMD health and overlay selection"
if grep -q 'api/v1/health' lib/service-registry.sh; then
    fail "service-registry.sh: AMD must use the manifest /health"
else
    pass "service-registry.sh: no AMD /api/v1/health override"
fi

_resolve() {
    env -u NATIVE_LLM_BASE_URL -u AMD_INFERENCE_BACKEND "$@" \
        bash scripts/resolve-compose-stack.sh --script-dir "$ROOT_DIR" --tier SH_LARGE \
        --gpu-backend amd --gpu-count 1 --ods-mode local
}
_flags="$(_resolve)"
if [[ " $_flags " == *" -f docker-compose.amd.yml "* && "$_flags" != *docker-compose.amd-rocm.yml* ]]; then
    pass "resolver: AMD defaults to the Vulkan overlay"
else
    fail "resolver: AMD must default to docker-compose.amd.yml alone, got: $_flags"
fi
_flags="$(_resolve AMD_INFERENCE_BACKEND=rocm)"
if [[ "$_flags" == *"-f docker-compose.amd.yml -f docker-compose.amd-rocm.yml"* ]]; then
    pass "resolver: AMD_INFERENCE_BACKEND=rocm layers the ROCm overlay after the AMD overlay"
else
    fail "resolver: AMD_INFERENCE_BACKEND=rocm must add docker-compose.amd-rocm.yml, got: $_flags"
fi
_flags="$(_resolve ODS_MODE=lemonade)"
if [[ " $_flags " == *" -f docker-compose.amd.yml "* ]]; then
    pass "resolver: a not-yet-migrated ODS_MODE=lemonade resolves like local"
else
    fail "resolver: ODS_MODE=lemonade must resolve the managed AMD stack for one release, got: $_flags"
fi
_flags="$(env -u AMD_INFERENCE_BACKEND NATIVE_LLM_BASE_URL=http://localhost:8080 \
    bash scripts/resolve-compose-stack.sh --script-dir "$ROOT_DIR" --tier 1 \
    --gpu-backend cpu --gpu-count 1 --ods-mode local)"
if [[ "$_flags" == "-f docker-compose.base.yml -f docker-compose.host-native-llm.yml"* \
    && "$_flags" != *compose.local.yaml* && "$_flags" != *docker-compose.cpu.yml* ]]; then
    pass "resolver: a host-native llama-server uses the host-native overlay and no local-inference overlays"
else
    fail "resolver: NATIVE_LLM_BASE_URL must select docker-compose.host-native-llm.yml, got: $_flags"
fi
if _err="$(_resolve AMD_INFERENCE_BACKEND=auto 2>&1 >/dev/null)"; then
    fail "resolver: an invalid AMD_INFERENCE_BACKEND must be refused"
elif [[ "$_err" == *"AMD_INFERENCE_BACKEND must be vulkan or rocm"* ]]; then
    pass "resolver: an invalid AMD_INFERENCE_BACKEND is refused with a reason"
else
    fail "resolver: unexpected error for an invalid AMD_INFERENCE_BACKEND: $_err"
fi
unset _flags _err

echo "[contract] AMD device nodes: Vulkan needs a render node; ROCm adds /dev/kfd"
_dev_tmp="$(mktemp -d)"
mkdir -p "$_dev_tmp/dev/dri"
touch "$_dev_tmp/dev/dri/renderD128"
_missing_for() (
    log() { :; }; warn() { :; }; ai() { :; }; ai_ok() { :; }; ai_warn() { :; }; ai_bad() { :; }
    SCRIPT_DIR="$ROOT_DIR"
    . "$ROOT_DIR/installers/lib/detection.sh"
    export ODS_AMD_DEVICE_ROOT="$_dev_tmp/dev"
    if [[ -n "$1" ]]; then export AMD_INFERENCE_BACKEND="$1"; else unset AMD_INFERENCE_BACKEND; fi
    amd_gpu_missing_devices_csv
)
_vulkan_missing="$(_missing_for vulkan)"
_default_missing="$(_missing_for '')"
_rocm_missing="$(_missing_for rocm)"
if [[ -z "$_vulkan_missing" && -z "$_default_missing" && "$_rocm_missing" == "$_dev_tmp/dev/kfd" ]]; then
    pass "detection.sh: the Vulkan image needs only /dev/dri; the ROCm image also needs /dev/kfd"
else
    fail "detection.sh: /dev/kfd must be required for ROCm only (vulkan='$_vulkan_missing' default='$_default_missing' rocm='$_rocm_missing')"
fi
rm -rf "$_dev_tmp"
unset _dev_tmp _vulkan_missing _default_missing _rocm_missing
unset -f _missing_for
if awk '/if \[\[ ! -e \/dev\/kfd \]\]; then/,/^    fi$/' installers/phases/10-amd-tuning.sh \
        | grep -q 'AMD_INFERENCE_BACKEND:-vulkan}" == "rocm"' \
    && ! grep -q 'GPU containers (llama-server, comfyui) will fail without it' installers/phases/10-amd-tuning.sh; then
    pass "10-amd-tuning.sh: a missing /dev/kfd fails only the ROCm image (and ComfyUI), not Vulkan"
else
    fail "10-amd-tuning.sh: the /dev/kfd warning must not claim the Vulkan llama-server needs it"
fi

# ---------------------------------------------------------------------------
# 5. Generic AMD contracts carried over from the Lemonade-era suite
# ---------------------------------------------------------------------------
echo "[contract] LiteLLM auth enforced on AMD"
if grep -qE '^[[:space:]]*unset[[:space:]]+LITELLM_MASTER_KEY' \
        extensions/services/litellm/compose.amd.yaml; then
    fail "litellm compose.amd.yaml: 'unset LITELLM_MASTER_KEY' is an auth bypass — must be removed"
else
    pass "litellm compose.amd.yaml: no 'unset LITELLM_MASTER_KEY' (auth enforced)"
fi

echo "[contract] ODS Talk keeps the long-model timeout on AMD"
if grep -q 'ODS_TALK_HERMES_TIMEOUT=${ODS_TALK_HERMES_TIMEOUT:-900}' docker-compose.base.yml \
   && grep -q 'ODS_TALK_HERMES_TIMEOUT=${ODS_TALK_HERMES_TIMEOUT:-900}' docker-compose.host-native-llm.yml; then
    pass "ODS Talk Hermes timeout is 900s for container and host-native AMD"
else
    fail "ODS Talk must keep ODS_TALK_HERMES_TIMEOUT=900 in the base and host-native overlays"
fi

echo "[contract] APE healthcheck uses python (not curl)"
if grep -q 'urllib.request' extensions/services/ape/compose.yaml; then
    pass "ape compose.yaml: python urllib healthcheck"
elif grep -q 'curl' extensions/services/ape/compose.yaml; then
    fail "ape compose.yaml: must not use curl (not in slim image)"
else
    fail "ape compose.yaml: no healthcheck found"
fi

echo "[contract] AMD Docker downgrade handles Debian and inactive docker group"
docker_phase="installers/phases/05-docker.sh"
if grep -q 'apt-cache madison docker-ce' "$docker_phase"; then
    pass "05-docker.sh: apt downgrade resolves the installable Docker CE version"
else
    fail "05-docker.sh: apt downgrade must resolve Docker CE 29.2.1 via apt-cache madison"
fi
if grep -q 'docker-ce=5:29\.2\.1-1~ubuntu' "$docker_phase" \
    || grep -q 'docker-ce-cli=5:29\.2\.1-1~ubuntu' "$docker_phase"; then
    fail "05-docker.sh: apt downgrade must not hardcode Ubuntu package versions"
else
    pass "05-docker.sh: apt downgrade no longer hardcodes Ubuntu package versions"
fi
if awk '/_docker_read_server_version\(\)/,/^}/' "$docker_phase" \
    | grep -q 'ods_sudo docker version'; then
    pass "05-docker.sh: Docker server version probe falls back through ods_sudo"
else
    fail "05-docker.sh: AMD downgrade must detect Docker 29.3 when docker group membership is not active"
fi
if awk '/_docker_server_version_for_amd_downgrade\(\)/,/^}/' "$docker_phase" \
    | grep -q 'systemctl start docker'; then
    pass "05-docker.sh: AMD downgrade starts docker before giving up on version detection"
else
    fail "05-docker.sh: AMD downgrade must retry after starting docker on systemd hosts"
fi
if awk '/Docker 29\.3\.x has a bug/,/fi$/' "$docker_phase" \
    | grep -q '_docker_server_version_for_amd_downgrade'; then
    pass "05-docker.sh: AMD downgrade uses the sudo-aware version probe"
else
    fail "05-docker.sh: AMD downgrade still uses a bare docker version probe"
fi
_amd_docker_probe_tmp="$(mktemp -d)"
mkdir -p "$_amd_docker_probe_tmp/bin"
cat > "$_amd_docker_probe_tmp/bin/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "version" && "${2:-}" == "--format" ]]; then
    if [[ "${ODS_FAKE_DOCKER_SUDO:-}" == "1" ]]; then
        echo "29.3.0"
        exit 0
    fi
    exit 1
fi
exit 99
EOF
cat > "$_amd_docker_probe_tmp/bin/sudo" <<'EOF'
#!/usr/bin/env bash
ODS_FAKE_DOCKER_SUDO=1 "$@"
EOF
chmod +x "$_amd_docker_probe_tmp/bin/docker" "$_amd_docker_probe_tmp/bin/sudo"
_docker_probe_func="$(awk '
    /_docker_read_server_version\(\)/,/^}/ {print}
    /_docker_server_version_for_amd_downgrade\(\)/,/^}/ {print}
' "$docker_phase")"
if _probe_output="$(
    PATH="$_amd_docker_probe_tmp/bin:$PATH" bash -c '
        set -euo pipefail
        ods_sudo() { ODS_FAKE_DOCKER_SUDO=1 "$@"; }
        eval "$1"
        _docker_server_version_for_amd_downgrade
    ' bash "$_docker_probe_func"
)"; then
    if [[ "$_probe_output" == "29.3.0" ]]; then
        pass "05-docker.sh: sudo fallback detects Docker 29.3 when bare docker is denied"
    else
        fail "05-docker.sh: sudo fallback returned unexpected Docker version: $_probe_output"
    fi
else
    fail "05-docker.sh: sudo fallback did not recover Docker server version"
fi
rm -rf "$_amd_docker_probe_tmp"
unset _amd_docker_probe_tmp _docker_probe_func _probe_output
_sample_debian_madison=$'   docker-ce | 5:29.3.0-1~debian.13~trixie | https://download.docker.com/linux/debian trixie/stable amd64 Packages\n   docker-ce | 5:29.2.1-1~debian.13~trixie | https://download.docker.com/linux/debian trixie/stable amd64 Packages'
_resolved_debian_2921="$(awk '$3 ~ /(^|:)29\.2\.1/ {print $3; exit}' <<<"$_sample_debian_madison")"
if [[ "$_resolved_debian_2921" == "5:29.2.1-1~debian.13~trixie" ]]; then
    pass "apt-cache madison parser resolves Debian trixie Docker CE 29.2.1"
else
    fail "apt-cache madison parser did not resolve the Debian trixie Docker CE 29.2.1 version"
fi
unset _sample_debian_madison _resolved_debian_2921 docker_phase

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "AMD llama.cpp contracts: $PASS passed, $FAIL failed"
if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
