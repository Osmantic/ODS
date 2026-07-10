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

# ── test: SYNC_EXACT_ONLY blocks fuzzy match ──────────────────────────────────
#
# Installer paths set SYNC_EXACT_ONLY=true so a differently-quantized LM Studio
# file cannot satisfy the model-presence check and silently bypass the sha256
# integrity download (unsafe on macOS where sha256sum may be absent).

_test_exact_only_blocks_fuzzy() {
    local td ODS_MODELS_DIR HOME OLLAMA_HOST SYNC_EXACT_ONLY result
    td="$(_tmpdir)"
    ODS_MODELS_DIR="$td/data/models"
    HOME="$td/fake-home"
    OLLAMA_HOST="http://127.0.0.1:19999"
    SYNC_EXACT_ONLY=true
    mkdir -p "$ODS_MODELS_DIR"
    # LM Studio has Q8_0 but ODS requests Q4_K_M — must NOT match under exact-only
    _make_fake_gguf "$HOME/.lmstudio/models/OrgA/TestModel/TestModel-Q8_0.gguf"

    result="$(sync_model "TestModel-Q4_K_M.gguf")"
    if [[ "$result" == "not_found" ]]; then
        pass "SYNC_EXACT_ONLY: fuzzy match correctly blocked; returns not_found"
    else
        fail "SYNC_EXACT_ONLY: expected not_found, got: $result (wrong-quant file must not satisfy installer check)"
    fi

    if [[ ! -f "$ODS_MODELS_DIR/TestModel-Q4_K_M.gguf" ]]; then
        pass "SYNC_EXACT_ONLY: no file written to models dir when fuzzy blocked"
    else
        fail "SYNC_EXACT_ONLY: file was written despite exact-only guard"
    fi

    rm -rf "$td"
}
_test_exact_only_blocks_fuzzy

# ── test: late installer sync (SYNC_EXACT_ONLY) leaves dest absent ────────────
#
# Mirrors the phase-11 late full-model sync at line 1027-1029: with only a
# Q8_0 file present in LM Studio and the requested target being Q4_K_M,
# SYNC_EXACT_ONLY=true must leave the destination absent so the normal
# background download is not skipped.

_test_late_installer_sync_exact_only() {
    local td ODS_MODELS_DIR HOME OLLAMA_HOST SYNC_EXACT_ONLY result dest
    td="$(_tmpdir)"
    ODS_MODELS_DIR="$td/data/models"
    HOME="$td/fake-home"
    OLLAMA_HOST="http://127.0.0.1:19999"
    SYNC_EXACT_ONLY=true
    mkdir -p "$ODS_MODELS_DIR"
    _make_fake_gguf "$HOME/.lmstudio/models/OrgA/TestModel/TestModel-Q8_0.gguf"
    dest="$ODS_MODELS_DIR/TestModel-Q4_K_M.gguf"

    result="$(sync_model "TestModel-Q4_K_M.gguf")"
    if [[ "$result" == "not_found" ]]; then
        pass "late installer sync: SYNC_EXACT_ONLY returns not_found for wrong-quant source"
    else
        fail "late installer sync: expected not_found, got: $result"
    fi

    if [[ ! -f "$dest" ]]; then
        pass "late installer sync: dest absent — background download will not be skipped"
    else
        fail "late installer sync: dest was created; background download would be incorrectly skipped"
    fi

    rm -rf "$td"
}
_test_late_installer_sync_exact_only

# ── test: CLI subprocess — not_found exits 0 (survives set -e callers) ────────
#
# Reproduces the exact failure the reviewer hit: `ods model sync MissingModel`
# exits 1 after "Searching..." because the script's exit 1 propagates through
# the $() command substitution under set -e, killing the CLI before it can reach
# the not_found guidance.  The fix is not_found → exit 0 in the script.

_test_cli_not_found_exit_code() {
    local td exit_code output
    td="$(_tmpdir)"

    output="$(ODS_MODELS_DIR="$td/data/models" HOME="$td" OLLAMA_HOST="http://127.0.0.1:19999" \
        bash "$SYNC_SCRIPT" "MissingModel-Q4_K_M.gguf")" || exit_code=$?
    exit_code="${exit_code:-0}"

    if [[ "$output" == "not_found" ]]; then
        pass "CLI not_found: script prints not_found"
    else
        fail "CLI not_found: expected output 'not_found', got: $output"
    fi

    if [[ "$exit_code" -eq 0 ]]; then
        pass "CLI not_found: script exits 0 so set -e callers survive"
    else
        fail "CLI not_found: script exited $exit_code; set -e callers will die before handling the result"
    fi

    rm -rf "$td"
}
_test_cli_not_found_exit_code

# ── Report ────────────────────────────────────────────────────────────────────

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
