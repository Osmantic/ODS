#!/usr/bin/env bash
# `ods update --dry-run` must report the installed version from manifest.json
# when ODS_VERSION is missing from .env and no .version file exists.
#
# cmd_dry_run() in ods-cli checked ODS_VERSION in .env and the .version file,
# but lacked the fallback to manifest.json's ods_version that get_current_version()
# and _check_version_compat() use. As a result, fresh installations (such as
# macOS where env-generator does not write ODS_VERSION into .env) reported
# "Installed : v0.0.0" on `ods update --dry-run`.
#
# Run from repo root:  bash ods/tests/test-update-dry-run-manifest-version.sh
# Or from ods:         bash tests/test-update-dry-run-manifest-version.sh

set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    for modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$modern_bash" ]]; then
            exec "$modern_bash" "$0" "$@"
        fi
    done
    printf '[SKIP] ods-cli requires Bash 4+\n'
    exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || fail "jq is required (ods-cli prerequisite)"

[[ -f "$ODS_CLI" ]] || fail "ods-cli not found at $ODS_CLI"

FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-dryrun-version.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

# Minimal install fixture structure to satisfy check_install
touch "$FIXTURE/docker-compose.base.yml"

# Provide offline mock curl to avoid external GitHub network dependencies
mkdir -p "$FIXTURE/bin"
cat > "$FIXTURE/bin/curl" <<'CURL'
#!/usr/bin/env bash
exit 1
CURL
chmod +x "$FIXTURE/bin/curl"

reported_installed_version() {
    PATH="$FIXTURE/bin:$PATH" ODS_HOME="$FIXTURE" NO_COLOR=1 bash "$ODS_CLI" update --dry-run 2>/dev/null \
        | sed -n 's/^[[:space:]]*Installed : v//p' | head -n1
}

reset_fixture() {
    rm -f "$FIXTURE/.env" "$FIXTURE/.version" "$FIXTURE/manifest.json"
}

echo "Test 1: fresh install with manifest.json (.env without ODS_VERSION, no .version)"
reset_fixture
printf 'ODS_MODE=local\n' > "$FIXTURE/.env"
printf '{"ods_version": "2.6.0"}\n' > "$FIXTURE/manifest.json"
out="$(PATH="$FIXTURE/bin:$PATH" ODS_HOME="$FIXTURE" NO_COLOR=1 bash "$ODS_CLI" update --dry-run 2>/dev/null || true)"
v="$(echo "$out" | sed -n 's/^[[:space:]]*Installed : v//p' | head -n1)"
[[ "$v" == "2.6.0" ]] || fail "expected 2.6.0 from manifest.json, got '$v'"
echo "$out" | grep -Fq 'Installed : v2.6.0' || fail "output missing 'Installed : v2.6.0'"
if echo "$out" | grep -Fq 'Installed : v0.0.0'; then
    fail "output unexpectedly contains 'Installed : v0.0.0'"
fi
pass "dry-run reports manifest version instead of v0.0.0"

echo "Test 2: .version file precedence over manifest.json"
reset_fixture
printf 'ODS_MODE=local\n' > "$FIXTURE/.env"
printf '{"ods_version": "2.6.0"}\n' > "$FIXTURE/manifest.json"
printf '{"version": "2.7.0"}\n' > "$FIXTURE/.version"
v="$(reported_installed_version)"
[[ "$v" == "2.7.0" ]] || fail "expected 2.7.0 from .version, got '$v'"
pass ".version file is preferred over manifest.json"

echo "Test 3: ODS_VERSION in .env takes highest precedence"
reset_fixture
printf 'ODS_VERSION=2.8.0\nODS_MODE=local\n' > "$FIXTURE/.env"
printf '{"ods_version": "2.6.0"}\n' > "$FIXTURE/manifest.json"
printf '{"version": "2.7.0"}\n' > "$FIXTURE/.version"
v="$(reported_installed_version)"
[[ "$v" == "2.8.0" ]] || fail "expected 2.8.0 from .env, got '$v'"
pass "ODS_VERSION in .env takes precedence over both .version and manifest.json"

echo "Test 4: default to 0.0.0 when no version source exists"
reset_fixture
printf 'ODS_MODE=local\n' > "$FIXTURE/.env"
v="$(reported_installed_version)"
[[ "$v" == "0.0.0" ]] || fail "expected 0.0.0 when no version sources exist, got '$v'"
pass "0.0.0 only when no version source exists"

echo ""
echo "All ods update --dry-run installed-version tests passed."
