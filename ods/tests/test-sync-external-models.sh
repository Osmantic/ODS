#!/usr/bin/env bash
# ============================================================================
# Tests for ods/scripts/sync-external-models.sh
# ============================================================================
# Covers:
#   - Unit: _gguf_base strips quantization suffixes correctly
#   - Unit: _gguf_to_ollama_tag derives the right Ollama tag
#   - Functional: already_present short-circuit
#   - Functional: no provider available → not_found (no crash)
#   - Functional: LM Studio exact filename match
#   - Functional: LM Studio fuzzy/family match (different quantization)
#   - Functional: Ollama not running → not_found (no crash)
#   - Docker-safety: synced dest must be a real file, not a symlink
#
# Variable isolation: test helpers use Bash dynamic-scoped `local` so that
# called functions (sync_model etc.) see the per-test overrides of
# ODS_MODELS_DIR and HOME without touching global state.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SYNC_SCRIPT="$ROOT_DIR/scripts/sync-external-models.sh"

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

# Source the sync script to load all helper functions.
# The BASH_SOURCE guard at the bottom of the script prevents sync_model from
# auto-executing on source.
# shellcheck source=../scripts/sync-external-models.sh
source "$SYNC_SCRIPT"

# ── Shared helpers ────────────────────────────────────────────────────────────

_tmpdir() { mktemp -d; }

_make_fake_gguf() {
    local path="$1"
    mkdir -p "$(dirname "$path")"
    printf 'GGUF fake content for testing\n' > "$path"
}

# ── Unit: _gguf_base ─────────────────────────────────────────────────────────

_check_gguf_base() {
    local input="$1" expected="$2"
    local got
    got="$(_gguf_base "$input")"
    if [[ "$got" == "$expected" ]]; then
        pass "_gguf_base \"$input\" → \"$expected\""
    else
        fail "_gguf_base \"$input\": expected \"$expected\", got \"$got\""
    fi
}

_check_gguf_base "Qwen3.5-2B-Q4_K_M"         "Qwen3.5-2B"
_check_gguf_base "Phi-4-mini-instruct-Q4_K_M" "Phi-4-mini-instruct"
_check_gguf_base "Mistral-7B-v0.1-Q8_0"       "Mistral-7B-v0.1"
_check_gguf_base "Llama-3.2-3B-F16"           "Llama-3.2-3B"
_check_gguf_base "SomeModel"                  "SomeModel"

# ── Unit: _gguf_to_ollama_tag ─────────────────────────────────────────────────

_check_ollama_tag() {
    local input="$1" expected="$2"
    local got
    got="$(_gguf_to_ollama_tag "$input")"
    if [[ "$got" == "$expected" ]]; then
        pass "_gguf_to_ollama_tag \"$input\" → \"$expected\""
    else
        fail "_gguf_to_ollama_tag \"$input\": expected \"$expected\", got \"$got\""
    fi
}

_check_ollama_tag "Qwen3.5-2B-Q4_K_M.gguf"          "qwen3.5:2b"
_check_ollama_tag "Mistral-7B-v0.1-Q4_K_M.gguf"     "mistral-7b:v0.1"
_check_ollama_tag "Llama-3.2-3B-Instruct-Q8_0.gguf"  "llama-3.2:3b"

# ── Functional tests ──────────────────────────────────────────────────────────
#
# Each test function uses `local ODS_MODELS_DIR HOME OLLAMA_HOST` to shadow the
# globals for the duration of the call.  Bash's dynamic scoping propagates those
# locals into sync_model / _sync_from_lmstudio / _lmstudio_model_dirs etc., so
# we get full isolation without subshells that would swallow the PASS/FAIL
# counters.

# ── test: already_present ─────────────────────────────────────────────────────

_test_already_present() {
    local td ODS_MODELS_DIR result
    td="$(_tmpdir)"
    ODS_MODELS_DIR="$td/data/models"
    mkdir -p "$ODS_MODELS_DIR"
    _make_fake_gguf "$ODS_MODELS_DIR/TestModel-Q4_K_M.gguf"

    result="$(sync_model "TestModel-Q4_K_M.gguf")"
    if [[ "$result" == "already_present" ]]; then
        pass "already_present: returned when file already in ODS models dir"
    else
        fail "already_present: expected already_present, got: $result"
    fi
    rm -rf "$td"
}
_test_already_present

# ── test: no provider → not_found ────────────────────────────────────────────

