#!/bin/bash
# =============================================================================
# Test: compute capability 12.1 runs the CUDA 13 llama.cpp image
# =============================================================================
# llama.cpp's CUDA 12 images (the NVIDIA Compose default among them) have no
# kernels for compute capability 12.1, the DGX Spark GB10 and the RTX Spark
# N1X, and answer "????" there. Detection records the compute capability,
# phase 06 swaps a CUDA 12 image (or the empty default) for the CUDA 13 image
# config/backends/nvidia.json pins right before it writes .env, and phase 08
# pulls that same image. Every other GPU keeps its image.
#
# Runs the shipped functions and the shipped phase 06 block with a stubbed
# nvidia-smi; no Docker, no GPU.
#
# Run: bash tests/test-llama-image-compute-cap.sh
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTRACT="$ROOT_DIR/config/backends/nvidia.json"
PHASE06="$ROOT_DIR/installers/phases/06-directories.sh"
TMP_DIR="$(mktemp -d -t ods-compute-image-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

command -v jq >/dev/null 2>&1 || { echo "[FAIL] jq is required"; exit 1; }

PASS=0
FAIL=0
assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        echo "  PASS: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label"
        echo "        expected: $expected"
        echo "        actual:   $actual"
        FAIL=$((FAIL + 1))
    fi
}

# assert_true <label> <command...>: the command must succeed.
assert_true() {
    local label="$1"
    shift
    if "$@"; then
        echo "  PASS: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label"
        FAIL=$((FAIL + 1))
    fi
}

log() { :; }
warn() { :; }
ai() { :; }
ai_ok() { :; }
ai_warn() { :; }
ai_bad() { :; }
# shellcheck source=../installers/lib/tier-map.sh
source "$ROOT_DIR/installers/lib/tier-map.sh"

REPO="ghcr.io/ggml-org/llama.cpp"
PIN="$(jq -r '.runtime.llama_server.container_images_by_compute["12.1"]' "$CONTRACT")"
DEFAULT="$(sed -n 's/^    image: \${LLAMA_SERVER_IMAGE:-\(.*\)}$/\1/p' "$ROOT_DIR/docker-compose.nvidia.yml")"
CATALOG_B9014="$REPO:server-cuda-b9014@sha256:fcf285820892e7ce3218379634e3590826fc697e8b6745b9392072462e355c4f"
CUDA12_TAG="$REPO:server-cuda12-b11429@sha256:$(printf 'a%.0s' {1..64})"
OTHER_CUDA13="$REPO:server-cuda13-b9014@sha256:$(printf 'b%.0s' {1..64})"
OWN_IMAGE="ods-llama-cpp-gb10:sm121-2496f9c"

echo "=== The pin ==="
[[ "$DEFAULT" =~ ^ghcr\.io/ggml-org/llama\.cpp:server-cuda-(b[0-9]+)@sha256:[0-9a-f]{64}$ ]] \
    || { echo "[FAIL] cannot read the NVIDIA Compose default"; exit 1; }
DEFAULT_BUILD="${BASH_REMATCH[1]}"
assert_true "12.1 maps to a digest-pinned server-cuda13 image" \
    grep -Eq '^ghcr\.io/ggml-org/llama\.cpp:server-cuda13-b[0-9]+@sha256:[0-9a-f]{64}$' <<< "$PIN"
assert_eq "it is the NVIDIA default's release ($DEFAULT_BUILD)" "$DEFAULT_BUILD" \
    "$(sed -n 's/.*:server-cuda13-\(b[0-9]*\)@.*/\1/p' <<< "$PIN")"
assert_eq "only compute capability 12.1 has an image" '["12.1"]' \
    "$(jq -c '.runtime.llama_server.container_images_by_compute | keys' "$CONTRACT")"

echo "=== ods_nvidia_llama_image_for_compute ==="
assert_eq "12.1 -> the CUDA 13 pin" "$PIN" "$(ods_nvidia_llama_image_for_compute 12.1 "$CONTRACT")"
for cc in 12.0 10.0 9.0 8.9 8.6 7.5 12; do
    assert_eq "$cc -> no image (keeps the default)" "" "$(ods_nvidia_llama_image_for_compute "$cc" "$CONTRACT")"
done
assert_eq "no compute capability never reads the contract" "" \
    "$(ods_nvidia_llama_image_for_compute "" "$TMP_DIR/missing.json")"
printf '{ "runtime": ' > "$TMP_DIR/broken.json"
contract_fails() { ! ods_nvidia_llama_image_for_compute 12.1 "$TMP_DIR/broken.json" >/dev/null 2>&1; }
assert_true "an unreadable contract fails" contract_fails

