#!/usr/bin/env bash
# test-env-set-literal-key.sh
#
# The .env upsert helpers decide "does this key exist?" with a regex
# (`grep "^${key}="`) but update with a literal match (awk index() /
# sed). A key containing regex metacharacters can therefore match a
# *different* existing line at probe time and then update nothing —
# silently dropping the new key. The probe must use the same literal
# prefix test as the update.
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
    "ods-cli _env_set|ods-cli|_env_set|install-dir"
    "mode-switch.sh env_set|scripts/mode-switch.sh|env_set|env-file"
)

run_writer() {  # $1 function source, $2 function name, $3 style, $4 env file, $5 key, $6 value
    local definition="$1" name="$2" style="$3" env_file="$4" key="$5" value="$6"
    (
        eval "$definition"
        # shellcheck disable=SC2034 # INSTALL_DIR/ENV_FILE are read by the eval'd function
        case "$style" in
            install-dir) INSTALL_DIR="$(dirname "$env_file")"; "$name" "$key" "$value" ;;
            env-file) ENV_FILE="$env_file"; "$name" "$key" "$value" ;;
        esac
    )
}

for entry in "${writers[@]}"; do
    IFS='|' read -r label file name style <<< "$entry"
    definition="$(extract_function "$name" "$ROOT_DIR/$file")"
    case_dir="$TMP_DIR/${name}"
    mkdir -p "$case_dir"

    # A key containing a regex metacharacter (".") must not match the
    # similar-looking FOO_BAR= line — the new key has to be appended.
    printf 'FOO_BAR=keep-me\n' > "$case_dir/.env"
    run_writer "$definition" "$name" "$style" "$case_dir/.env" 'FOO.BAR' 'new-value'
    if grep -qxF 'FOO.BAR=new-value' "$case_dir/.env" \
        && grep -qxF 'FOO_BAR=keep-me' "$case_dir/.env"; then
        pass "$label appends a metacharacter key instead of matching FOO_BAR"
    else
        fail "$label lost a metacharacter key: $(tr '\n' '|' < "$case_dir/.env")"
    fi

    # A metacharacter key that already exists must be updated in place,
    # not appended a second time.
    printf 'FOO.BAR=old\nFOO_BAR=keep-me\n' > "$case_dir/.env"
    run_writer "$definition" "$name" "$style" "$case_dir/.env" 'FOO.BAR' 'updated'
    if [[ "$(grep -cxF 'FOO.BAR=updated' "$case_dir/.env")" == "1" ]] \
        && ! grep -qF 'FOO.BAR=old' "$case_dir/.env" \
        && grep -qxF 'FOO_BAR=keep-me' "$case_dir/.env"; then
        pass "$label updates an existing metacharacter key in place"
    else
        fail "$label mangled an existing metacharacter key: $(tr '\n' '|' < "$case_dir/.env")"
    fi

    # Ordinary keys keep working unchanged.
    printf 'PLAIN=1\n' > "$case_dir/.env"
    run_writer "$definition" "$name" "$style" "$case_dir/.env" 'PLAIN' '2'
    if [[ "$(cat "$case_dir/.env")" == "PLAIN=2" ]]; then
        pass "$label still updates a plain key"
    else
        fail "$label broke a plain key update: $(tr '\n' '|' < "$case_dir/.env")"
    fi
done

printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
