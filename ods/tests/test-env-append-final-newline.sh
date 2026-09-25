#!/usr/bin/env bash
# Editors such as VS Code save .env without a final newline by default. The
# .env upsert helpers append a missing key with `>>`, which then joined the new
# assignment onto the owner's last line: `API_KEY=secret` became
# `API_KEY=secretNEW_KEY=value`, corrupting the owner's value and dropping the
# new key. The macOS CLI's reader also skipped such a last line entirely.
# Run each shipped helper against files with and without a final newline.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf '[PASS] %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '[FAIL] %s\n' "$1" >&2; }

extract_function() {
    local name="$1" file="$2" body
    body="$(awk -v name="$name" '
        $0 ~ "^" name "\\(\\) *\\{" { emit = 1 }
        emit { print }
        emit && $0 == "}" { exit }
    ' "$file")"
    [[ -n "$body" ]] || { echo "[FAIL] could not extract $name from $file" >&2; exit 1; }
    printf '%s\n' "$body"
}

# writer label | source file | function | how the function receives the file
writers=(
    "macOS installer upsert_env_value|installers/macos/lib/env-generator.sh|upsert_env_value|arg"
    "macOS CLI upsert_env_value|installers/macos/ods-macos.sh|upsert_env_value|arg"
    "ods-cli _env_set|ods-cli|_env_set|install-dir"
    "mode-switch.sh env_set|scripts/mode-switch.sh|env_set|env-file"
)

run_writer() {  # $1 function source, $2 function name, $3 style, $4 env file
    local definition="$1" name="$2" style="$3" env_file="$4"
    (
        eval "$definition"
        case "$style" in
            arg) "$name" "$env_file" NEW_KEY new-value ;;
            install-dir) INSTALL_DIR="$(dirname "$env_file")"; "$name" NEW_KEY new-value ;;
            env-file) ENV_FILE="$env_file"; "$name" NEW_KEY new-value ;;
        esac
    )
}

for entry in "${writers[@]}"; do
    IFS='|' read -r label file name style <<< "$entry"
    definition="$(extract_function "$name" "$ROOT_DIR/$file")"
    case_dir="$TMP_DIR/${name}-${style}"
    mkdir -p "$case_dir"

    printf 'FIRST=1\nAPI_KEY=owner-secret' > "$case_dir/.env"
    run_writer "$definition" "$name" "$style" "$case_dir/.env"
    expected=$'FIRST=1\nAPI_KEY=owner-secret\nNEW_KEY=new-value\n'
    [[ "$(cat "$case_dir/.env"; printf x)" == "${expected}x" ]] \
        && pass "$label keeps an unterminated last line and adds the new key on its own line" \
        || fail "$label corrupted an unterminated last line: $(tr '\n' '|' < "$case_dir/.env")"

    printf 'FIRST=1\n' > "$case_dir/.env"
    run_writer "$definition" "$name" "$style" "$case_dir/.env"
    [[ "$(cat "$case_dir/.env"; printf x)" == $'FIRST=1\nNEW_KEY=new-value\nx' ]] \
        && pass "$label adds no blank line to a newline-terminated file" \
        || fail "$label changed a newline-terminated file unexpectedly: $(tr '\n' '|' < "$case_dir/.env")"

    : > "$case_dir/.env"
    run_writer "$definition" "$name" "$style" "$case_dir/.env"
    [[ "$(cat "$case_dir/.env"; printf x)" == $'NEW_KEY=new-value\nx' ]] \
        && pass "$label writes an empty file without a leading blank line" \
        || fail "$label wrote an empty file unexpectedly: $(tr '\n' '|' < "$case_dir/.env")"
done

reader="$(extract_function read_ods_env "$ROOT_DIR/installers/macos/ods-macos.sh")"
mkdir -p "$TMP_DIR/reader"
printf 'FIRST=1\nAPI_KEY=owner-secret' > "$TMP_DIR/reader/.env"
value="$(
    eval "$reader"
    INSTALL_DIR="$TMP_DIR/reader"
    read_ods_env
    printf '%s' "${ENV_API_KEY-missing}"
)"
[[ "$value" == "owner-secret" ]] \
    && pass "macOS CLI read_ods_env reads an unterminated last line" \
    || fail "macOS CLI read_ods_env dropped the last line (got $value)"

printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
