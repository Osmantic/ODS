#!/usr/bin/env bash
# The .env that installers/macos/lib/env-generator.sh writes must pass the
# project's own schema validator (scripts/validate-env.sh): every key declared
# in .env.schema.json, values of the declared types, and no key assigned twice.
# Two commits each added `LLM_BACKEND=llama-server` to the heredoc, so every
# macOS install failed validation with a duplicate-key error.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$candidate" ]] && exec "$candidate" "$0" "$@"
    done
    echo "[FAIL] Bash 4+ is required" >&2
    exit 1
fi
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_GENERATOR="$ROOT_DIR/installers/macos/lib/env-generator.sh"
VALIDATOR="$ROOT_DIR/scripts/validate-env.sh"
SCHEMA="$ROOT_DIR/.env.schema.json"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$ENV_GENERATOR" ]] || fail "missing $ENV_GENERATOR"
[[ -f "$VALIDATOR" ]] || fail "missing $VALIDATOR"
command -v jq >/dev/null 2>&1 || fail "jq is required by validate-env.sh"

# The generator only shells out for the Docker CPU budget and the hostname;
# neither is a property of the file under test.
STUB_DIR="$TMP_DIR/bin"
mkdir -p "$STUB_DIR"
printf '#!/bin/sh\nexit 1\n' > "$STUB_DIR/docker"
printf '#!/bin/sh\necho ods-test-mac\n' > "$STUB_DIR/hostname"
chmod +x "$STUB_DIR/docker" "$STUB_DIR/hostname"

# Render the .env for one tier the way install-macos.sh does: constants,
# tier map, then the generator with the tier's model variables resolved.
generate_env() {
    local tier="$1"
    local install_dir="$2"
    mkdir -p "$install_dir/config/searxng"
    (
        export PATH="$STUB_DIR:$PATH"
        export ODS_INSTALL_DIR="$install_dir"
        export HOME="$TMP_DIR/home"
        # shellcheck source=/dev/null
        . "$ROOT_DIR/installers/macos/lib/constants.sh"
        # shellcheck source=/dev/null
        . "$ROOT_DIR/installers/macos/lib/tier-map.sh"
        # shellcheck source=/dev/null
        . "$ENV_GENERATOR"
        # shellcheck disable=SC2329  # called by generate_ods_env
        calculate_llama_cpu_budget() { printf '4 1 8\n'; }
        SYSTEM_RAM_GB=64
        DOCKER_BACKEND="docker-desktop"
        ODS_MODEL_SWITCHBOARD="enabled"
        resolve_tier_config "$tier" || exit 3
        generate_ods_env "$install_dir" "$tier" true
    ) >"$install_dir/generate.log" 2>&1 \
        || fail "generate_ods_env failed for tier $tier: $(tail -n 3 "$install_dir/generate.log")"
    [[ -f "$install_dir/.env" ]] || fail "tier $tier produced no .env"
}

duplicate_keys() {
    grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$1" \
        | cut -d= -f1 \
        | sort \
        | uniq -d
}

for tier in 1 CLOUD; do
    install_dir="$TMP_DIR/tier-$tier"
    generate_env "$tier" "$install_dir"
    env_file="$install_dir/.env"

    dupes="$(duplicate_keys "$env_file")"
    [[ -z "$dupes" ]] \
        || fail "tier $tier .env assigns a key more than once: $(printf '%s' "$dupes" | tr '\n' ' ')"
    [[ "$(grep -c '^LLM_BACKEND=' "$env_file")" -eq 1 ]] \
        || fail "tier $tier .env must declare LLM_BACKEND exactly once"
    pass "tier $tier: generated .env assigns every key once"

    # validate-env.sh needs Bash 4+ (associative arrays); ods-cli runs it with
    # "$BASH" for the same reason, so do not go through its /bin/bash shebang.
    if ! output="$("$BASH" "$VALIDATOR" "$env_file" "$SCHEMA" 2>&1)"; then
        fail "tier $tier .env does not validate against .env.schema.json: $(printf '%s' "$output" | grep -E 'ERROR|  - ' | head -n 4 | tr '\n' ' ')"
    fi
    pass "tier $tier: generated .env validates against .env.schema.json"
done
