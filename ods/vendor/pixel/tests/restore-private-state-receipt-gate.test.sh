#!/usr/bin/env bash
# The snippets below are intentionally literal search patterns for the script under test.
# shellcheck disable=SC2016
set -euo pipefail
# Compatibility regression: the optional migration-receipt 3.2.2 -> 4.3.27 exact-version
# gate must never run for an ordinary no-receipt restore. The faithful legacy 0.8.0
# validate-only check in tests/e2e.sh:148-149 exercises the same behavior end-to-end;
# this test proves by construction that the gate is unreachable for the no-receipt
# validate/rehearse/general paths and that the receipt flow still rejects a non-3.2.2
# source before any live swap.
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT="$SOURCE/scripts/restore-private-state.sh"
HELPER="$SOURCE/scripts/restore-receipt.py"

fail() { echo "restore-private-state receipt-gate regression: $*" >&2; exit 1; }

first_line() { grep -Fn -m1 "$1" "$2" | cut -d: -f1; }

# --- Static ordering proof (no live state) ---
# Locate the structural markers by content.
validate_exit=$(first_line 'if [[ $mode == validate ]]; then' "$SCRIPT")
rehearse_exit=$(first_line 'if [[ $mode == rehearse ]]; then' "$SCRIPT")
receipt_guard=$(first_line 'if [[ -n "$receipt" ]]; then' "$SCRIPT")
version_check=$(first_line '[[ "$source_pixel" == "3.2.2" ]]' "$SCRIPT")
first_swap=$(first_line 'pixel_systemctl stop' "$SCRIPT")
for marker in validate_exit rehearse_exit receipt_guard version_check first_swap; do
  [[ ${!marker} -gt 0 ]] || fail "could not locate structural marker $marker"
done
# The exact-version gate must run only for the receipt path and only after the
# no-receipt validate and rehearse exits, and must remain before any live swap.
[[ "$version_check" -gt "$validate_exit" ]] || fail "version gate runs before the validate exit"
[[ "$version_check" -gt "$rehearse_exit" ]] || fail "version gate runs before the rehearse exit"
[[ "$version_check" -lt "$first_swap" ]] || fail "version gate runs after the live swap"
# The gate must be nested inside the optional-receipt guard (the last receipt guard that
# precedes the version check), so a no-receipt validate/rehearse/general restore skips it.
[[ "$receipt_guard" -gt "$rehearse_exit" && "$receipt_guard" -lt "$version_check" ]] \
  || fail "version gate is not guarded by the optional receipt after the no-receipt exits"

# --- Command proof: receipt flow rejects a non-3.2.2 source before live swap ---
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
repo="$tmp/repo"; mkdir -p "$repo"
parent="$tmp/out"; mkdir -p "$parent"; chmod 700 "$parent"
receipt="$parent/receipt.json"
backup_sha=$(printf 'a%.0s' {1..64})
python3 "$HELPER" reserve "$receipt" "$repo" >/dev/null
grep -F '"status":"reserved"' "$receipt" >/dev/null || fail "reserve did not create a reservation"
# A legacy 0.8.0 source must be rejected by the receipt flow (source_pixel != 3.2.2),
# leaving the reservation intact rather than a durable pass receipt.
if python3 "$HELPER" finalize "$receipt" "$backup_sha" "0.8.0" "4.3.27" "$repo" "0" >/dev/null 2>&1; then
  fail "receipt finalize accepted a non-3.2.2 source"
fi
grep -F '"status":"reserved"' "$receipt" >/dev/null \
  || fail "rejected receipt flow did not leave the reservation intact before live swap"

echo "restore-private-state receipt-gate regression passed"
