#!/usr/bin/env bash
# ============================================================================
# ODS Installer Tests — External Service Detection
# ============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/installers/lib/external-services.sh"

_assert_eq() {
    local got="$1" expected="$2" label="$3"
    if [ "$got" != "$expected" ]; then
        echo "[FAIL] ${label}: expected '${expected}', got '${got}'"
        exit 1
    fi
    echo "[PASS] ${label}"
}

# normalize_model_name tests
_assert_eq "$(normalize_model_name 'qwen3:8b')"              "qwen3-8b"      "ollama colon format"
_assert_eq "$(normalize_model_name 'Qwen3-8B-Q4_K_M.gguf')" "qwen3-8b"      "gguf with quant suffix"
_assert_eq "$(normalize_model_name 'llama3.2:3b')"           "llama3.2-3b"   "version dot in name"
_assert_eq "$(normalize_model_name 'deepseek-r1:7b')"        "deepseek-r1-7b" "hyphenated name"

# model_family_matches tests
if model_family_matches "Qwen3-8B-Q4_K_M.gguf" "qwen3:8b"; then
    echo "[PASS] gguf matches ollama tag"
else
    echo "[FAIL] gguf matches ollama tag"
    exit 1
fi

if model_family_matches "llama3:8b" "qwen3:8b"; then
    echo "[FAIL] different models should not match"
    exit 1
else
    echo "[PASS] different models do not match"
fi

# find_matching_external_model tests
model_list="mistral:7b
qwen3:8b
phi3:mini"

result=$(find_matching_external_model "Qwen3-8B-Q4_K_M.gguf" "$model_list")
_assert_eq "$result" "qwen3:8b" "find match in list"

result=$(find_matching_external_model "gemma2:9b" "$model_list") || result=""
_assert_eq "$result" "" "no match returns empty"

# Regression test: source 02b-external-services.sh under set -u with stubs and unset variables
(
    set -u
    # Stub required functions and variables
    ods_progress() { :; }
    chapter() { :; }
    log() { :; }
    warn() { :; }
    ai() { :; }
    ai_ok() { :; }
    resolve_compose_config() { :; }

    SCRIPT_DIR="$ROOT_DIR"
    INTERACTIVE=false
    DRY_RUN=false
    ODS_MODE=local
    GGUF_FILE="qwen3:8b"

    # Ensure EXTERNAL_LLM_URL is NOT set/bound
    unset EXTERNAL_LLM_URL
    unset EXTERNAL_LLM_PROVIDER
    unset EXTERNAL_LLM_MODEL
    unset SKIP_MODEL_DOWNLOAD
    unset LEMONADE_EXTERNAL

    # Source the phase — this should not crash with unbound variable error
    source "$ROOT_DIR/installers/phases/02b-external-services.sh"
    echo "[PASS] Sourced 02b-external-services.sh with unset variables under set -u"
)

echo ""
echo "[PASS] All external-services tests passed"
