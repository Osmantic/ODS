#!/usr/bin/env bash
# AMD CPU smoke. CI only (.github/workflows/amd-cpu-smoke.yml): it pulls and
# runs Docker images, so never run it on a host with a live ODS stack.
#
# 1. Render the AMD stacks with Docker Compose and check the contract on the
#    rendered result: the pinned Vulkan image (ROCm only when selected), the
#    base llama.cpp launch with the GGUF file name as the served model id,
#    /dev/dri without /dev/kfd for Vulkan, and no Lemonade-era settings.
# 2. Run the pinned Vulkan image with the rendered launch but no GPU devices
#    on a tiny SHA-pinned GGUF, and check /health, /v1/models, a chat
#    completion and /metrics.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# llama.cpp's own server tests use this model (tools/server/tests/utils.py,
# preset tinyllama2): 1.2 MB, pinned to a repository commit and a digest.
MODEL_FILE="stories260K-f32.gguf"
MODEL_URL="https://huggingface.co/ggml-org/test-model-stories260K/resolve/479896ec924af6d40fd419ab8f4d1eb2101de00d/$MODEL_FILE"
MODEL_SHA256="270cba1bd5109f42d03350f60406024560464db173c0e387d91f0426d3bd256d"
PORT="${AMD_SMOKE_PORT:-18080}"
CONTAINER="ods-amd-cpu-smoke-$$"
WORK_DIR="$(mktemp -d)"

cleanup() {
    local status=$?
    if [[ "$status" -ne 0 ]] && docker container inspect "$CONTAINER" >/dev/null 2>&1; then
        echo "--- llama-server log" >&2
        docker logs --tail 80 "$CONTAINER" >&2 || echo "(log unavailable)" >&2
    fi
    # A container that never started is not an error here.
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    rm -rf "$WORK_DIR"
    exit "$status"
}
trap cleanup EXIT

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

# Placeholders for the base stack's required secrets; nothing here starts it.
export WEBUI_SECRET=ci-placeholder SEARXNG_SECRET=ci-placeholder \
    N8N_USER=ci@example.com N8N_PASS=ci-placeholder LITELLM_KEY=ci-placeholder
export GGUF_FILE="$MODEL_FILE"
unset AMD_INFERENCE_BACKEND NATIVE_LLM_BASE_URL LLAMA_SERVER_IMAGE

echo "== Render the AMD stacks"
flags="$(bash scripts/resolve-compose-stack.sh --script-dir "$ROOT_DIR" --tier 3 \
    --gpu-backend amd --gpu-count 1 --ods-mode local)"
[[ " $flags " == *" -f docker-compose.amd.yml "* && "$flags" != *docker-compose.amd-rocm.yml* ]] \
    || fail "resolver: AMD must default to the Vulkan overlay, got: $flags"
rocm_flags="$(AMD_INFERENCE_BACKEND=rocm bash scripts/resolve-compose-stack.sh --script-dir "$ROOT_DIR" \
    --tier 3 --gpu-backend amd --gpu-count 1 --ods-mode local)"
[[ "$rocm_flags" == *"-f docker-compose.amd.yml -f docker-compose.amd-rocm.yml"* ]] \
    || fail "resolver: AMD_INFERENCE_BACKEND=rocm must add the ROCm overlay, got: $rocm_flags"
pass "resolver selects Vulkan by default and ROCm on request"

docker compose -f docker-compose.base.yml -f docker-compose.amd.yml config --format json \
    > "$WORK_DIR/vulkan.json"
AMD_INFERENCE_BACKEND=rocm docker compose -f docker-compose.base.yml -f docker-compose.amd.yml \
    -f docker-compose.amd-rocm.yml config --format json > "$WORK_DIR/rocm.json"
GPU_COUNT=2 LLAMA_SERVER_GPU_INDICES=0,1 docker compose -f docker-compose.base.yml \
    -f docker-compose.amd.yml -f docker-compose.multigpu-amd.yml config --format json \
    > "$WORK_DIR/multigpu.json"

