#!/usr/bin/env bash
# Regression coverage for preset names at the public ods CLI boundary.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-preset-name.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

pass_count=0

pass() {
    echo "PASS: $1"
    pass_count=$((pass_count + 1))
}

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

mkdir -p "$FIXTURE/lib" "$FIXTURE/extensions/services" "$FIXTURE/presets"
cp "$ROOT_DIR/ods-cli" "$FIXTURE/ods-cli"
cp "$ROOT_DIR"/lib/*.sh "$FIXTURE/lib/"
: > "$FIXTURE/docker-compose.base.yml"
printf 'GPU_BACKEND=cpu\n' > "$FIXTURE/.env"

run_cli() {
    local output_file="$1"
    shift

    set +e
    printf 'y\n' | ODS_HOME="$FIXTURE" bash "$FIXTURE/ods-cli" "$@" >"$output_file" 2>&1
    local status=$?
    set -e
    return "$status"
}

assert_rejected() {
    local description="$1"
    shift
    local output_file="$FIXTURE/output.log"

    if run_cli "$output_file" "$@"; then
        fail "$description was accepted"
    fi
    grep -qF "Invalid preset name" "$output_file" \
        || fail "$description did not explain the name constraint"
    pass "$description is rejected"
}

# Saving ../outside writes beside presets on the unguarded implementation.
assert_rejected "save traversal" preset save ../outside
[[ ! -e "$FIXTURE/outside" ]] || fail "save traversal wrote outside presets"
pass "save traversal cannot create an out-of-scope preset"

# Make an otherwise valid preset outside PRESETS_DIR so read actions would
# accept it if they only checked the joined path.
mkdir -p "$FIXTURE/outside-preset"
printf 'name=outside\n' > "$FIXTURE/outside-preset/meta.txt"
: > "$FIXTURE/outside-preset/extensions.list"
: > "$FIXTURE/outside-preset/env"

assert_rejected "load traversal" preset load ../outside-preset
assert_rejected "export traversal" preset export ../outside-preset "$FIXTURE/export.tar.gz"

mkdir -p "$FIXTURE/presets/safe"
printf 'name=safe\n' > "$FIXTURE/presets/safe/meta.txt"
: > "$FIXTURE/presets/safe/extensions.list"
: > "$FIXTURE/presets/safe/env"
assert_rejected "diff traversal" preset diff safe ../outside-preset

# Delete is the destructive case: ../data previously escaped PRESETS_DIR and
# recursively removed installation data after confirmation.
mkdir -p "$FIXTURE/data"
printf 'keep\n' > "$FIXTURE/data/sentinel"
assert_rejected "delete traversal" preset delete ../data
[[ -f "$FIXTURE/data/sentinel" ]] || fail "delete traversal removed installation data"
pass "delete traversal leaves installation data untouched"

assert_rejected "dot name" preset save .
assert_rejected "backslash-separated name" preset save 'group\preset'
assert_rejected "control-character name" preset save $'bad\nname'

# Spaces are valid within one path segment and remain supported.
if ! run_cli "$FIXTURE/valid.log" preset save "team demo"; then
    fail "valid single-segment name with spaces was rejected"
fi
[[ -f "$FIXTURE/presets/team demo/meta.txt" ]] \
    || fail "valid preset was not saved"
pass "valid single-segment names remain supported"

echo "Result: $pass_count passed"
