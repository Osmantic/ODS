#!/usr/bin/env bash
# ods-update.sh must report the installed version on a fresh install.
#
# get_current_version() only read .version, which no installer creates and
# which `check` populates with last_check alone, so every fresh install showed
# "Version: 0.0.0", `check` announced "Update available: 0.0.0 -> vX.Y.Z" on
# the latest release, and rollback snapshots were recorded as version 0.0.0.
# It now falls back to ODS_VERSION in .env and manifest.json's ods_version,
# the sources ods-cli and the dashboard already use.
#
# Run from repo root:  bash ods/tests/test-update-current-version.sh
# Or from ods:         bash tests/test-update-current-version.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || fail "jq is required (ods-update.sh prerequisite)"
command -v curl >/dev/null 2>&1 || fail "curl is required (ods-update.sh prerequisite)"

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-update-version.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT
# INSTALL_DIR is the script's own directory, so copy it into the fixture.
cp "$ROOT_DIR/ods-update.sh" "$FIXTURE/ods-update.sh"
mkdir -p "$FIXTURE/home"

reported_version() {
    # `status` reads only; HOME is redirected so BACKUP_DIR never leaves the fixture.
    HOME="$FIXTURE/home" bash "$FIXTURE/ods-update.sh" status 2>/dev/null \
        | sed -n 's/^Version:[[:space:]]*//p' | head -n1
}

reset_fixture() {
    rm -f "$FIXTURE/.env" "$FIXTURE/.version" "$FIXTURE/manifest.json"
}

echo "Test 1: fresh Linux install (ODS_VERSION in .env, no .version)"
reset_fixture
printf 'ODS_VERSION=2.6.0\nLLM_MODEL=qwen\n' > "$FIXTURE/.env"
v="$(reported_version)"
[[ "$v" == "2.6.0" ]] || fail "expected 2.6.0 from .env, got '$v'"
pass "status reports the .env version"

echo "Test 2: fresh install with only manifest.json (macOS env-generator writes no ODS_VERSION)"
reset_fixture
printf '{"ods_version": "2.6.0"}\n' > "$FIXTURE/manifest.json"
v="$(reported_version)"
[[ "$v" == "2.6.0" ]] || fail "expected 2.6.0 from manifest.json, got '$v'"
pass "status falls back to manifest.json ods_version"

echo "Test 3: after 'check' wrote a .version holding only last_check"
reset_fixture
printf 'ODS_VERSION="2.6.0"\n' > "$FIXTURE/.env"
printf '{"last_check": "2026-09-07T18:53:25Z"}\n' > "$FIXTURE/.version"
v="$(reported_version)"
[[ "$v" == "2.6.0" ]] || fail "a version-less .version must not hide the .env version, got '$v'"
pass "a .version without a version key does not mask .env (quotes stripped)"

echo "Test 4: after an update, the .version record written by ods-update.sh wins"
reset_fixture
printf 'ODS_VERSION=2.6.0\n' > "$FIXTURE/.env"
printf '{"version": "v2.7.0", "last_update": "2026-09-08T00:00:00Z"}\n' > "$FIXTURE/.version"
v="$(reported_version)"
[[ "$v" == "v2.7.0" ]] || fail "expected the post-update .version record to win, got '$v'"
pass "post-update .version record is preferred"

echo "Test 5: nothing recorded anywhere"
reset_fixture
v="$(reported_version)"
[[ "$v" == "0.0.0" ]] || fail "expected 0.0.0 with no version sources, got '$v'"
pass "0.0.0 only when no source exists"

echo ""
echo "All ods-update.sh current-version tests passed."