python3 - "$WORK_DIR" "$MODEL_FILE" <<'PY'
import json
import sys
from pathlib import Path

work, model = Path(sys.argv[1]), sys.argv[2]
backend = json.loads(Path("config/backends/amd.json").read_text())["runtime"]["llama_server"]
lock = {entry.get("value") for entry in json.loads(Path("config/dependency-lock.json").read_text())["entries"]
        if isinstance(entry, dict) and entry.get("type") == "image"}
errors = []


def service(name):
    config = json.loads((work / f"{name}.json").read_text())
    for other, spec in config["services"].items():
        if "lemonade" in str(spec.get("image", "")).lower():
            errors.append(f"{name}: {other} still uses a Lemonade image")
    return config["services"]["llama-server"]


def devices(spec):
    return {str(item.get("source") if isinstance(item, dict) else item).split(":")[0]
            for item in spec.get("devices") or []}


def env(spec):
    return spec.get("environment") or {}


vulkan = service("vulkan")
if vulkan["image"] != backend["linux_image"]:
    errors.append(f"Vulkan image {vulkan['image']} is not the pinned {backend['linux_image']}")
if backend["linux_image"] not in lock:
    errors.append("the Vulkan image is missing from config/dependency-lock.json")
if "build" in vulkan:
    errors.append("llama-server must be pulled, not built, on AMD")
command = vulkan.get("command") or []
for flag, value in (("--model", f"/models/{model}"), ("--alias", model), ("--port", "8080")):
    if flag not in command or command[command.index(flag) + 1] != value:
        errors.append(f"Vulkan launch must pass {flag} {value}: {command}")
if "--metrics" not in command:
    errors.append("Vulkan launch must pass --metrics")
if devices(vulkan) != {"/dev/dri"}:
    errors.append(f"Vulkan must get /dev/dri only, got {sorted(devices(vulkan))}")
for key in env(vulkan):
    if key.startswith(("HSA_", "ROCBLAS_", "ROCR_")) or "LEMONADE" in key:
        errors.append(f"Vulkan llama-server must not receive {key}")
if env(vulkan).get("LLAMA_ARG_SPLIT_MODE") != "none":
    errors.append("a single AMD GPU must default LLAMA_ARG_SPLIT_MODE to none")
if "http://127.0.0.1:8080/health" not in " ".join(vulkan.get("healthcheck", {}).get("test", [])):
    errors.append("llama-server health must be /health")

rocm = service("rocm")
if rocm["image"] != backend["linux_rocm_image"]:
    errors.append(f"ROCm image {rocm['image']} is not the pinned {backend['linux_rocm_image']}")
if backend["linux_rocm_image"] not in lock:
    errors.append("the ROCm image is missing from config/dependency-lock.json")
if not {"/dev/dri", "/dev/kfd"} <= devices(rocm):
    errors.append(f"ROCm must get /dev/dri and /dev/kfd, got {sorted(devices(rocm))}")
if env(rocm).get("ROCBLAS_USE_HIPBLASLT") != "0":
    errors.append("ROCm must default ROCBLAS_USE_HIPBLASLT to 0")
if env(rocm).get("HSA_OVERRIDE_GFX_VERSION") not in (None, ""):
    errors.append("HSA_OVERRIDE_GFX_VERSION must stay unset unless the installer sets it")

multigpu = service("multigpu")
for key, value in (("GGML_VK_VISIBLE_DEVICES", "0,1"), ("ROCR_VISIBLE_DEVICES", "0,1"),
                   ("NODEVICE_SELECT", "1"), ("LLAMA_ARG_SPLIT_MODE", "layer")):
    if env(multigpu).get(key) != value:
        errors.append(f"multi-GPU AMD must set {key}={value}, got {env(multigpu).get(key)!r}")