echo "=== ods_llama_image_for_compute_cap ==="
assert_eq "empty (Compose default) -> pin" "$PIN" "$(ods_llama_image_for_compute_cap "" "$PIN")"
assert_eq "CUDA 12 default -> pin" "$PIN" "$(ods_llama_image_for_compute_cap "$DEFAULT" "$PIN")"
assert_eq "CUDA 12 catalog image (gemma4 b9014) -> pin" "$PIN" "$(ods_llama_image_for_compute_cap "$CATALOG_B9014" "$PIN")"
assert_eq "server-cuda12 tag -> pin" "$PIN" "$(ods_llama_image_for_compute_cap "$CUDA12_TAG" "$PIN")"
assert_eq "another CUDA 13 build is kept" "$OTHER_CUDA13" "$(ods_llama_image_for_compute_cap "$OTHER_CUDA13" "$PIN")"
assert_eq "an owner's own image is kept" "$OWN_IMAGE" "$(ods_llama_image_for_compute_cap "$OWN_IMAGE" "$PIN")"
assert_eq "no compute image keeps the CUDA 12 default" "$DEFAULT" "$(ods_llama_image_for_compute_cap "$DEFAULT" "")"
assert_eq "no compute image keeps the empty default" "" "$(ods_llama_image_for_compute_cap "" "")"

echo "=== detect_gpu records the compute capability ==="
SCRIPT_DIR="$ROOT_DIR"
# shellcheck source=../installers/lib/detection.sh
source "$ROOT_DIR/installers/lib/detection.sh"
mkdir -p "$TMP_DIR/drm/card0/device"
echo "0x10de" > "$TMP_DIR/drm/card0/device/vendor"
SPARK_ROW="$(sed -n '2p' "$ROOT_DIR/tests/fixtures/rtx-spark/nvidia-smi-wsl.txt")"
SPARK_NAME="$(cut -d',' -f1 <<< "$SPARK_ROW")"
SPARK_PCI="$(cut -d',' -f2 <<< "$SPARK_ROW" | xargs)"
SPARK_CC="$(cut -d',' -f3 <<< "$SPARK_ROW" | xargs)"
SPARK_MEM="$(cut -d',' -f4 <<< "$SPARK_ROW" | xargs)"

# Rows are "name, memory|pci id|compute cap"; COMPUTE_FAILS=1 models a driver
# whose nvidia-smi has no compute_cap field.
detect_with_rows() (
    printf '%s\n' "$@" > "$TMP_DIR/rows"
    nvidia-smi() {
        case "$*" in
            *"--query-gpu=name,memory.total"*) cut -d'|' -f1 "$TMP_DIR/rows" ;;
            *"--query-gpu=pci.device_id"*) cut -d'|' -f2 "$TMP_DIR/rows" ;;
            *"--query-gpu=compute_cap"*)
                if [[ "${COMPUTE_FAILS:-0}" == 1 ]]; then
                    echo 'Field "compute_cap" is not a valid field to query.'
                    return 2
                fi
                cut -d'|' -f3 "$TMP_DIR/rows"
                ;;
            *) return 0 ;;
        esac
    }
    export ODS_DRM_SYS="$TMP_DIR/drm"
    set -euo pipefail
    detect_gpu || true
    printf '%s|%s|%s\n' "$GPU_NAME" "$GPU_COUNT" "${GPU_COMPUTE_CAP-unset}"
)

assert_eq "RTX Spark N1X (WSL probe row)" "$SPARK_NAME|1|12.1" \
    "$(detect_with_rows "$SPARK_NAME, $SPARK_MEM|$SPARK_PCI|$SPARK_CC")"
assert_eq "an NVIDIA NPU row listed first does not take the GPU's place" "$SPARK_NAME|1|12.1" \
    "$(detect_with_rows "NVIDIA NPU, [N/A]|[N/A]|[N/A]" "$SPARK_NAME, $SPARK_MEM|$SPARK_PCI|$SPARK_CC")"
assert_eq "DGX Spark GB10 (unified memory)" "NVIDIA GB10|1|12.1" \
    "$(detect_with_rows "NVIDIA GB10, [N/A]|0x2E1210DE|12.1")"
assert_eq "RTX 5090 (12.0)" "NVIDIA GeForce RTX 5090|1|12.0" \
    "$(detect_with_rows "NVIDIA GeForce RTX 5090, 32607 MiB|0x2B8510DE|12.0")"
assert_eq "RTX 4090 (8.9)" "NVIDIA GeForce RTX 4090|1|8.9" \
    "$(detect_with_rows "NVIDIA GeForce RTX 4090, 24564 MiB|0x268410DE|8.9")"
