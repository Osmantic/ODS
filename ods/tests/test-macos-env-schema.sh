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
    local force_overwrite="${3:-true}"
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
        generate_ods_env "$install_dir" "$tier" "$force_overwrite" || exit $?
        # The installer persists this native choice before Pixel activation.
        upsert_env_value "$install_dir/.env" ENABLE_OPENCODE "${ODS_TEST_OPENCODE_CHOICE:-false}"
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
    if [[ "$tier" == 1 ]]; then ODS_TEST_OPENCODE_CHOICE=true; else ODS_TEST_OPENCODE_CHOICE=false; fi
    install_dir="$TMP_DIR/tier-$tier"
    generate_env "$tier" "$install_dir"
    env_file="$install_dir/.env"
    grep -qx 'N8N_RUN_USER=node' "$env_file" \
        || fail "tier $tier must use the image user for n8n on macOS"

    dupes="$(duplicate_keys "$env_file")"
    [[ -z "$dupes" ]] \
        || fail "tier $tier .env assigns a key more than once: $(printf '%s' "$dupes" | tr '\n' ' ')"
    [[ "$(grep -c '^LLM_BACKEND=' "$env_file")" -eq 1 ]] \
        || fail "tier $tier .env must declare LLM_BACKEND exactly once"
    grep -qx 'TTS_WORKERS=1' "$env_file" \
        || fail "tier $tier macOS install must use one TTS worker"
    grep -Eq '^TTS_THREADS=[1-4]$' "$env_file" \
        || fail "tier $tier macOS install must bound Kokoro threads"
    grep -qx 'HERMES_REQUIRE_OWNER_CARD=false' "$env_file" \
        || fail "tier $tier must open Hermes without an owner card by default"
    grep -qx "TIER=$tier" "$env_file" \
        || fail "tier $tier .env must persist TIER for ods-cli and the host agent"
    pass "tier $tier: generated .env assigns every key once"

    # validate-env.sh needs Bash 4+ (associative arrays); ods-cli runs it with
    # "$BASH" for the same reason, so do not go through its /bin/bash shebang.
    if ! output="$("$BASH" "$VALIDATOR" "$env_file" "$SCHEMA" 2>&1)"; then
        fail "tier $tier .env does not validate against .env.schema.json: $(printf '%s' "$output" | grep -E 'ERROR|  - ' | head -n 4 | tr '\n' ' ')"
    fi
    pass "tier $tier: generated .env validates against .env.schema.json"
done

tts_override_dir="$TMP_DIR/tts-worker-override"
generate_env 1 "$tts_override_dir"
awk '{ if ($0 == "TTS_WORKERS=1") print "TTS_WORKERS=\0472\047"; else if ($0 ~ /^TTS_THREADS=/) print "TTS_THREADS=\"2\""; else print }' \
    "$tts_override_dir/.env" > "$tts_override_dir/.env.new"
mv "$tts_override_dir/.env.new" "$tts_override_dir/.env"
generate_env 1 "$tts_override_dir" false
grep -qx 'TTS_WORKERS=2' "$tts_override_dir/.env" \
    || fail 'macOS reinstall did not preserve a quoted TTS worker override'
grep -qx 'TTS_THREADS=2' "$tts_override_dir/.env" \
    || fail 'macOS reinstall did not preserve a quoted TTS thread override'
pass 'macOS reinstall preserves explicit TTS worker and thread overrides'
sed 's/^TTS_WORKERS=2$/TTS_WORKERS=8/' "$tts_override_dir/.env" > "$tts_override_dir/.env.new"
mv "$tts_override_dir/.env.new" "$tts_override_dir/.env"
generate_env 1 "$tts_override_dir" false
grep -qx 'TTS_THREADS=1' "$tts_override_dir/.env" \
    || fail 'macOS reinstall did not cap stale TTS threads after a worker increase'
pass 'macOS reinstall caps TTS threads to the new per-worker CPU budget'

hermes_override_dir="$TMP_DIR/hermes-owner-card-override"
generate_env 1 "$hermes_override_dir"
sed 's/^HERMES_REQUIRE_OWNER_CARD=false$/HERMES_REQUIRE_OWNER_CARD=true/' \
    "$hermes_override_dir/.env" > "$hermes_override_dir/.env.new"
mv "$hermes_override_dir/.env.new" "$hermes_override_dir/.env"
generate_env 1 "$hermes_override_dir" false
grep -qx 'HERMES_REQUIRE_OWNER_CARD=true' "$hermes_override_dir/.env" \
    || fail 'macOS reinstall did not preserve explicit Hermes owner-card gating'
pass 'macOS reinstall preserves explicit Hermes owner-card gating'

# A forced reinstall must not rotate credentials already bound to a persisted
# Langfuse database. Other install secrets may still rotate under --force.
langfuse_dir="$TMP_DIR/langfuse-force"
generate_env 1 "$langfuse_dir"
mkdir -p "$langfuse_dir/data/langfuse/postgres"
printf '16\n' > "$langfuse_dir/data/langfuse/postgres/PG_VERSION"
old_langfuse="$(grep '^LANGFUSE_' "$langfuse_dir/.env")"
old_dashboard_key="$(grep '^DASHBOARD_API_KEY=' "$langfuse_dir/.env")"
generate_env 1 "$langfuse_dir"
[[ "$(grep '^LANGFUSE_' "$langfuse_dir/.env")" == "$old_langfuse" ]] \
    || fail 'forced reinstall rotated persisted Langfuse credentials'