# The launch the CPU run below repeats, without devices.
(work / "image").write_text(vulkan["image"] + "\n")
(work / "args").write_text("".join(f"{arg}\n" for arg in command))
# The CPU run has no GPU device. llama.cpp b9014 refuses split mode "none"
# without one ("invalid value for main_gpu: 0 (available devices: 0)"), so the
# run omits GPU placement; installs without devices use the CPU stack instead.
(work / "env").write_text("".join(f"{key}={value}\n" for key, value in env(vulkan).items()
                                  if value is not None
                                  and key not in {"LLAMA_ARG_SPLIT_MODE", "LLAMA_ARG_MAIN_GPU"}))
if errors:
    print("\n".join(f"[FAIL] {error}" for error in errors), file=sys.stderr)
    sys.exit(1)
print("[PASS] rendered AMD stacks keep the llama.cpp contract")
PY

echo "== Check the ROCm pin resolves"
rocm_image="$(python3 -c 'import json; print(json.load(open("config/backends/amd.json"))["runtime"]["llama_server"]["linux_rocm_image"])')"
docker buildx imagetools inspect "$rocm_image" >/dev/null \
    || fail "the pinned ROCm image $rocm_image does not resolve"
pass "the pinned ROCm image resolves"

echo "== Serve a tiny model with the Vulkan image on CPU"
mkdir -p "$WORK_DIR/models"
curl -fsSL --retry 3 -o "$WORK_DIR/models/$MODEL_FILE" "$MODEL_URL"
echo "$MODEL_SHA256  $WORK_DIR/models/$MODEL_FILE" | sha256sum -c - >/dev/null \
    || fail "$MODEL_FILE does not match its pinned SHA-256"
pass "tiny GGUF matches its pinned SHA-256"

image="$(cat "$WORK_DIR/image")"
mapfile -t args < "$WORK_DIR/args"
docker pull --quiet "$image" >/dev/null
docker run -d --name "$CONTAINER" --security-opt no-new-privileges:true \
    -p "127.0.0.1:$PORT:8080" --env-file "$WORK_DIR/env" \
    -v "$WORK_DIR/models:/models:ro" "$image" "${args[@]}" >/dev/null

base="http://127.0.0.1:$PORT"
for _ in $(seq 1 90); do
    # Refused or 503 until the model is loaded; the loop bounds the wait.
    if curl -fsS "$base/health" >"$WORK_DIR/health" 2>/dev/null; then
        break
    fi
    docker container inspect -f '{{.State.Running}}' "$CONTAINER" | grep -qx true \
        || fail "llama-server exited before it became healthy"
    sleep 2
done
grep -q '"ok"' "$WORK_DIR/health" || fail "/health did not report ok"
pass "/health reports ok on CPU, with no GPU device"

curl -fsS "$base/v1/models" > "$WORK_DIR/models.json"
python3 - "$WORK_DIR/models.json" "$MODEL_FILE" <<'PY' || fail "/v1/models must list the GGUF file name"
import json
import sys
data = json.load(open(sys.argv[1]))["data"]
sys.exit(0 if [item["id"] for item in data] == [sys.argv[2]] else 1)
PY
pass "/v1/models serves the GGUF file name as the model id"

curl -fsS "$base/v1/chat/completions" -H 'Content-Type: application/json' \
    -d "{\"model\": \"$MODEL_FILE\", \"messages\": [{\"role\": \"user\", \"content\": \"Once upon a time\"}], \"max_tokens\": 8}" \
    > "$WORK_DIR/chat.json"
python3 - "$WORK_DIR/chat.json" <<'PY' || fail "the chat completion is malformed"
import json
import sys
reply = json.load(open(sys.argv[1]))
ok = (reply.get("object") == "chat.completion"
      and reply["choices"][0]["message"]["role"] == "assistant"
      and reply["usage"]["completion_tokens"] > 0)
sys.exit(0 if ok else 1)
PY
pass "a chat completion returns generated tokens"

curl -fsS "$base/metrics" > "$WORK_DIR/metrics"
grep -Eq '^llamacpp:tokens_predicted_total [1-9]' "$WORK_DIR/metrics" \
    || fail "/metrics must count the predicted tokens"
pass "/metrics counts the generated tokens"

echo "AMD CPU smoke passed"
