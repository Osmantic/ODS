#!/usr/bin/env bash
# External Ollama / LM Studio discovery and installer-selection contracts.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../installers/lib/external-services.sh
source "$ROOT_DIR/installers/lib/external-services.sh"

PASSED=0
FAILED=0

pass() {
    printf '[PASS] %s\n' "$1"
    PASSED=$((PASSED + 1))
}

fail() {
    printf '[FAIL] %s\n' "$1" >&2
    FAILED=$((FAILED + 1))
}

assert_eq() {
    local actual="$1" expected="$2" label="$3"
    if [[ "$actual" == "$expected" ]]; then
        pass "$label"
    else
        fail "$label (expected '$expected', got '$actual')"
    fi
}

assert_true() {
    local label="$1"
    shift
    if "$@"; then
        pass "$label"
    else
        fail "$label"
    fi
}

assert_eq "$(external_llm_normalize_model_name 'qwen3.5:9b')" \
    "qwen3.5-9b" "normalizes Ollama tags"
assert_eq "$(external_llm_normalize_model_name 'Qwen3.5-9B-Q4_K_M.gguf')" \
    "qwen3.5-9b" "removes GGUF quantization suffixes"
assert_eq "$(external_llm_container_url 'http://localhost:11434/v1')" \
    "http://host.docker.internal:11434" "normalizes localhost for containers"
assert_eq "$(external_llm_container_url 'http://[::1]:1234/api/v1')" \
    "http://host.docker.internal:1234" "normalizes IPv6 loopback for containers"
assert_eq "$(external_llm_host_url 'http://host.docker.internal:11434/v1')" \
    "http://127.0.0.1:11434" "normalizes the Docker host alias for host probes"
assert_true "accepts a supported external base URL" \
    external_llm_validate_url "http://127.0.0.1:11434/v1"
if external_llm_validate_url "file:///tmp/models"; then
    fail "rejects non-HTTP external URLs"
else
    pass "rejects non-HTTP external URLs"
fi
if external_llm_validate_url "http://user:secret@127.0.0.1:11434"; then
    fail "rejects credentials embedded in external URLs"
else
    pass "rejects credentials embedded in external URLs"
fi
if external_llm_validate_url "http://127.0.0.1:11434?token=secret"; then
    fail "rejects query data embedded in external URLs"
else
    pass "rejects query data embedded in external URLs"
fi
assert_true "matches equivalent Ollama and GGUF model names" \
    external_llm_model_matches "Qwen3.5-9B-Q4_K_M.gguf" "qwen3.5:9b"
if external_llm_model_matches "qwen3.5:9b" "llama3.2:3b"; then
    fail "rejects unrelated model families"
else
    pass "rejects unrelated model families"
fi

