#!/usr/bin/env bash
# migrate-config.sh must never feed a JSON blob to compare_versions.
#
# get_current_version() used `jq -e '.version'` as an existence probe and fell
# back to `cat` on any failure — including the routine case where .version is a
# JSON record with no `version` member (ods-update.sh's `check` command writes
# exactly that: {"last_check": ...}). The raw JSON text then became the
# "current version": it rendered garbage in output and crashed [[ -gt ]]
# inside compare_versions, which made `check`/`migrate` report "No migration
# needed" while pending migrations went unseen.
#
# Run from repo root:  bash ods/tests/test-migrate-config-version-file.sh
# Or from ods:         bash tests/test-migrate-config-version-file.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/migrate-config.sh"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || fail "jq is required (migrate-config.sh prerequisite)"

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-migrate-version.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT
mkdir -p "$FIXTURE/install" "$FIXTURE/data" "$FIXTURE/scripts"
# migrate-config resolves migrations at SCRIPT_DIR/../migrations; keep the
# script in a fixture scripts/ dir so INSTALL_DIR stays the fixture.
mkdir -p "$FIXTURE/scripts-src"
cp "$SCRIPT" "$FIXTURE/scripts-src/migrate-config.sh"

run_check() {
    INSTALL_DIR="$FIXTURE/install" DATA_DIR="$FIXTURE/data" \
        bash "$FIXTURE/scripts-src/migrate-config.sh" check 2>&1
}

echo "Test 1: JSON .version without a version member -> 0.0.0, no garbage, no syntax errors"
printf '{"last_check": "2026-09-07T18:53:25Z"}\n' > "$FIXTURE/install/.version"
set +e
out="$(run_check)"
rc=$?
set -e
[[ "$rc" -eq 0 || "$rc" -eq 2 ]] || { echo "$out"; fail "check failed (rc=$rc)"; }
grep -q "Current version: 0.0.0" <<<"$out" \
    || { echo "$out"; fail "version-less JSON .version must normalize to 0.0.0"; }
if grep -q "syntax error\|last_check" <<<"$out"; then
    echo "$out"
    fail "raw JSON leaked into version reporting/comparison"
fi
pass "JSON record without .version is normalized"

echo "Test 2: JSON .version with a version member is honored"
printf '{"version": "2.6.0", "last_update": "2026-09-08T00:00:00Z"}\n' > "$FIXTURE/install/.version"
set +e
out="$(run_check)"
rc=$?
set -e
[[ "$rc" -eq 0 || "$rc" -eq 2 ]] || { echo "$out"; fail "check failed (rc=$rc)"; }
grep -q "Current version: 2.6.0" <<<"$out" \
    || { echo "$out"; fail "JSON .version member not read"; }
pass "JSON .version member is read"

echo "Test 3: plain-text .version still works (legacy layout)"
printf '2.5.9\n' > "$FIXTURE/install/.version"
set +e
out="$(run_check)"
rc=$?
set -e
[[ "$rc" -eq 0 || "$rc" -eq 2 ]] || { echo "$out"; fail "check failed (rc=$rc)"; }
grep -q "Current version: 2.5.9" <<<"$out" \
    || { echo "$out"; fail "plain-text .version regressed"; }
pass "plain-text .version still parsed"

echo "Test 4: corrupt .version normalizes instead of poisoning arithmetic"
printf 'not-a-version{\n' > "$FIXTURE/install/.version"
set +e
out="$(run_check)"
rc=$?
set -e
[[ "$rc" -eq 0 || "$rc" -eq 2 ]] || { echo "$out"; fail "check failed (rc=$rc)"; }
grep -q "Current version: 0.0.0" <<<"$out" \
    || { echo "$out"; fail "corrupt .version must normalize to 0.0.0"; }
if grep -q "syntax error" <<<"$out"; then
    echo "$out"
    fail "corrupt .version still reaches [[ -gt ]]"
fi
pass "corrupt .version normalized to 0.0.0"

echo "Test 5: pending migrations are detected after normalization"
# .version JSON without a version -> 0.0.0 vs last_migrated 0.0.0 -> "no migration"
# is correct; a real pending case: state older than current.
printf '{"version": "2.6.0"}\n' > "$FIXTURE/install/.version"
printf '0.0.0\n' > "$FIXTURE/data/.migration-state"
set +e
out="$(run_check)"
rc=$?
set -e
[[ "$rc" -eq 2 ]] || { echo "$out"; fail "check should signal pending migration (rc=2), got $rc"; }
grep -q "Migration needed" <<<"$out" \
    || { echo "$out"; fail "pending migration not reported"; }
pass "pending migrations still detected"

echo ""
echo "All migrate-config version-file tests passed."