_test_no_provider() {
    local td ODS_MODELS_DIR HOME OLLAMA_HOST result
    td="$(_tmpdir)"
    ODS_MODELS_DIR="$td/data/models"
    HOME="$td"
    OLLAMA_HOST="http://127.0.0.1:19999"
    mkdir -p "$ODS_MODELS_DIR"

    result="$(sync_model "TestModel-Q4_K_M.gguf")"
    if [[ "$result" == "not_found" ]]; then
        pass "no-provider: returns not_found cleanly"
    else
        fail "no-provider: expected not_found, got: $result"
    fi
    rm -rf "$td"
}
_test_no_provider

# ── test: LM Studio exact filename match ─────────────────────────────────────

_test_lmstudio_exact() {
    local td ODS_MODELS_DIR HOME OLLAMA_HOST result dest
    td="$(_tmpdir)"
    ODS_MODELS_DIR="$td/data/models"
    HOME="$td/fake-home"
    OLLAMA_HOST="http://127.0.0.1:19999"
    mkdir -p "$ODS_MODELS_DIR"
    _make_fake_gguf "$HOME/.lmstudio/models/OrgA/TestModel/TestModel-Q4_K_M.gguf"

    result="$(sync_model "TestModel-Q4_K_M.gguf")"
    dest="$ODS_MODELS_DIR/TestModel-Q4_K_M.gguf"

    if [[ "$result" == synced:lmstudio:* ]]; then
        pass "LM Studio exact match: returns synced:lmstudio:..."
    else
        fail "LM Studio exact match: expected synced:lmstudio:..., got: $result"
    fi

    if [[ -f "$dest" ]]; then
        pass "LM Studio exact match: dest file exists"
    else
        fail "LM Studio exact match: dest file missing at $dest"
    fi

    # Docker-safety: must NOT be a symlink — symlinks to host paths are broken
    # inside the container's ./data/models:/models mount.
    if [[ ! -L "$dest" ]]; then
        pass "LM Studio exact match: dest is a real file (not a symlink) — Docker-safe"
    else
        fail "LM Studio exact match: dest is a symlink — will break inside Docker container"
    fi

    rm -rf "$td"
}
_test_lmstudio_exact

# ── test: LM Studio fuzzy/family match (different quantization) ───────────────

_test_lmstudio_fuzzy() {
    local td ODS_MODELS_DIR HOME OLLAMA_HOST result dest
    td="$(_tmpdir)"
    ODS_MODELS_DIR="$td/data/models"
    HOME="$td/fake-home"
    OLLAMA_HOST="http://127.0.0.1:19999"
    mkdir -p "$ODS_MODELS_DIR"
    # LM Studio has Q8_0; ODS requests Q4_K_M — fuzzy match should bridge the gap
    _make_fake_gguf "$HOME/.lmstudio/models/OrgA/TestModel/TestModel-Q8_0.gguf"

    result="$(sync_model "TestModel-Q4_K_M.gguf")"
    # Dest is always named after what ODS requested, not what LM Studio had
    dest="$ODS_MODELS_DIR/TestModel-Q4_K_M.gguf"

    if [[ "$result" == synced:lmstudio:* ]]; then
        pass "LM Studio fuzzy match: returns synced:lmstudio:..."
    else
        fail "LM Studio fuzzy match: expected synced:lmstudio:..., got: $result"
    fi

    if [[ -f "$dest" ]]; then
        pass "LM Studio fuzzy match: dest file exists under requested name"
    else
        fail "LM Studio fuzzy match: dest file missing at $dest"
    fi

    if [[ ! -L "$dest" ]]; then
        pass "LM Studio fuzzy match: dest is a real file (not a symlink) — Docker-safe"
    else
        fail "LM Studio fuzzy match: dest is a symlink — will break inside Docker container"
    fi

    rm -rf "$td"
}
_test_lmstudio_fuzzy

# ── test: Ollama not running → not_found ─────────────────────────────────────

_test_ollama_not_running() {
    local td ODS_MODELS_DIR HOME OLLAMA_HOST result
    td="$(_tmpdir)"
    ODS_MODELS_DIR="$td/data/models"
    HOME="$td"
    OLLAMA_HOST="http://127.0.0.1:19999"
    mkdir -p "$ODS_MODELS_DIR"

    result="$(sync_model "TestModel-Q4_K_M.gguf")"
    if [[ "$result" == "not_found" ]]; then
        pass "Ollama not running: returns not_found cleanly (no crash)"
    else
        fail "Ollama not running: expected not_found, got: $result"
    fi
    rm -rf "$td"
}
_test_ollama_not_running

# ── Report ────────────────────────────────────────────────────────────────────

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