run_phase_case() {
    set -euo pipefail

    local case_name="$1"
    local install_dir="$2"

    ods_progress() { :; }
    log() { :; }
    ai() { :; }
    ai_ok() { :; }
    ai_bad() { :; }
    resolve_compose_config() { :; }

    curl() {
        local url="${*: -1}"
        case "$url" in
            */api/tags)
                [[ "${MOCK_OLLAMA:-down}" == "up" ]] || return 22
                printf '{"models":[{"name":"qwen3.5:9b"},{"name":"llama3.2:3b"}]}'
                ;;
            */v1/models)
                [[ "${MOCK_LMSTUDIO:-down}" == "up" ]] || return 22
                printf '{"data":[{"id":"qwen3.5-9b"},{"id":"local-model"}]}'
                ;;
            */v1/chat/completions)
                printf '{"choices":[{"message":{"content":"OK"}}]}'
                ;;
            *)
                return 22
                ;;
        esac
    }

    INTERACTIVE=false
    DRY_RUN=false
    ODS_MODE=local
    INSTALL_DIR="$install_dir"
    GGUF_FILE="Qwen3.5-9B-Q4_K_M.gguf"
    LLM_MODEL="qwen3.5-9b"
    unset EXTERNAL_LLM_URL EXTERNAL_LLM_CONTAINER_URL EXTERNAL_LLM_PROVIDER
    unset EXTERNAL_LLM_MODEL EXTERNAL_LLM_AUTO_REUSE EXTERNAL_LLM_DISABLE
    unset EXTERNAL_LLM_RESET SKIP_MODEL_DOWNLOAD LEMONADE_EXTERNAL

    case "$case_name" in
        default)
            MOCK_OLLAMA=up
            ;;
        auto)
            MOCK_OLLAMA=up
            EXTERNAL_LLM_AUTO_REUSE=true
            ;;
        persisted)
            MOCK_OLLAMA=up
            ;;
        disabled)
            MOCK_OLLAMA=up
            EXTERNAL_LLM_DISABLE=true
            ;;
        disabled-cloud)
            MOCK_OLLAMA=up
            ODS_MODE=cloud
            EXTERNAL_LLM_DISABLE=true
            ;;
        explicit-offline)
            EXTERNAL_LLM_URL="http://127.0.0.1:11434"
            EXTERNAL_LLM_PROVIDER="ollama"
            EXTERNAL_LLM_MODEL="qwen3.5:9b"
            ;;
        explicit-openai|detect-openai)
            MOCK_LMSTUDIO=up
            MOCK_OLLAMA=down
            EXTERNAL_LLM_URL="http://10.0.2.2:18080"
            EXTERNAL_LLM_PROVIDER="openai-compatible"
            [[ "$case_name" != detect-openai ]] || EXTERNAL_LLM_PROVIDER=auto
            EXTERNAL_LLM_MODEL="local-model"
            ;;
        explicit-cloud)
            MOCK_OLLAMA=up
            ODS_MODE=cloud
            EXTERNAL_LLM_URL="http://127.0.0.1:11434"
            EXTERNAL_LLM_PROVIDER="ollama"
            EXTERNAL_LLM_MODEL="qwen3.5:9b"
            ;;
        explicit-hybrid)
            MOCK_OLLAMA=up
            ODS_MODE=hybrid
            EXTERNAL_LLM_URL="http://127.0.0.1:11434"
            EXTERNAL_LLM_PROVIDER="ollama"
            EXTERNAL_LLM_MODEL="qwen3.5:9b"
            ;;
        explicit-lemonade)
            MOCK_OLLAMA=up
            LEMONADE_EXTERNAL=true
            EXTERNAL_LLM_URL="http://127.0.0.1:11434"
            EXTERNAL_LLM_PROVIDER="ollama"
            EXTERNAL_LLM_MODEL="qwen3.5:9b"
            ;;
        *)
            return 99
            ;;
    esac

    # shellcheck source=../installers/phases/02b-external-services.sh
    source "$ROOT_DIR/installers/phases/02b-external-services.sh"
}

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

for external_case in explicit-openai detect-openai; do
    if output="$(run_phase_case "$external_case" "$TEMP_DIR/$external_case"; printf '%s|%s\n' \
        "${EXTERNAL_LLM_PROVIDER:-}" "${EXTERNAL_LLM_MODEL:-}")"; then
        assert_eq "$output" "openai-compatible|local-model" "$external_case preserves protocol and exact model identity"
    else
        fail "$external_case accepts a generic OpenAI-compatible endpoint"
    fi
done

if output="$(run_phase_case default "$TEMP_DIR/default"; printf '%s|%s\n' "${EXTERNAL_LLM_URL:-}" "${SKIP_MODEL_DOWNLOAD:-}")"; then
    assert_eq "$output" "|false" "non-interactive ambient discovery is inert by default"
else
    fail "default non-interactive phase completes"
fi

if output="$(run_phase_case auto "$TEMP_DIR/auto"; printf '%s|%s|%s|%s\n' \
    "${EXTERNAL_LLM_PROVIDER:-}" "${EXTERNAL_LLM_MODEL:-}" \
    "${EXTERNAL_LLM_CONTAINER_URL:-}" "${SKIP_MODEL_DOWNLOAD:-}")"; then
    assert_eq "$output" \
        "ollama|qwen3.5:9b|http://host.docker.internal:11434|true" \
        "explicit auto-reuse validates and persists an exact external route"
