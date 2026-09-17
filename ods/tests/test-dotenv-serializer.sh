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
    ""
    "it's a model"
    'it'"'"'s $HOME $(touch pwned) `id` C:\path "dq"'
)

for value in "${values[@]}"; do
    quoted="$(dotenv_quote "$value")"
    expected="${value//$'\r'/ }"
    expected="${expected//$'\n'/ }"
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

printf 'Results: %d passed, %d failed\n' "$pass_count" "$fail_count"
[[ "$fail_count" -eq 0 ]]
