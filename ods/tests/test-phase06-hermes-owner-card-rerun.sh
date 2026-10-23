#!/usr/bin/env bash
# A Linux installer rerun regenerates .env from phase 06's template. An owner
# who opted in to Hermes owner-card gating (HERMES_REQUIRE_OWNER_CARD=true)
# must keep it: the rerun must not reopen Hermes without a signed session.
# Renders phase 06's shipped reader, producer and template line.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/06-directories.sh"
# shellcheck source=../lib/safe-env.sh
. "$ROOT_DIR/lib/safe-env.sh"
# shellcheck source=../lib/dotenv-quote.sh
. "$ROOT_DIR/lib/dotenv-quote.sh"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf '[PASS] %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '[FAIL] %s\n' "$1" >&2; }

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

extract_nested_function() {
    awk -v name="$1" '
        $0 == "    " name "() {" { emit = 1 }
        emit { print }
        emit && $0 == "    }" { exit }
    ' "$PHASE"
}
for reader in _env_get _env_get_explicit_first; do
    definition="$(extract_nested_function "$reader")"
    [[ -n "$definition" ]] || { fail "could not extract $reader from phase 06"; exit 1; }
    eval "$definition"
done

producer="$(grep -E '^    HERMES_REQUIRE_OWNER_CARD_VALUE=' "$PHASE" || true)"
template="$(awk '
    /cat > "\$INSTALL_DIR\/.env" << ENV_EOF/ { body = 1; next }
    body && $0 == "ENV_EOF" { exit }
    body && /^HERMES_REQUIRE_OWNER_CARD=/ { print }
' "$PHASE")"
[[ -n "$template" ]] || { fail "phase 06 no longer writes HERMES_REQUIRE_OWNER_CARD"; exit 1; }

# Usage: render SAVED_ENV_CONTENT [EXPLICIT_VALUE]
render() {
    printf '%s' "$1" > "$tmp_dir/existing.env"
    (
        unset HERMES_REQUIRE_OWNER_CARD HERMES_REQUIRE_OWNER_CARD_VALUE
        [[ $# -lt 2 ]] || export HERMES_REQUIRE_OWNER_CARD="$2"
        _env_existing="$tmp_dir/existing.env"
        eval "$producer"
        eval "printf '%s\n' \"$template\""
    )
}

[[ "$(render $'HERMES_REQUIRE_OWNER_CARD=true\n')" == "HERMES_REQUIRE_OWNER_CARD=true" ]] \
    && pass "rerun keeps an owner's HERMES_REQUIRE_OWNER_CARD=true" \
    || fail "rerun reset HERMES_REQUIRE_OWNER_CARD=true: $(render $'HERMES_REQUIRE_OWNER_CARD=true\n')"

[[ "$(render $'HERMES_REQUIRE_OWNER_CARD="true" # gate Hermes\n')" == "HERMES_REQUIRE_OWNER_CARD=true" ]] \
    && pass "rerun decodes a quoted, commented owner value" \
    || fail "rerun did not decode the quoted owner value"

[[ "$(render $'HERMES_REQUIRE_OWNER_CARD=false\n')" == "HERMES_REQUIRE_OWNER_CARD=false" ]] \
    && pass "rerun keeps an explicit false" \
    || fail "rerun changed an explicit false"

[[ "$(render '')" == "HERMES_REQUIRE_OWNER_CARD=false" ]] \
    && pass "fresh install defaults owner-card gating off" \
    || fail "fresh install default changed: $(render '')"

[[ "$(render $'HERMES_REQUIRE_OWNER_CARD=false\n' true)" == "HERMES_REQUIRE_OWNER_CARD=true" ]] \
    && pass "an explicit installer environment value wins over the saved one" \
    || fail "explicit HERMES_REQUIRE_OWNER_CARD did not win"

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