else
    fail "opt-in auto-reuse phase completes"
fi

mkdir -p "$TEMP_DIR/persisted"
cat > "$TEMP_DIR/persisted/.env" <<'EOF'
EXTERNAL_LLM_URL=http://127.0.0.1:11434
EXTERNAL_LLM_CONTAINER_URL=http://host.docker.internal:11434
EXTERNAL_LLM_PROVIDER=ollama
EXTERNAL_LLM_MODEL=qwen3.5:9b
SKIP_MODEL_DOWNLOAD=true
EOF
if output="$(run_phase_case persisted "$TEMP_DIR/persisted"; printf '%s|%s\n' \
    "${EXTERNAL_LLM_MODEL:-}" "${SKIP_MODEL_DOWNLOAD:-}")"; then
    assert_eq "$output" "qwen3.5:9b|true" \
        "rerun revalidates and preserves a reachable external selection"
else
    fail "persisted external selection rerun completes"
fi

mkdir -p "$TEMP_DIR/disabled"
cp "$TEMP_DIR/persisted/.env" "$TEMP_DIR/disabled/.env"
if output="$(run_phase_case disabled "$TEMP_DIR/disabled"; printf '%s|%s|%s\n' \
    "${EXTERNAL_LLM_URL:-}" "${SKIP_MODEL_DOWNLOAD:-}" "${EXTERNAL_LLM_RESET:-}")"; then
    assert_eq "$output" "|false|true" \
        "--no-external-llm clears the persisted topology for managed inference"
else
    fail "external reset phase completes"
fi

if output="$(run_phase_case disabled-cloud "$TEMP_DIR/disabled"; printf '%s|%s|%s\n' \
    "${EXTERNAL_LLM_URL:-}" "${SKIP_MODEL_DOWNLOAD:-}" "${EXTERNAL_LLM_RESET:-}")"; then
    assert_eq "$output" "|false|true" \
        "--no-external-llm permits an intentional transition to cloud mode"
else
    fail "external reset followed by cloud mode completes"
fi

if run_phase_case explicit-offline "$TEMP_DIR/offline"; then
    fail "explicit unavailable provider must fail closed"
else
    pass "explicit unavailable provider fails closed"
fi

if run_phase_case explicit-cloud "$TEMP_DIR/cloud"; then
    fail "external reuse must reject cloud mode"
else
    pass "external reuse rejects cloud mode without mutating the topology"
fi

if run_phase_case explicit-hybrid "$TEMP_DIR/hybrid"; then
    fail "external reuse must reject hybrid mode"
else
    pass "external reuse rejects hybrid mode without bypassing LiteLLM"
fi

if run_phase_case explicit-lemonade "$TEMP_DIR/lemonade"; then
    fail "external reuse must reject a second host-managed backend"
else
    pass "external reuse rejects simultaneous external Lemonade"
fi

