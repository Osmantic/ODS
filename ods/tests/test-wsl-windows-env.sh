#!/usr/bin/env bash
# Exercise the actual phase-06 endpoint and dotenv writers without provisioning.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
phase="$root/installers/phases/06-directories.sh"
prepare="$(sed -n '/^    LITELLM_KEY=/,/^    LIVEKIT_SECRET=/p' "$phase" | sed '$d')"
fields="$(sed -n '/^AMD_INFERENCE_RUNTIME=/,/^LEMONADE_MODEL=/p' "$phase")"
[[ -n "$prepare" && -n "$fields" ]]
SCRIPT_DIR="$root"
_phase06_env_hex_secret() { printf 'fixture-key'; }
_env_get_explicit_first() { printf '%s' "${!1-$2}"; }
dotenv_value() { printf '%q' "$1"; }
error() { echo "$*" >&2; return 1; }
ai_ok() { :; }
warn() { echo "$*" >&2; }
# Simulate a qualified WSL host independently of the test runner's kernel.
uname() { if [[ "$*" == '-s' ]]; then echo Linux; else command uname "$@"; fi; }
grep() { if [[ "$*" == '-qi microsoft /proc/version' ]]; then return 0; else command grep "$@"; fi; }

GPU_BACKEND=amd
ODS_MODE=local
EXTERNAL_LLM_ACTIVE=false
LEMONADE_EXTERNAL=false
AMD_INFERENCE_RUNTIME_MODE=wsl-windows-lemonade
AMD_INFERENCE_RUNTIME=lemonade
AMD_INFERENCE_LOCATION=host
AMD_INFERENCE_MANAGED=true
AMD_INFERENCE_PORT=18080
LEMONADE_BASE_URL=http://127.0.0.1:18080
LEMONADE_CONTAINER_BASE_URL=http://host.docker.internal:18080
LEMONADE_MODEL=extra.example.gguf
LEMONADE_API_KEY=fixture-admin-key
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT
INSTALL_DIR="$fixture/install"
mkdir -p "$INSTALL_DIR"
LOG_FILE=/dev/null
# The fixture exercises the real registry writer; hardware is mocked above.
ODS_WINDOWS_MODELS_PATH="$fixture/models"
mkdir -p "$ODS_WINDOWS_MODELS_PATH"
source /dev/stdin <<< "$prepare"
[[ "$LITELLM_LEMONADE_API_KEY" == fixture-admin-key ]]
[[ "$LEMONADE_CONTAINER_API_BASE_VALUE" == http://host.docker.internal:18080/api/v1 ]]
[[ "$LEMONADE_MODEL_VALUE" == extra.example.gguf ]]
[[ "$ODS_ACTIVE_MODEL_STORE" == windows-inference && -f "$INSTALL_DIR/data/model-stores.json" ]]
generated="$(source /dev/stdin <<< "cat <<ENV
$fields
ENV")"
source /dev/stdin <<< "$generated"
[[ "$AMD_INFERENCE_RUNTIME_MODE" == wsl-windows-lemonade ]]
[[ "$AMD_INFERENCE_MANAGED" == true && "$LEMONADE_EXTERNAL" == false ]]
[[ "$AMD_INFERENCE_LOCATION" == host && "$AMD_INFERENCE_PORT" == 18080 ]]
[[ "$AMD_INFERENCE_BACKEND" == vulkan && "$AMD_INFERENCE_SUPPORTED_BACKENDS" == vulkan ]]
echo 'PASS: Windows runtime placement, ownership, endpoint and credential survive dotenv generation'

for invalid in external wrong-port remote-host no-model; do
    if (
        case "$invalid" in
            external) EXTERNAL_LLM_URL=http://example.test ;;
            wrong-port) AMD_INFERENCE_PORT=99999 ;;
            remote-host) LEMONADE_CONTAINER_BASE_URL=http://example.test:18080 ;;
            no-model) LEMONADE_MODEL='' ;;
        esac
        ods_windows_lemonade_validate
    ); then echo "FAIL: accepted $invalid" >&2; exit 1; fi
done
echo 'PASS: inconsistent Windows runtime handoffs are rejected'

# Linux must retain the hardware and model actually verified by Windows,
# rather than treating the WSL VM as a new CPU-only machine.
ODS_WINDOWS_GPU_NAME='AMD Radeon RX 9070 XT'
ODS_WINDOWS_GPU_VRAM_MB=16384
ODS_WINDOWS_GPU_COUNT=1
ODS_WINDOWS_GPU_MEMORY_TYPE=discrete
ODS_WINDOWS_TIER=2
ODS_WINDOWS_MODEL_FILE=example.gguf
ODS_WINDOWS_MODEL_ID=example
ODS_WINDOWS_MODEL_CONTEXT=65536
truncate -s 2097153 "$ODS_WINDOWS_MODELS_PATH/example.gguf"
ods_windows_lemonade_apply_hardware
[[ "$GPU_NAME" == 'AMD Radeon RX 9070 XT' && "$GPU_VRAM" == 16384 ]]
[[ "$TIER" == 2 && "$GGUF_FILE" == example.gguf && "$LLM_MODEL" == example ]]
[[ "$CTX_SIZE" == 65536 && "$NO_BOOTSTRAP" == true && "$ODS_MODE" == lemonade ]]
[[ "$LLM_MODEL_SIZE_MB" == 3 && "$GPU_TOTAL_VRAM" == 16384 && "$GPU_TOPOLOGY_JSON" == '{}' ]]
[[ "$OPENCLAW_PROVIDER_URL_DEFAULT" == http://litellm:4000/v1 ]]
for invalid in context tier filename memory; do
    if (
        case "$invalid" in
            context) ODS_WINDOWS_MODEL_CONTEXT=0 ;;
            tier) TIER=3 ;;
            filename) ODS_WINDOWS_MODEL_FILE=../example.gguf ;;
            memory) ODS_WINDOWS_GPU_MEMORY_TYPE=unknown ;;
        esac
        ods_windows_lemonade_apply_hardware
    ); then echo "FAIL: accepted invalid hardware handoff $invalid" >&2; exit 1; fi
done
echo 'PASS: verified Windows hardware and model survive Linux detection; inconsistent inputs are rejected'

# Run the complete detection phase, so its early Windows return must still
# initialize the variables consumed by later feature/configuration phases.
ods_progress() { :; }
chapter() { :; }
ai() { :; }
log() { :; }
load_capability_profile() { CAP_PROFILE_LOADED=true; CAP_LLM_BACKEND=cpu; }
detect_host_arch() { echo amd64; }
resolve_compose_config() { COMPOSE_RESOLVED=true; }
source "$root/installers/phases/02-detection.sh"
[[ "$COMPOSE_RESOLVED" == true && "$CAP_PROFILE_LOADED" == false ]]
[[ "$GPU_BACKEND" == amd && "$GGUF_FILE" == example.gguf && "$CTX_SIZE" == 65536 ]]
[[ "$GPU_HAS_NVLINK" == false && "$GPU_TOPOLOGY_JSON" == '{}' && "$LLM_MODEL_SIZE_MB" == 3 ]]
echo 'PASS: complete Linux detection retains the prepared Windows model and initializes downstream state'
