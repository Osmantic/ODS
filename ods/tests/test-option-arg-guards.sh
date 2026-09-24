#!/usr/bin/env bash
# test-option-arg-guards.sh — Options that take a value must reject a missing
# argument with a clean error instead of crashing on "$2: unbound variable"
# under `set -u`.
#
# Covers every user-facing parser that consumed "$2" unguarded:
#   install-core.sh   --tier --lemonade-url --lemonade-api-key
#                     --external-llm-url --external-llm-provider
#                     --external-llm-model --summary-json
#   ods-backup.sh     -o/--output -t/--type -d/--delete --description
#   scripts/pre-download.sh  --tier
#   scripts/ods-test.sh      --service/-s

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="$(dirname "$SCRIPT_DIR")"

PASS=0
FAIL=0

check() {
    local label="$1"
    shift
    local out rc
    out="$(timeout 30 "$@" 2>&1)"
    rc=$?
    if [[ "$out" == *"unbound variable"* ]]; then
        echo "[FAIL] $label — crashed on unbound \$2"
        ((FAIL++))
    elif [[ $rc -eq 0 ]]; then
        echo "[FAIL] $label — exited 0 despite missing option value"
        ((FAIL++))
    elif [[ "$out" != *"requires"* ]]; then
        echo "[FAIL] $label — no 'requires' error message (rc=$rc)"
        ((FAIL++))
    else
        echo "[PASS] $label"
        ((PASS++))
    fi
}

echo "== Trailing value-option must produce a clean error, not a set -u crash =="

for opt in --tier --lemonade-url --lemonade-api-key --external-llm-url \
           --external-llm-provider --external-llm-model --summary-json; do
    check "install-core.sh $opt" bash "$ODS_DIR/install-core.sh" "$opt"
done

for opt in --output -o --type -t --delete -d --description; do
    check "ods-backup.sh $opt" bash "$ODS_DIR/ods-backup.sh" "$opt"
done

check "pre-download.sh --tier"    bash "$ODS_DIR/scripts/pre-download.sh" --tier
check "ods-test.sh --service"     bash "$ODS_DIR/scripts/ods-test.sh" --service
check "ods-test.sh -s"            bash "$ODS_DIR/scripts/ods-test.sh" -s

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
