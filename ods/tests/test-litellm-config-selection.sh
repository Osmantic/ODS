#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SELECTOR="$ROOT_DIR/extensions/services/litellm/select-config.sh"
COMPOSE="$ROOT_DIR/extensions/services/litellm/compose.yaml"
CLOUD_CONFIG="$ROOT_DIR/config/litellm/cloud.yaml"
passed=0
failed=0

check_selection() {
    local mode="$1"
    local switchboard="$2"
    local expected="$3"
    local actual

    actual="$(
        ODS_MODE="$mode" ODS_MODEL_SWITCHBOARD="$switchboard" \
            sh "$SELECTOR" /mode.yaml /switchboard.yaml
    )"
    if [[ "$actual" == "$expected" ]]; then
        printf '[PASS] %s + %s -> %s\n' "$mode" "$switchboard" "$expected"
        passed=$((passed + 1))
    else
        printf '[FAIL] %s + %s: expected %s, got %s\n' \
            "$mode" "$switchboard" "$expected" "$actual"
        failed=$((failed + 1))
    fi
}

[[ -f "$SELECTOR" ]] || {
    printf '[FAIL] LiteLLM config selector is missing\n'
    exit 1
}

check_selection local observe /mode.yaml
check_selection local enabled /switchboard.yaml
check_selection hybrid enabled /switchboard.yaml
check_selection lemonade enabled /switchboard.yaml
check_selection cloud observe /mode.yaml
check_selection cloud enabled /mode.yaml

default_selection="$(
    env -u ODS_MODEL_SWITCHBOARD ODS_MODE=local \
        sh "$SELECTOR" /mode.yaml /switchboard.yaml
)"
if [[ "$default_selection" == /switchboard.yaml ]]; then
    printf '[PASS] local default -> stable switchboard alias\n'
    passed=$((passed + 1))
else
    printf '[FAIL] local default: expected /switchboard.yaml, got %s\n' "$default_selection"
    failed=$((failed + 1))
fi

if grep -Fq 'ODS_MODE=${ODS_MODE:-local}' "$COMPOSE" \
    && grep -Fq 'ODS_MODEL_SWITCHBOARD=${ODS_MODEL_SWITCHBOARD:-enabled}' "$COMPOSE" \
    && grep -Fq 'sh /app/ods-select-config.sh' "$COMPOSE"; then
    printf '[PASS] Compose passes mode and delegates config selection\n'
    passed=$((passed + 1))
else
    printf '[FAIL] Compose does not use the tested config selector\n'
    failed=$((failed + 1))
fi

if grep -Fq '[ -z "$$CONFIG_PATH" ]' "$COMPOSE" \
    && grep -Fq 'LiteLLM config selector returned an unreadable path' "$COMPOSE"; then
    printf '[PASS] Compose fails closed when selector output is empty or unreadable\n'
    passed=$((passed + 1))
else
    printf '[FAIL] Compose does not guard selector output before LiteLLM startup\n'
    failed=$((failed + 1))
fi

if grep -Fq 'model_name: ods/current' "$CLOUD_CONFIG"; then
    printf '[PASS] Cloud config exposes the stable ods/current alias\n'
    passed=$((passed + 1))
else
    printf '[FAIL] Cloud config does not expose the stable ods/current alias\n'
    failed=$((failed + 1))
fi

printf '\nResult: %d passed, %d failed\n' "$passed" "$failed"
(( failed == 0 ))
