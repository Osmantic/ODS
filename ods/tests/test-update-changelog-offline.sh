#!/usr/bin/env bash
# ods-update.sh changelog must not recurse forever when offline.
#
# With no local CHANGELOG.md, `changelog` fetched the latest release tag and
# re-entered itself with the tag as $1. When curl fails (offline) or the
# payload has no tag_name, that re-entry is called with an empty string,
# lands on the same missing-file branch, and spins forever — each recursion
# printing another "Fetching latest release notes" line until killed.
#
# Run from repo root:  bash ods/tests/test-update-changelog-offline.sh
# Or from ods:         bash tests/test-update-changelog-offline.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || fail "jq is required (ods-update.sh prerequisite)"

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-update-changelog.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT
# INSTALL_DIR is the script's own directory, so copy it into the fixture.
# Deliberately no CHANGELOG.md in the fixture.
cp "$ROOT_DIR/ods-update.sh" "$FIXTURE/ods-update.sh"
mkdir -p "$FIXTURE/home" "$FIXTURE/bin"

# curl stub selected per case by replacing this file.
cat > "$FIXTURE/bin/curl" <<'SH'
#!/usr/bin/env bash
exit 1
SH
chmod +x "$FIXTURE/bin/curl"

run_changelog() {
    # Hard timeout: the unfixed script never exits on its own.
    timeout 15 env PATH="$FIXTURE/bin:$PATH" HOME="$FIXTURE/home" \
        bash "$FIXTURE/ods-update.sh" changelog 2>&1
}

echo "Test 1: offline curl fails -> clean error, no spin"
set +e
out="$(run_changelog)"
rc=$?
set -e
[[ "$rc" -ne 124 ]] || fail "changelog timed out — recursive re-entry still spins offline"
[[ "$rc" -ne 0 ]] || { echo "$out"; fail "changelog should fail when no changelog source is reachable"; }
grep -q "Could not determine the latest release" <<<"$out" \
    || { echo "$out"; fail "missing explicit offline error"; }
[[ "$(grep -c 'Fetching latest release notes' <<<"$out")" -eq 1 ]] \
    || { echo "$out"; fail "release fetch was attempted more than once"; }
pass "offline changelog exits promptly with a clear error"

echo "Test 2: release payload without tag_name -> clean error"
cat > "$FIXTURE/bin/curl" <<'SH'
#!/usr/bin/env bash
if [[ "$*" == *"releases/latest"* ]]; then
    printf '%s\n' '{"name":"no tag here"}'
    exit 0
fi
exit 1
SH
chmod +x "$FIXTURE/bin/curl"
set +e
out="$(run_changelog)"
rc=$?
set -e
[[ "$rc" -ne 124 ]] || fail "changelog timed out on a tag-less release payload"
[[ "$rc" -ne 0 ]] || fail "changelog should fail when latest release has no tag_name"
pass "tag-less payload exits promptly"

echo "Test 3: latest tag resolves -> release body is printed"
cat > "$FIXTURE/bin/curl" <<'SH'
#!/usr/bin/env bash
if [[ "$*" == *"releases/latest"* ]]; then
    printf '%s\n' '{"tag_name":"v9.9.9"}'
    exit 0
fi
if [[ "$*" == *"releases/tags/v9.9.9"* ]]; then
    printf '%s\n' '{"body":"## v9.9.9 release notes"}'
    exit 0
fi
exit 1
SH
chmod +x "$FIXTURE/bin/curl"
out="$(run_changelog)"
grep -q "v9.9.9 release notes" <<<"$out" \
    || { echo "$out"; fail "resolved release body was not printed"; }
pass "reachable release notes still render"

echo "Test 4: local CHANGELOG.md short-circuits the network path"
printf '# Changelog\n\n- local entry\n' > "$FIXTURE/CHANGELOG.md"
out="$(run_changelog)"
grep -q "local entry" <<<"$out" \
    || { echo "$out"; fail "local CHANGELOG.md was not shown"; }
pass "local changelog wins when present"

echo ""
echo "All ods-update.sh changelog offline tests passed."
