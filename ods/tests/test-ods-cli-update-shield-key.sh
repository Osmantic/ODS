#!/usr/bin/env bash
# Regression: `ods update` backfills SHIELD_API_KEY without ever writing an
# empty key or clobbering an existing one.
#
# The update path generates the key via `openssl rand -hex 32` with a
# /dev/urandom fallback. Previously the fallback piped through `xxd`, which is
# not part of a minimal install: when both generators were unavailable the
# command substitution produced an empty string, `_env_set` wrote
# `SHIELD_API_KEY=`, and the update reported success while the dashboard
# Privacy Shield /stats proxy stayed unauthenticated. A duplicate `.env` entry
# could also clobber a real key: `_env_get_raw` reads only the last matching
# line, so `SHIELD_API_KEY=<real>` followed by `SHIELD_API_KEY=` triggered a
# regeneration that rewrote every line.
#
# Usage: ./tests/test-ods-cli-update-shield-key.sh

set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    for modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$modern_bash" ]]; then
            exec "$modern_bash" "$0" "$@"
        fi
    done
    printf '[SKIP] ods-cli requires Bash 4+\n'
    exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root_dir="$(cd "$script_dir/.." && pwd)"
ods_cli="$root_dir/ods-cli"

PASSED=0
FAILED=0
pass() { printf '  [PASS] %s\n' "$1"; PASSED=$((PASSED + 1)); }
fail() { printf '  [FAIL] %s\n' "$1"; FAILED=$((FAILED + 1)); }