assert_eq "a driver without the compute_cap field leaves it empty" "NVIDIA GeForce RTX 3090|1|" \
    "$(COMPUTE_FAILS=1 detect_with_rows "NVIDIA GeForce RTX 3090, 24576 MiB|0x220410DE|8.6")"
assert_eq "an unreadable value leaves it empty" "NVIDIA GeForce RTX 3090|1|" \
    "$(detect_with_rows "NVIDIA GeForce RTX 3090, 24576 MiB|0x220410DE|[N/A]")"

echo "=== phase 06 swaps the image right before it writes .env ==="
# first_line_with <text> <file>: the first line number containing <text>, or
# nothing (awk, not grep, so a missing anchor reaches the [FAIL] below).
first_line_with() { awk -v text="$1" 'index($0, text) { print NR; exit }' "$2"; }
block_start="$(first_line_with '# A GPU of compute capability 12.1 (DGX Spark GB10, RTX Spark N1X) gets' "$PHASE06")"
block_end="$(awk -v start="${block_start:-0}" 'NR > start && /^    LLAMA_SERVER_MEMORY_LIMIT_VALUE=""$/ { print NR - 1; exit }' "$PHASE06")"
amd_block="$(first_line_with 'for _amd_image_key in LLAMA_SERVER_IMAGE LLAMA_SERVER_IMAGE_FALLBACK' "$PHASE06")"
# shellcheck disable=SC2016  # literal phase 06 text
env_write="$(first_line_with 'cat > "$INSTALL_DIR/.env" << ENV_EOF' "$PHASE06")"
[[ -n "$block_start" && -n "$block_end" && -n "$amd_block" && -n "$env_write" ]] \
    || { echo "[FAIL] cannot locate the phase 06 compute image block"; exit 1; }
assert_true "the block runs after the AMD image guard and before the .env write" \
    test "$amd_block" -lt "$block_start" -a "$block_end" -lt "$env_write"
assert_eq "nothing between the block and the .env write sets the image" "" \
    "$(sed -n "${block_end},${env_write}p" "$PHASE06" | grep -E '^[[:space:]]*(LLAMA_SERVER_IMAGE|LLAMA_SERVER_IMAGE_FALLBACK)=|unset LLAMA_SERVER_IMAGE' || true)"
env_template="$(sed -n "${env_write},/^ENV_EOF$/p" "$PHASE06")"
# shellcheck disable=SC2016  # the template's own text, not an expansion
assert_true ".env records GPU_COMPUTE_CAP" grep -qF 'echo "GPU_COMPUTE_CAP=${GPU_COMPUTE_CAP}"' <<< "$env_template"
# shellcheck disable=SC2016
assert_true ".env writes the shell's LLAMA_SERVER_IMAGE" grep -qF 'echo "LLAMA_SERVER_IMAGE=${LLAMA_SERVER_IMAGE}"' <<< "$env_template"
schema_knows_compute_cap() { jq -e '.properties.GPU_COMPUTE_CAP.type == "string"' "$ROOT_DIR/.env.schema.json" >/dev/null; }
assert_true ".env schema knows GPU_COMPUTE_CAP" schema_knows_compute_cap

phase06_block="$(sed -n "${block_start},${block_end}p" "$PHASE06")"
eval "_phase06_compute_block() {
$phase06_block
}"

# Usage: run_block <backend> <compute_cap> <native> <image> <fallback> [source root]
# Prints "<image>|<fallback or unset>|<status>|<log>" after the shipped block.
run_block() (
    export GPU_BACKEND="$1" GPU_COMPUTE_CAP="$2" NATIVE_LLM_ACTIVE="$3"
    LLAMA_SERVER_IMAGE="$4"
    if [[ -n "$5" ]]; then LLAMA_SERVER_IMAGE_FALLBACK="$5"; else unset LLAMA_SERVER_IMAGE_FALLBACK; fi
    SCRIPT_DIR="${6:-$ROOT_DIR}"
    LOG_LINES=""
    log() { LOG_LINES+="$1;"; }
    error() { LOG_LINES+="ERROR $1;"; }
    status=0
    _phase06_compute_block || status=$?
    printf '%s|%s|%s|%s\n' "${LLAMA_SERVER_IMAGE:-}" "${LLAMA_SERVER_IMAGE_FALLBACK-unset}" "$status" "$LOG_LINES"
)

