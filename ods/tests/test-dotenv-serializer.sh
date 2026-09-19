#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT_DIR/lib/dotenv-quote.sh"
. "$ROOT_DIR/lib/safe-env.sh"

pass_count=0
fail_count=0
pass() { pass_count=$((pass_count + 1)); }
fail() { printf 'FAIL: %s\n' "$1" >&2; fail_count=$((fail_count + 1)); }

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

values=(
    "Arch-aware catalog policy (spark-aarch64): selected after fit check"
    "deepseek-r1:32768:48;qwen-a3b:131072:35.48"
    'contains "literal double quotes"'
    'cost is $HOME and ${UNSET_VAR} and $((1+1))'
    'command $(touch pwned) substitution'
    'backtick `id` here'
    'C:\Users\dev\ods\models'
    $'carriage\rreturn'
    $'multi\nline'
    $'form\ffeed'
    $'vertical\vtab'
    ""
    "it's a model"
    'it'"'"'s $HOME $(touch pwned) `id` C:\path "dq"'
)

for value in "${values[@]}"; do
    quoted="$(dotenv_quote "$value")"
    expected="${value//$'\r'/ }"
    expected="${expected//$'\n'/ }"
    expected="${expected//$'\f'/ }"
    expected="${expected//$'\v'/ }"
    if [[ "$expected" == *"'"* ]]; then
        expected="${expected//\`/ˋ}"
    fi
    printf 'TESTVAR=%s\n' "$quoted" > "$tmp_dir/.env"

    unset TESTVAR
    actual="$(set -a; source "$tmp_dir/.env"; set +a; printf '%s' "${TESTVAR-}")"
    [[ "$actual" == "$expected" ]] \
        && pass \
        || fail "Bash source did not round-trip [$value]"

    unset TESTVAR
    load_env_file "$tmp_dir/.env"
    [[ "${TESTVAR-}" == "$expected" ]] \
        && pass \
        || fail "safe-env did not round-trip [$value]"
done

[[ ! -e "$tmp_dir/pwned" ]] \
    && pass \
    || fail "serialized command substitution executed"
[[ "$(dotenv_quote 'a;b $HOME')" == "$(dotenv_quote 'a;b $HOME')" ]] \
    && pass \
    || fail "serialization is not deterministic"

# Compose has its own dotenv reader. Parsing every adversarial value proves the
# generated file remains valid there without requiring an image pull in CI.
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    : > "$tmp_dir/compose.env"
    {
        printf '%s\n' 'services:' '  probe:' '    image: busybox' '    environment:'
        index=0
        for value in "${values[@]}"; do
            index=$((index + 1))
            printf 'CASE%d=%s\n' "$index" "$(dotenv_quote "$value")" >> "$tmp_dir/compose.env"
            printf '      CASE%d: ${CASE%d}\n' "$index" "$index"
        done
    } > "$tmp_dir/compose.yaml"
    docker compose --env-file "$tmp_dir/compose.env" -f "$tmp_dir/compose.yaml" config >/dev/null 2>&1 \
        && pass \
        || fail "Docker Compose rejected serialized values"
fi

# dotenv_value keeps a value bare whenever Docker Compose already reads the bare
# text literally, so existing .env lines and simple grep/cut readers keep their
# exact bytes, and quotes only what Compose would otherwise change.
# shellcheck disable=SC2088  # a literal, unexpanded tilde is one of the values under test
bare_values=(
    "4f3c2a1b0e9d8c7b6a5f4e3d2c1b0a99"
    "sk-ods-0123456789abcdef"
    "Zm9vYmFyYmF6+/w=="
    "http://llama-server:8080/v1"
    "http://lan-host:8000/v1?model=a&stream=1"
    "admin@ods.local"
    "/home/dev/ods/data"
    "BAAI/bge-base-en-v1.5"
    "sk#no-space-before-hash"
    "it's"
    'C:\Users\dev'
    'inner "double" quotes'
    'backtick `id` here'
    '~/relative'
    'a;b&c|d'
    '-n'
    ''
)
compose_changed_values=(
    'my$ecret#1 now'
    'Xk9${pQ#r2Lm'
    "it's\$HOME"
    'cost is $HOME and ${UNSET_VAR}'
    ' padded '
    'Lab #2'
    "'leading single quote"
    '"leading double quote'
    'tick`tock$$'
    'back\slash$"dq'"'"'sq'
)
for value in "${bare_values[@]}"; do
    [[ "$(dotenv_value "$value")" == "$value" ]] \
        && pass \
        || fail "dotenv_value quoted a value Compose already reads literally [$value]"
done
for value in "${compose_changed_values[@]}"; do
    [[ "$(dotenv_value "$value")" != "$value" ]] \
        && pass \
        || fail "dotenv_value left bare a value Compose would change [$value]"
