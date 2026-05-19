#!/bin/bash
# ============================================================================
# Test: installer model-selector env application is eval-free  (#1271)
# ============================================================================
# SECURITY REGRESSION TEST.
#
# installers/phases/02-detection.sh and installers/macos/install-macos.sh
# used to `eval "$_selector_env"` on the (untrusted) output of
# scripts/select-model.py. A spoofed/crafted device string could inject
# `KEY=1; touch /tmp/ds_pwned` and get arbitrary code execution during a
# privileged install (#1271).
#
# This test asserts:
#   1. Neither phase script `eval`s the selector output anymore.
#   2. The safe parser (lib/safe-env.sh::load_env_from_output) does NOT
#      execute injected shell, even when the payload is
#      `x=1; touch /tmp/ds_pwned` (assert /tmp/ds_pwned is absent after).
#   3. All legitimate allowlisted KEY="value" lines still parse and export.
#
# Run: bash tests/test-installer-selector-env-no-eval.sh
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0
PWNED_MARKER="/tmp/ds_pwned"

pass() { echo "  PASS: $1"; ((PASS++)); }
fail() { echo "  FAIL: $1"; ((FAIL++)); }

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then pass "$label"
    else fail "$label (expected '$expected', got '$actual')"; fi
}

cleanup() { rm -f "$PWNED_MARKER"; }
trap cleanup EXIT
cleanup

echo "== #1271: installer selector-env is eval-free =="

# ---------------------------------------------------------------------------
# 1. Static guard: the dangerous `eval "$_selector_env"` must be gone.
# ---------------------------------------------------------------------------
for rel in installers/phases/02-detection.sh installers/macos/install-macos.sh; do
    f="$SCRIPT_DIR/$rel"
    if [[ ! -f "$f" ]]; then fail "$rel missing"; continue; fi
    if grep -Eq 'eval[[:space:]]+"?\$(\{)?_selector_env' "$f"; then
        fail "$rel still eval's \$_selector_env"
    else
        pass "$rel does not eval \$_selector_env"
    fi
    # The script must still consume the selector contract somehow
    # (safe parser call or per-line read) — not silently drop it.
    if grep -Eq 'load_env_from_output|while[[:space:]]+IFS=.*read.*_selector_env|<<<[[:space:]]*"?\$\{?_selector_env' "$f" \
       || grep -Eq '_selector_env' "$f"; then
        pass "$rel still consumes the selector contract"
    else
        fail "$rel no longer references the selector env (regression risk)"
    fi
done

# ---------------------------------------------------------------------------
# 2. The safe parser must NOT execute injected shell.
#    This is the exact payload from the issue.
# ---------------------------------------------------------------------------
SAFE_ENV="$SCRIPT_DIR/lib/safe-env.sh"
if [[ ! -f "$SAFE_ENV" ]]; then
    fail "lib/safe-env.sh missing — cannot validate safe parser"
else
    # shellcheck source=/dev/null
    . "$SAFE_ENV"

    if ! declare -F load_env_from_output >/dev/null 2>&1; then
        fail "load_env_from_output not defined in lib/safe-env.sh"
    else
        # Attack payload exactly as described in #1271. Feed it the way the
        # selector output would arrive (stdin), in a subshell so any exported
        # vars don't leak into the harness.
        (
            printf '%s\n' \
                'LLM_MODEL="qwen3:8b"' \
                'x=1; touch '"$PWNED_MARKER" \
                '$(touch '"$PWNED_MARKER"')' \
                '`touch '"$PWNED_MARKER"'`' \
                'GGUF_FILE="model.gguf"; rm -rf /tmp/should_not_run' \
                'MAX_CONTEXT=8192' \
            | load_env_from_output
        )
        if [[ -e "$PWNED_MARKER" ]]; then
            fail "RCE: injected payload executed (created $PWNED_MARKER)"
        else
            pass "injected shell payload did NOT execute (no $PWNED_MARKER)"
        fi

        # Command-substitution / backtick attack in the *value* must not run.
        (
            printf '%s\n' 'LLM_MODEL="$(touch '"$PWNED_MARKER"')"' \
            | load_env_from_output
        )
        if [[ -e "$PWNED_MARKER" ]]; then
            fail "RCE: command substitution in value executed"
        else
            pass "command substitution in value did NOT execute"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 3. Legitimate allowlisted keys still parse from real selector output shape.
#    select-model.py emits  KEY=<shlex.quoted value>  lines.
# ---------------------------------------------------------------------------
if declare -F load_env_from_output >/dev/null 2>&1; then
    eval_result="$(
        # Subshell: parse a realistic selector contract, then echo the vars.
        printf '%s\n' \
            'LLM_MODEL="qwen3-30b-a3b"' \
            'GGUF_FILE="qwen3-30b.gguf"' \
            'GGUF_URL="https://example.test/qwen3-30b.gguf"' \
            'GGUF_SHA256="abc123def456"' \
            'MAX_CONTEXT="131072"' \
            'LLM_MODEL_SIZE_MB="18000"' \
            'MODEL_RECOMMENDATION_SOURCE="catalog"' \
            'MODEL_RECOMMENDATION_POLICY="evidence"' \
            'MODEL_RECOMMENDATION_CONFIDENCE="high"' \
            'MODEL_RECOMMENDATION_REASON="Catalog fit: good"' \
            'MODEL_RECOMMENDED_ALTERNATIVES="a:8192:6;b:4096:4"' \
            'MODEL_RUNTIME_PROFILE="rp-1"' \
            'LLAMA_SERVER_IMAGE="ghcr.io/example/llama:1"' \
        | { load_env_from_output; printf '%s\n' \
              "LLM_MODEL=$LLM_MODEL" \
              "GGUF_FILE=$GGUF_FILE" \
              "GGUF_SHA256=$GGUF_SHA256" \
              "MAX_CONTEXT=$MAX_CONTEXT" \
              "MODEL_RECOMMENDATION_REASON=$MODEL_RECOMMENDATION_REASON" \
              "MODEL_RUNTIME_PROFILE=$MODEL_RUNTIME_PROFILE" \
              "LLAMA_SERVER_IMAGE=$LLAMA_SERVER_IMAGE"; }
    )"
    get() { printf '%s\n' "$eval_result" | grep -m1 "^$1=" | cut -d= -f2-; }
    assert_eq "LLM_MODEL parsed"                 "qwen3-30b-a3b"            "$(get LLM_MODEL)"
    assert_eq "GGUF_FILE parsed"                 "qwen3-30b.gguf"          "$(get GGUF_FILE)"
    assert_eq "GGUF_SHA256 parsed"               "abc123def456"            "$(get GGUF_SHA256)"
    assert_eq "MAX_CONTEXT parsed"               "131072"                  "$(get MAX_CONTEXT)"
    assert_eq "MODEL_RECOMMENDATION_REASON kept" "Catalog fit: good"       "$(get MODEL_RECOMMENDATION_REASON)"
    assert_eq "MODEL_RUNTIME_PROFILE parsed"     "rp-1"                    "$(get MODEL_RUNTIME_PROFILE)"
    assert_eq "LLAMA_SERVER_IMAGE parsed"        "ghcr.io/example/llama:1" "$(get LLAMA_SERVER_IMAGE)"
fi

echo
echo "== #1271 results: $PASS passed, $FAIL failed =="
[[ $FAIL -eq 0 ]] || exit 1