run_phase06_env_cycle() (
    set -euo pipefail

    local install_dir="$TEMP_DIR/phase06-install"
    mkdir -p "$install_dir"
    tar -C "$ROOT_DIR" \
        --exclude='./.env' \
        --exclude='./extensions/services/dashboard/node_modules' \
        --exclude='./extensions/services/dashboard/dist' \
        -cf - . | tar -C "$install_dir" -xf -

    export INSTALL_DIR="$install_dir"
    export SCRIPT_DIR="$install_dir"
    export LOG_FILE="$install_dir/phase06.log"
    export DRY_RUN=false
    export INTERACTIVE=false
    export ODS_MODE=local
    export GPU_BACKEND=cpu
    export TIER=T1
    export TIER_NAME=Entry
    export LLM_MODEL=qwen3-1.7b
    export GGUF_FILE=Qwen3-1.7B-Q4_K_M.gguf
    export MAX_CONTEXT=4096
    export ODS_VERSION=2.1.0
    export ENABLE_VOICE=false
    export ENABLE_WORKFLOWS=false
    export ENABLE_RAG=false
    export ENABLE_HERMES=false
    export ENABLE_OPENCLAW=false
    export EXTERNAL_LLM_URL=http://127.0.0.1:11434
    export EXTERNAL_LLM_CONTAINER_URL=http://host.docker.internal:11434
    export EXTERNAL_LLM_PROVIDER=ollama
    export EXTERNAL_LLM_MODEL=qwen3.5:9b
    export LEMONADE_EXTERNAL=false

    # shellcheck source=../installers/lib/constants.sh
    source "$install_dir/installers/lib/constants.sh"
    # shellcheck source=../installers/lib/logging.sh
    source "$install_dir/installers/lib/logging.sh"
    # shellcheck source=../installers/lib/ui.sh
    source "$install_dir/installers/lib/ui.sh"
    # shellcheck source=../installers/lib/detection.sh
    source "$install_dir/installers/lib/detection.sh"
    # shellcheck source=../installers/lib/progress.sh
    source "$install_dir/installers/lib/progress.sh"

    ods_progress() { :; }
    ai() { :; }
    ai_ok() { :; }
    ai_warn() { :; }
    ai_bad() { :; }
    chapter() { :; }
    signal() { :; }
    show_phase() { :; }
    sudo() { return 0; }
    docker() {
        if [[ "${1:-}" == "info" && "${2:-}" == "--format" ]]; then
            printf '4\n'
            return 0
        fi
        command docker "$@"
    }

    # shellcheck source=../installers/phases/06-directories.sh
    source "$install_dir/installers/phases/06-directories.sh"

    grep -qx 'LLM_BACKEND=external' "$install_dir/.env"
    grep -qx 'LLM_MODEL=qwen3.5:9b' "$install_dir/.env"
    grep -qx 'LLM_API_URL=http://litellm:4000' "$install_dir/.env"
    grep -qx 'OPEN_WEBUI_LLM_BASE_URL=http://litellm:4000/v1' "$install_dir/.env"
    grep -qx 'HERMES_LLM_BASE_URL=http://litellm:4000/v1' "$install_dir/.env"
    grep -q 'model_name: "ods/current"' "$install_dir/config/litellm/local.yaml"
    grep -q 'model: "openai/qwen3.5:9b"' "$install_dir/config/litellm/local.yaml"
    grep -q 'api_base: "http://host.docker.internal:11434/v1"' "$install_dir/config/litellm/local.yaml"
    grep -q 'master_key: os.environ/LITELLM_MASTER_KEY' "$install_dir/config/litellm/local.yaml"
    grep -qx 'EXTERNAL_LLM_PROVIDER=ollama' "$install_dir/.env"
    grep -qx 'SKIP_MODEL_DOWNLOAD=true' "$install_dir/.env"
    grep -qx 'MODEL_RECOMMENDED_MODEL=qwen3-1.7b' "$install_dir/.env"

    export LLM_MODEL=qwen3-1.7b
    export GGUF_FILE=Qwen3-1.7B-Q4_K_M.gguf
    export MAX_CONTEXT=4096
    export EXTERNAL_LLM_URL=
    export EXTERNAL_LLM_CONTAINER_URL=
    export EXTERNAL_LLM_PROVIDER=
    export EXTERNAL_LLM_MODEL=
    export EXTERNAL_LLM_RESET=true

    source "$install_dir/installers/phases/06-directories.sh"

    grep -qx 'LLM_BACKEND=llama-server' "$install_dir/.env"
    grep -qx 'LLM_MODEL=qwen3-1.7b' "$install_dir/.env"
    grep -qx 'LLM_API_URL=http://llama-server:8080' "$install_dir/.env"
    grep -qx 'OPEN_WEBUI_LLM_BASE_URL=' "$install_dir/.env"
    grep -qx 'HERMES_LLM_BASE_URL=http://llama-server:8080/v1' "$install_dir/.env"
    grep -qx 'EXTERNAL_LLM_URL=' "$install_dir/.env"
    grep -qx 'EXTERNAL_LLM_PROVIDER=' "$install_dir/.env"
    grep -qx 'SKIP_MODEL_DOWNLOAD=false' "$install_dir/.env"
    grep -q 'api_base: http://llama-server:8080/v1' "$install_dir/config/litellm/local.yaml"
    ! grep -q 'host.docker.internal:11434\|openai/qwen3.5:9b' "$install_dir/config/litellm/local.yaml"
)