result="$(run_block nvidia 12.1 false "" "")"
assert_eq "12.1, Compose default -> pin" "$PIN|unset|0" "$(cut -d'|' -f1-3 <<< "$result")"
assert_true "the swap is logged" grep -qF "llama-server runs $PIN instead of the Compose default" <<< "$result"
assert_eq "12.1, tier gemma4 default (CUDA 12) -> pin" "$PIN|unset|0" \
    "$(run_block nvidia 12.1 false "$DEFAULT" "" | cut -d'|' -f1-3)"
assert_eq "12.1, catalog b9014 image and fallback -> pin, CUDA 12 fallback dropped" "$PIN|unset|0" \
    "$(run_block nvidia 12.1 false "$CATALOG_B9014" "$CATALOG_B9014" | cut -d'|' -f1-3)"
assert_eq "12.1, owner image and CUDA 13 fallback kept" "$OWN_IMAGE|$OTHER_CUDA13|0" \
    "$(run_block nvidia 12.1 false "$OWN_IMAGE" "$OTHER_CUDA13" | cut -d'|' -f1-3)"
for cc in 12.0 8.9 ""; do
    assert_eq "compute '${cc}': Compose default unchanged" "|unset|0|" "$(run_block nvidia "$cc" false "" "")"
    assert_eq "compute '${cc}': CUDA 12 image and fallback unchanged" "$CATALOG_B9014|$CATALOG_B9014|0|" \
        "$(run_block nvidia "$cc" false "$CATALOG_B9014" "$CATALOG_B9014")"
done
assert_eq "host-native route (no container) unchanged" "|unset|0|" "$(run_block nvidia 12.1 true "" "")"
assert_eq "AMD unchanged" "|unset|0|" "$(run_block amd 12.1 false "" "")"
assert_eq "CPU unchanged" "$CATALOG_B9014|unset|0|" "$(run_block cpu 12.1 false "$CATALOG_B9014" "")"

mkdir -p "$TMP_DIR/broken-root/config/backends"
cp "$TMP_DIR/broken.json" "$TMP_DIR/broken-root/config/backends/nvidia.json"
# jq's own parse error goes to stderr, beside the installer's message.
result="$(run_block nvidia 12.1 false "" "" "$TMP_DIR/broken-root" 2>/dev/null)"
assert_eq "an unreadable contract stops before .env" "|unset|1" "$(cut -d'|' -f1-3 <<< "$result")"
assert_true "the stop gives the cause" grep -qF "ERROR Could not read the llama.cpp image for compute capability 12.1" <<< "$result"
assert_true "the stop gives the recovery" grep -qF "download ODS again and rerun the installer" <<< "$result"
assert_true "the stop links the ODS Discord" grep -qF "Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC" <<< "$result"

echo "=== phase 08 pulls the same image ==="
pull_plan() (
    export SCRIPT_DIR="$ROOT_DIR" LOG_FILE=/dev/null DRY_RUN=true COMPOSE_FLAGS=''
    export GPU_BACKEND="$1" GPU_COMPUTE_CAP="$2" NATIVE_LLM_ACTIVE=false ODS_MODE=local EXTERNAL_LLM_URL=''
    export NATIVE_LLM_BASE_URL='' ENABLE_PERPLEXICA=false ENABLE_COMFYUI=false ENABLE_OPEN_WEBUI=true
    export ENABLE_VOICE=false ENABLE_WORKFLOWS=false ENABLE_RAG=false
    export ENABLE_QDRANT=false ENABLE_EMBEDDINGS=false ENABLE_HERMES=false
    LLAMA_SERVER_IMAGE="$3"
    unset LLAMA_SERVER_IMAGE_FALLBACK
    log() { :; }
    error() { echo "ERROR $1" >&2; return 1; }
    ods_progress() { :; }; show_phase() { :; }; bootline() { :; }; signal() { :; }
    _phase06_compute_block
    # shellcheck disable=SC1091
    source "$ROOT_DIR/installers/phases/08-images.sh"
    for entry in "${PULL_LIST[@]}"; do
        if [[ "${entry##*|}" == LLAMA-SERVER* ]]; then
            printf '%s\n' "${entry%%|*}"
        fi
    done
)
assert_eq "12.1 pulls the CUDA 13 pin" "$PIN" "$(pull_plan nvidia 12.1 "")"
assert_eq "12.1 with a catalog CUDA 12 image pulls the pin" "$PIN" "$(pull_plan nvidia 12.1 "$CATALOG_B9014")"
assert_eq "12.0 pulls the CUDA 12 default" "$DEFAULT" "$(pull_plan nvidia 12.0 "")"
assert_eq "8.9 keeps a catalog image" "$CATALOG_B9014" "$(pull_plan nvidia 8.9 "$CATALOG_B9014")"

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