[[ "$(grep '^DASHBOARD_API_KEY=' "$langfuse_dir/.env")" != "$old_dashboard_key" ]] \
    || fail 'forced reinstall did not rotate an unbound install secret'
pass 'forced reinstall preserves persisted Langfuse credentials'

missing_env_dir="$TMP_DIR/langfuse-without-env"
mkdir -p "$missing_env_dir/data/langfuse/postgres"
printf '16\n' > "$missing_env_dir/data/langfuse/postgres/PG_VERSION"
if (generate_env 1 "$missing_env_dir") >/dev/null 2>&1; then
    fail 'persisted Langfuse database accepted a missing prior .env'
fi
[[ ! -f "$missing_env_dir/.env" ]] \
    || fail 'missing prior Langfuse credentials still produced a new .env'
pass 'persisted Langfuse database fails closed without prior credentials'

sed '/^LANGFUSE_DB_PASSWORD=/d' "$langfuse_dir/.env" > "$langfuse_dir/.env.missing-key"
mv "$langfuse_dir/.env.missing-key" "$langfuse_dir/.env"
if (generate_env 1 "$langfuse_dir") >/dev/null 2>&1; then
    fail 'persisted Langfuse database accepted a missing password'
fi
[[ "$(grep -c '^LANGFUSE_DB_PASSWORD=' "$langfuse_dir/.env" || true)" -eq 0 ]] \
    || fail 'rejected Langfuse credentials were overwritten'
pass 'persisted Langfuse database fails closed on an incomplete prior .env'

# Explicit cache limits must survive regeneration without changing the runtime
# default for owners who did not set one. Exercise the real generator above.
read_cache_value() {
    python3 - "$1" "$ROOT_DIR/extensions/services/dashboard-api" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from env_values import parse_env_value
values = [parse_env_value(line.partition('=')[2])
          for line in Path(sys.argv[1]).read_text().splitlines()
          if line.startswith('LLAMA_ARG_CACHE_RAM=')]
assert len(values) <= 1
print(values[0] if values else '')
PY
}
cache_unset_dir="$TMP_DIR/cache-unset"
LLAMA_ARG_CACHE_RAM= generate_env 1 "$cache_unset_dir"
if grep -q '^LLAMA_ARG_CACHE_RAM=' "$cache_unset_dir/.env"; then
    fail 'unset cache RAM limit gained an invented default'
fi
for cache_value in 0 512; do
    cache_dir="$TMP_DIR/cache-$cache_value"
    LLAMA_ARG_CACHE_RAM="$cache_value" generate_env 1 "$cache_dir"
    [[ "$(read_cache_value "$cache_dir/.env")" == "$cache_value" ]] \
        || fail "explicit cache RAM $cache_value was not retained"
    LLAMA_ARG_CACHE_RAM= generate_env 1 "$cache_dir" false
    [[ "$(read_cache_value "$cache_dir/.env")" == "$cache_value" ]] \
        || fail "existing cache RAM $cache_value was lost on rerun"
    [[ "$(grep -c '^LLAMA_ARG_CACHE_RAM=' "$cache_dir/.env")" -eq 1 ]] \
        || fail 'rerun duplicated the cache RAM setting'
done
LLAMA_ARG_CACHE_RAM=1024 generate_env 1 "$TMP_DIR/cache-0" false
[[ "$(read_cache_value "$TMP_DIR/cache-0/.env")" == 0 ]] \
    || fail 'process environment overrode the existing owner cache limit'
LLAMA_ARG_CACHE_RAM=0 generate_env 1 "$cache_unset_dir" false
[[ "$(read_cache_value "$cache_unset_dir/.env")" == 0 ]] \
    || fail 'rerun failed to backfill an explicit cache limit'
pass 'explicit cache RAM zero and nonzero survive generation and rerun'

# Invalid text is left for the existing runtime validator, but it must remain
# literal data for every dotenv consumer, including a shell reading the line.
cache_literal_dir="$TMP_DIR/cache-literal"
cache_marker="$TMP_DIR/cache-marker"
cache_literal='$(touch '"$cache_marker"') # literal'
LLAMA_ARG_CACHE_RAM="$cache_literal" generate_env 1 "$cache_literal_dir"
cache_line="$(grep '^LLAMA_ARG_CACHE_RAM=' "$cache_literal_dir/.env")"
[[ "$cache_line" == "LLAMA_ARG_CACHE_RAM='$cache_literal'" ]] \
    || fail 'cache RAM literal was not dotenv-quoted'
"$BASH" -c "$cache_line"
[[ ! -e "$cache_marker" ]] || fail 'cache RAM text executed as shell code'
cache_literal_backfill="$TMP_DIR/cache-literal-backfill"
LLAMA_ARG_CACHE_RAM= generate_env 1 "$cache_literal_backfill"
LLAMA_ARG_CACHE_RAM="$cache_literal" generate_env 1 "$cache_literal_backfill" false
cache_line="$(grep '^LLAMA_ARG_CACHE_RAM=' "$cache_literal_backfill/.env")"
[[ "$cache_line" == "LLAMA_ARG_CACHE_RAM='$cache_literal'" ]] \
    || fail 'backfilled cache RAM literal was not dotenv-quoted'
"$BASH" -c "$cache_line"
[[ ! -e "$cache_marker" ]] || fail 'backfilled cache RAM text executed as shell code'
pass 'cache RAM values remain quoted literal data'