done
for value in "${bare_values[@]}" "${compose_changed_values[@]}"; do
    printf 'TESTVAR=%s\n' "$(dotenv_value "$value")" > "$tmp_dir/.env"
    unset TESTVAR
    load_env_file "$tmp_dir/.env"
    [[ "${TESTVAR-}" == "$value" ]] \
        && pass \
        || fail "safe-env did not round-trip dotenv_value [$value]"
done
[[ "$(dotenv_value $'two\nlines')" == "two lines" ]] \
    && pass \
    || fail "dotenv_value did not normalize a newline"

# The dashboard reads the same file with its own parser.
if command -v python3 >/dev/null 2>&1; then
    : > "$tmp_dir/parity.bin"
    for value in "${bare_values[@]}" "${compose_changed_values[@]}"; do
        printf '%s\0%s\0' "$value" "$(dotenv_value "$value")" >> "$tmp_dir/parity.bin"
    done
    if python3 - "$ROOT_DIR/extensions/services/dashboard-api" "$tmp_dir/parity.bin" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from env_values import parse_env_value

items = open(sys.argv[2], encoding="utf-8").read().split("\0")[:-1]
for literal, serialized in zip(items[0::2], items[1::2]):
    decoded = parse_env_value(serialized)
    if decoded != literal:
        sys.exit(f"dashboard reader decoded {serialized!r} as {decoded!r}, not {literal!r}")
PY
    then
        pass
    else
        fail "the dashboard .env reader does not decode dotenv_value output literally"
    fi
fi

# safe_env_decode_value reads one raw value exactly as load_env_file does,
# including the dashboard's single-quoted form, the double-quoted escape set,
# inline comments and a CRLF line ending.
raw_values=(
    "'my\$ecret#1 now'"
    '"it'"'"'s\$HOME"'
    '"back\\slash\"dq'"'"'sq"'
    'plain # inline note'
    "'quoted' # note"
    '  spaced  '
    'a#b'
    $'crlf\r'
    ''
    '"'"'"'"'
)
decoded_values=(
    'my$ecret#1 now'
    "it's\$HOME"
    'back\slash"dq'"'"'sq'
    'plain'
    'quoted'
    'spaced'
    'a#b'
    'crlf'
    ''
    "'"
)
for index in "${!raw_values[@]}"; do
    raw="${raw_values[$index]}"
    [[ "$(safe_env_decode_value "$raw")" == "${decoded_values[$index]}" ]] \
        && pass \
        || fail "safe_env_decode_value misread [$raw]"
    printf 'TESTVAR=%s\n' "$raw" > "$tmp_dir/.env"
    unset TESTVAR
    load_env_file "$tmp_dir/.env"
    [[ "${TESTVAR-}" == "$(safe_env_decode_value "$raw")" ]] \
        && pass \
        || fail "safe_env_decode_value and load_env_file disagree on [$raw]"
done

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 \
    && command -v python3 >/dev/null 2>&1; then
    : > "$tmp_dir/value.env"
    : > "$tmp_dir/expected.bin"
    {
        printf '%s\n' 'services:' '  probe:' '    image: busybox' '    environment:'
        index=0
        for value in "${bare_values[@]}" "${compose_changed_values[@]}"; do
            index=$((index + 1))
            printf 'VALUE%d=%s\n' "$index" "$(dotenv_value "$value")" >> "$tmp_dir/value.env"
            printf '%s\0' "$value" >> "$tmp_dir/expected.bin"
            printf '      VALUE%d: ${VALUE%d}\n' "$index" "$index"
        done
    } > "$tmp_dir/value-compose.yaml"
    if env -i PATH="$PATH" HOME="$HOME" docker compose --env-file "$tmp_dir/value.env" \
        -f "$tmp_dir/value-compose.yaml" config --format json > "$tmp_dir/value-compose.json" 2>/dev/null \
        && python3 - "$tmp_dir/value-compose.json" "$tmp_dir/expected.bin" <<'PY'
import json
import sys

environment = json.load(open(sys.argv[1], encoding="utf-8"))["services"]["probe"]["environment"]
expected = open(sys.argv[2], encoding="utf-8").read().split("\0")[:-1]
for index, value in enumerate(expected, start=1):
    # `config` re-escapes a literal "$" as "$$" in its rendered output.
    observed = environment[f"VALUE{index}"].replace("$$", "$")
    if observed != value:
        sys.exit(f"VALUE{index}: expected {value!r}, Compose resolved {observed!r}")
PY
    then
        pass
    else
        fail "Docker Compose did not resolve dotenv_value output literally"
    fi
fi

printf 'Results: %d passed, %d failed\n' "$pass_count" "$fail_count"
[[ "$fail_count" -eq 0 ]]