# run_update <env_content> <missing_tools> <failing_tools>
# Builds a fake install and runs `ods update --force` with a synthetic PATH:
# tools in <missing_tools> are genuinely absent (clean-room symlink dir), and
# tools in <failing_tools> are stubs that exit 1. Sets RC, OUT, ENV_AFTER.
run_update() {
    local env_content="$1" missing_tools="$2" failing_tools="$3"
    local tmp_dir install_dir bin_dir clean_dir
    tmp_dir="$(mktemp -d)"
    install_dir="$tmp_dir/install"
    bin_dir="$tmp_dir/bin"
    clean_dir="$tmp_dir/clean"
    mkdir -p "$install_dir/data" "$bin_dir" "$clean_dir" "$install_dir/installers/lib"
    cp "$root_dir/docker-compose.base.yml" "$install_dir/docker-compose.base.yml"
    cp "$root_dir/manifest.json" "$install_dir/manifest.json"
    cp "$root_dir/installers/lib/compose-images.sh" "$install_dir/installers/lib/compose-images.sh"
    printf '%s\n' '-f docker-compose.base.yml' > "$install_dir/.compose-flags"
    printf '%s\n' "$env_content" > "$install_dir/.env"

    cat > "$install_dir/ods-update.sh" <<'UPDATE'
#!/usr/bin/env bash
exit 0
UPDATE
    chmod +x "$install_dir/ods-update.sh"

    cat > "$bin_dir/docker" <<'DOCKER'
#!/usr/bin/env bash
if [[ "${1:-}" == "info" ]]; then
    [[ "$*" == *NCPU* ]] && printf '16\n'
    exit 0
fi
if [[ "${1:-}" == "compose" ]]; then
    shift
    joined=" $* "
    if [[ "$joined" == *" config --format json "* ]]; then
        printf '%s\n' '{"services":{"dashboard":{"image":"ods-dashboard:local","build":{"context":"./extensions/services/dashboard"}}}}'
        exit 0
    fi
    if [[ "$joined" == *" config --services "* ]]; then
        printf '%s\n' dashboard
        exit 0
    fi
    if [[ "$joined" == *" --services "* && "$joined" == *" --status running "* ]]; then
        printf '%s\n' dashboard
        exit 0
    fi
    if [[ "$joined" == *" ps "* ]]; then
        exit 0
    fi
    exit 0
fi
if [[ "${1:-}" == "ps" || "${1:-}" == "pull" ]]; then
    exit 0
fi
exit 0
DOCKER

    cat > "$bin_dir/sleep" <<'SLEEP'
#!/usr/bin/env bash
exit 0
SLEEP

    # A clean-room PATH: symlink every real tool except the ones the case
    # marks absent, so `command -v` (not a failing stub) drives the fallback.
    local d c b
    for d in /usr/bin /bin /usr/local/bin; do
        [[ -d "$d" ]] || continue
        for c in "$d"/*; do
            b="$(basename "$c")"
            case " $missing_tools $failing_tools " in
                *" $b "*) continue ;;
            esac
            [[ -e "$clean_dir/$b" ]] || ln -s "$c" "$clean_dir/$b" 2>/dev/null || true
        done
    done
    for b in $failing_tools; do
        printf '#!/usr/bin/env bash\nexit 1\n' > "$bin_dir/$b"
    done
    chmod +x "$bin_dir"/*

    set +e
    PATH="$bin_dir:$clean_dir" \
    ODS_HOME="$install_dir" \
    NO_COLOR=1 \
    ODS_COMPOSE_PULL_RETRY_DELAY_1=0 \
    ODS_COMPOSE_PULL_RETRY_DELAY_2=0 \
    ODS_COMPOSE_PULL_RETRY_DELAY_N=0 \
        "$BASH" "$ods_cli" update --force > "$tmp_dir/update.out" 2>&1
    RC=$?
    set -e
    OUT="$(cat "$tmp_dir/update.out")"
    ENV_AFTER="$(cat "$install_dir/.env")"
    rm -rf "$tmp_dir"
}

# HERMES_DASHBOARD_SESSION_TOKEN is preset so the unrelated token backfill in
# get_compose_flags stays inert even when the openssl stub is poisoned.
BASE_ENV='ODS_VERSION=2.6.0
ODS_MODE=local
GPU_BACKEND=cpu
GPU_COUNT=1
TIER=1
LLAMA_CPU_LIMIT=8.0
LLAMA_CPU_RESERVATION=2.0
HERMES_DASHBOARD_SESSION_TOKEN=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'

# 1. Missing key: generated and written as a 64-hex value.
run_update "$BASE_ENV" "" ""
if [[ $RC -eq 0 ]] && printf '%s\n' "$ENV_AFTER" | grep -qE '^SHIELD_API_KEY=[0-9a-f]{64}$'; then
    pass "missing SHIELD_API_KEY is backfilled with a 64-hex key"
else
    fail "missing key should be backfilled (rc=$RC): $(printf '%s' "$OUT" | tail -3)"
fi

# 2. openssl and xxd absent (minimal install): the od fallback yields a key.
run_update "$BASE_ENV" "openssl xxd" ""
if [[ $RC -eq 0 ]] && printf '%s\n' "$ENV_AFTER" | grep -qE '^SHIELD_API_KEY=[0-9a-f]{64}$'; then
    pass "missing openssl falls back to od/urandom and still writes a real key"
else
    fail "od fallback should produce a key (rc=$RC): $(printf '%s' "$OUT" | tail -3)"
fi

# 3. No usable random source: update must abort and never write an empty key.
run_update "$BASE_ENV" "openssl xxd" "od"
if [[ $RC -ne 0 ]] && ! printf '%s\n' "$ENV_AFTER" | grep -q '^SHIELD_API_KEY='; then
    pass "no random source aborts the update without writing an empty key"
else
    fail "update should abort and leave .env untouched (rc=$RC, env: $(printf '%s' "$ENV_AFTER" | grep SHIELD || echo 'no key line'))"
fi

# 4. An empty trailing duplicate must not clobber an earlier real key.
REAL_KEY="$(printf 'a%.0s' $(seq 64))"
DUP_ENV="$BASE_ENV
SHIELD_API_KEY=$REAL_KEY
SHIELD_API_KEY="
run_update "$DUP_ENV" "" ""
if [[ $RC -eq 0 ]] && printf '%s\n' "$ENV_AFTER" | grep -q "^SHIELD_API_KEY=$REAL_KEY$"; then
    pass "empty trailing duplicate does not clobber the real key"
else
    fail "real key must survive an empty duplicate (rc=$RC, env: $(printf '%s' "$ENV_AFTER" | grep SHIELD))"
fi

# 5. Existing real key is preserved untouched.
SET_ENV="$BASE_ENV
SHIELD_API_KEY=$REAL_KEY"
run_update "$SET_ENV" "openssl xxd" ""
if [[ $RC -eq 0 ]] && [[ "$(printf '%s\n' "$ENV_AFTER" | grep -c "^SHIELD_API_KEY=$REAL_KEY$")" == "1" ]]; then
    pass "existing SHIELD_API_KEY is preserved"
else
    fail "existing key must be preserved (rc=$RC)"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
