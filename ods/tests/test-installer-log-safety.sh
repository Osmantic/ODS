#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TMP_DIR"' EXIT

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }

# Validator failures are copied into the installer log. No input line or
# invalid value may be repeated there, even if it is not a schema secret.
marker='ODS_FAKE_PRIVATE_TOKEN_7e3f5c'
printf '%s\n' \
    '{"properties":{"MODE":{"type":"string","enum":["safe"]},"COUNT":{"type":"integer"},"FLAG":{"type":"boolean"}}}' \
    >"$TMP_DIR/schema.json"
printf '%s\n' \
    "UNPARSEABLE_$marker" \
    "MODE=$marker" \
    "COUNT=$marker" \
    "FLAG=$marker" \
    >"$TMP_DIR/invalid.env"
if bash "$ROOT_DIR/scripts/validate-env.sh" "$TMP_DIR/invalid.env" "$TMP_DIR/schema.json" \
    >"$TMP_DIR/validator.out" 2>&1; then
    fail 'invalid environment passed validation'
else
    [[ $? -eq 2 ]] || fail 'validator failed for the wrong reason'
fi
if grep -Fq "$marker" "$TMP_DIR/validator.out"; then
    fail 'validator copied a raw private value into diagnostic output'
fi
grep -Fq 'Ignoring line 1' "$TMP_DIR/validator.out" || fail 'malformed line lost its line number'
grep -Fq 'MODE: invalid value' "$TMP_DIR/validator.out" || fail 'enum error lost its key'
grep -Fq 'COUNT: expected integer' "$TMP_DIR/validator.out" || fail 'type error lost its key'
pass 'validator errors retain keys and line numbers without raw values'

source "$ROOT_DIR/installers/lib/secure-log.sh"

LOG_FILE="$TMP_DIR/help.log" bash "$ROOT_DIR/install.sh" --help \
    >"$TMP_DIR/help.out" 2>&1 || fail '--help failed'
[[ ! -e "$TMP_DIR/help.log" ]] || fail '--help created an installer log'
if LOG_FILE="$TMP_DIR/unknown.log" bash "$ROOT_DIR/install.sh" --not-an-option \
    >"$TMP_DIR/unknown.out" 2>&1; then
    fail 'unknown option was accepted'
fi
[[ ! -e "$TMP_DIR/unknown.log" ]] || fail 'unknown option created an installer log'
pass 'help and option errors do not create diagnostic files'

( umask 0002; ods_prepare_install_log "$TMP_DIR/fresh.log" ) || fail 'could not create fresh log'
[[ "$(stat -c '%a' "$TMP_DIR/fresh.log")" == 600 ]] || fail 'fresh log is not private'
pass 'fresh log is 0600 despite caller umask 0002'

printf 'preserve this diagnostic\n' >"$TMP_DIR/existing.log"
chmod 0644 "$TMP_DIR/existing.log"
ods_prepare_install_log "$TMP_DIR/existing.log" || fail 'could not secure existing log'
[[ "$(stat -c '%a' "$TMP_DIR/existing.log")" == 600 ]] || fail 'existing log is not private'
[[ "$(cat "$TMP_DIR/existing.log")" == 'preserve this diagnostic' ]] || fail 'existing log was truncated'
pass 'existing owned regular log is privatized without truncation'

printf 'do not follow\n' >"$TMP_DIR/target.txt"
ln -s "$TMP_DIR/target.txt" "$TMP_DIR/link.log"
if ods_prepare_install_log "$TMP_DIR/link.log" 2>/dev/null; then
    fail 'symlink log was accepted'
fi
[[ "$(cat "$TMP_DIR/target.txt")" == 'do not follow' ]] || fail 'symlink target was changed'
pass 'symlink log is rejected'

mkdir "$TMP_DIR/unsafe-parent"
chmod 0777 "$TMP_DIR/unsafe-parent"
if ods_prepare_install_log "$TMP_DIR/unsafe-parent/new.log" 2>/dev/null; then
    fail 'non-sticky world-writable log directory was accepted'
fi
pass 'unsafe log parent is rejected'

ods_prepare_install_log /dev/null || fail '/dev/null diagnostic sink was rejected'
pass '/dev/null remains a supported explicit diagnostic sink'