if run_phase06_env_cycle; then
    pass "phase 06 persists external routing and restores managed inference on reset"
else
    fail "phase 06 external routing/reset cycle"
fi

run_phase06_amd_external() (
    set -euo pipefail

    local install_dir="$TEMP_DIR/phase06-amd"
    mkdir -p "$install_dir"
    tar -C "$ROOT_DIR" \
        --exclude='./.env' \
        --exclude='./extensions/services/dashboard/node_modules' \
        --exclude='./extensions/services/dashboard/dist' \
        -cf - . | tar -C "$install_dir" -xf -

    export INSTALL_DIR="$install_dir"
    export SCRIPT_DIR="$install_dir"
    export LOG_FILE="$install_dir/phase06.log"
    export DRY_RUN=false
    export INTERACTIVE=false
    export ODS_MODE=local
    export GPU_BACKEND=amd
    export TIER=T1
    export TIER_NAME=Entry
    export LLM_MODEL=qwen3-1.7b
    export GGUF_FILE=Qwen3-1.7B-Q4_K_M.gguf
    export MAX_CONTEXT=4096
    export ODS_VERSION=2.1.0
    export ENABLE_VOICE=false
    export ENABLE_WORKFLOWS=false
    export ENABLE_RAG=false
    export ENABLE_HERMES=false
    export ENABLE_OPENCLAW=false
    export EXTERNAL_LLM_URL=http://127.0.0.1:11434
    export EXTERNAL_LLM_CONTAINER_URL=http://host.docker.internal:11434
    export EXTERNAL_LLM_PROVIDER=ollama
    export EXTERNAL_LLM_MODEL=qwen3.5:9b
    export LEMONADE_EXTERNAL=false

    source "$install_dir/installers/lib/constants.sh"
    source "$install_dir/installers/lib/logging.sh"
    source "$install_dir/installers/lib/ui.sh"
    source "$install_dir/installers/lib/detection.sh"
    source "$install_dir/installers/lib/progress.sh"

    ods_progress() { :; }
    ai() { :; }
    ai_ok() { :; }
    ai_warn() { :; }
    ai_bad() { :; }
    chapter() { :; }
    signal() { :; }
    show_phase() { :; }
    sudo() { return 0; }
    docker() {
        if [[ "${1:-}" == "info" && "${2:-}" == "--format" ]]; then
            printf '4\n'
            return 0
        fi
        command docker "$@"
    }

    source "$install_dir/installers/phases/06-directories.sh"

    grep -qx 'ODS_MODE=local' "$install_dir/.env"
    grep -qx 'LLM_BACKEND=external' "$install_dir/.env"
    grep -qx 'LLM_API_BASE_PATH=/v1' "$install_dir/.env"
    grep -qx 'AMD_INFERENCE_RUNTIME=' "$install_dir/.env"
    grep -qx 'AMD_INFERENCE_BACKEND=' "$install_dir/.env"
    grep -qx 'AMD_INFERENCE_LOCATION=' "$install_dir/.env"
    grep -qx 'AMD_INFERENCE_PORT=' "$install_dir/.env"
    grep -qx 'AMD_INFERENCE_MANAGED=' "$install_dir/.env"
)

if run_phase06_amd_external; then
    pass "AMD external reuse writes one coherent non-Lemonade backend contract"
else
    fail "AMD external reuse .env contract"
fi

if python3 "$ROOT_DIR/tests/test-external-completion-probe.py"; then
    pass "completion probe distinguishes reasoning budget exhaustion from invalid responses"
else
    fail "external completion response validation"
fi

printf '\nResult: %d passed, %d failed\n' "$PASSED" "$FAILED"
[[ "$FAILED" -eq 0 ]]
