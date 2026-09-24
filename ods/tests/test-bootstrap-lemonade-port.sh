#!/usr/bin/env bash
# ============================================================================
# Regression: bootstrap-upgrade.sh must probe each runtime on ITS OWN port.
#
# .env always carries OLLAMA_PORT (the llama-server external port, default
# 11434). The upgrade hot-swap used ${OLLAMA_PORT:-8080} for the Lemonade
# (AMD) health URL, the live-model-id resolver, and the load warm-up — so on
# AMD installs every Lemonade probe hit :11434 where nothing listens, the
# health wait timed out, and the resolver silently fell back to a guessed
# "extra.<file>" model id. The non-AMD health check also fell back to the
# container-internal 8080 instead of the external default 11434.
#
# Contract:
#   AMD branches use AMD_INFERENCE_PORT (default 8080, native Lemonade)
#   non-AMD health uses OLLAMA_PORT (default 11434, external — never 8080)
# ============================================================================
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
SCRIPT="${ODS_ROOT}/scripts/bootstrap-upgrade.sh"

PASSED=0
FAILED=0

check() {
    local name="$1" expected="$2" actual="$3"
    if [[ "$actual" == "$expected" ]]; then
        echo "PASS $name"
        PASSED=$((PASSED + 1))
    else
        echo "FAIL $name: expected $expected, got $actual"
        FAILED=$((FAILED + 1))
    fi
}

# Extract the backend-aware health URL selection and evaluate it directly.
HEALTH_BLOCK="$(awk '
    /# Pick health endpoint based on GPU backend/ {f=1}
    f {print}
    f && /^    fi$/ {exit}
' "$SCRIPT")"

health_url() {  # $1=backend $2=amd_port $3=ollama_port
    # shellcheck disable=SC2034 # consumed by the eval'd HEALTH_BLOCK
    local _gpu_backend="$1" AMD_INFERENCE_PORT="$2" OLLAMA_PORT="$3" _health_url=""
    eval "$HEALTH_BLOCK"
    printf '%s' "$_health_url"
}

check "amd health probes AMD_INFERENCE_PORT" \
    "http://127.0.0.1:8080/api/v1/health" \
    "$(health_url amd 8080 11434)"

check "amd health honors configured AMD_INFERENCE_PORT" \
    "http://127.0.0.1:9000/api/v1/health" \
    "$(health_url amd 9000 11434)"

check "nvidia health probes external OLLAMA_PORT" \
    "http://127.0.0.1:11434/health" \
    "$(health_url nvidia 8080 11434)"

check "nvidia health default is external 11434, not internal 8080" \
    "http://127.0.0.1:11434/health" \
    "$(health_url nvidia '' '')"

# The Lemonade-only call sites must not use the llama-server port.
# shellcheck disable=SC2016 # literal grep patterns, not expansions
lemonade_hits=$(grep -c 'resolve_live_lemonade_model_id "${AMD_INFERENCE_PORT:-8080}"' "$SCRIPT" || true)
check "live-model resolver uses AMD_INFERENCE_PORT (2 inline sites)" "2" "$lemonade_hits"

# shellcheck disable=SC2016
if grep -n 'resolve_live_lemonade_model_id "${OLLAMA_PORT' "$SCRIPT" >/dev/null; then
    echo "FAIL resolver still reads OLLAMA_PORT for Lemonade"
    FAILED=$((FAILED + 1))
else
    echo "PASS resolver never reads OLLAMA_PORT for Lemonade"
    PASSED=$((PASSED + 1))
fi

# shellcheck disable=SC2016
if grep -n '127.0.0.1:${OLLAMA_PORT:-8080}/api/v1/chat/completions' "$SCRIPT" >/dev/null; then
    echo "FAIL Lemonade warm-up still targets OLLAMA_PORT"
    FAILED=$((FAILED + 1))
else
    echo "PASS Lemonade warm-up targets the native port"
    PASSED=$((PASSED + 1))
fi

# The post-cleanup Lemonade refresh must read the native port like its
# sibling code paths (it used to read OLLAMA_PORT here).
# shellcheck disable=SC2016
check "no Lemonade port var reads OLLAMA_PORT" "0" \
    "$(grep -c 'lemonade_port="$(read_env_value OLLAMA_PORT)"' "$SCRIPT" || true)"

echo ""
echo "Passed: $PASSED  Failed: $FAILED"
[[ "$FAILED" -eq 0 ]]
