#!/bin/bash
# ============================================================================
# ODS Support Bundle Test Suite
# ============================================================================
# Verifies the standalone diagnostics bundle generator creates a useful,
# redacted archive without requiring Docker.
#
# Usage: bash tests/test-support-bundle.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SUPPORT_SCRIPT="$ROOT_DIR/scripts/ods-support-bundle.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0

pass() { echo -e "  ${GREEN}PASS${NC}  $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}FAIL${NC}  $1"; FAILED=$((FAILED + 1)); }
skip() { echo -e "  ${YELLOW}SKIP${NC}  $1"; SKIPPED=$((SKIPPED + 1)); }

echo ""
echo "ODS Support Bundle Test Suite"
echo "======================================"

TMP_DIR="$(mktemp -d)"
ENV_PATH="$ROOT_DIR/.env"
ENV_BACKUP=""
HAD_ENV=false
BASH_WRAPPER_LOG="$TMP_DIR/bash-wrapper.log"
REAL_BASH="$(command -v bash)"
BASH_WRAPPER="$TMP_DIR/bash-wrapper"

cleanup() {
    if [[ "$HAD_ENV" == "true" && -n "$ENV_BACKUP" && -f "$ENV_BACKUP" ]]; then
        cp "$ENV_BACKUP" "$ENV_PATH"
    else
        rm -f "$ENV_PATH"
    fi
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if [[ -f "$ENV_PATH" ]]; then
    HAD_ENV=true
    ENV_BACKUP="$TMP_DIR/env.backup"
    cp "$ENV_PATH" "$ENV_BACKUP"
fi

if [[ -f "$SUPPORT_SCRIPT" ]]; then
    pass "support bundle script exists"
else
    fail "support bundle script is missing"
    exit 1
fi

if bash -n "$SUPPORT_SCRIPT"; then
    pass "support bundle script passes bash syntax check"
else
    fail "support bundle script has a syntax error"
fi

if bash "$SUPPORT_SCRIPT" --help | grep -q "Create a redacted diagnostics bundle"; then
    pass "--help describes support bundle behavior"
else
    fail "--help output is missing expected text"
fi

cat > "$BASH_WRAPPER" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$BASH_WRAPPER_LOG"
exec "$REAL_BASH" "\$@"
EOF
chmod +x "$BASH_WRAPPER"

SECRET_VALUE="support-bundle-super-secret"
cat > "$ENV_PATH" <<EOF
DASHBOARD_API_KEY=$SECRET_VALUE
OPENAI_API_KEY=$SECRET_VALUE
N8N_USER=$SECRET_VALUE
LANGFUSE_INIT_USER_EMAIL=$SECRET_VALUE
NORMAL_VALUE=visible-value
ODS_MODE=cloud
GPU_BACKEND=cpu
GPU_COUNT=1
LLM_API_URL=http://litellm:4000
HERMES_LLM_BASE_URL=http://litellm:4000/v1
EOF

OUTPUT_DIR="$TMP_DIR/out"
RESULT_JSON="$TMP_DIR/result.json"

if ODS_SUPPORT_BUNDLE_BASH="$BASH_WRAPPER" ODS_SUPPORT_BUNDLE_DISABLE_DOCKER=1 bash "$SUPPORT_SCRIPT" --output "$OUTPUT_DIR" --no-logs --json > "$RESULT_JSON"; then
    pass "support bundle command succeeds with Docker disabled"
else
    fail "support bundle command failed with Docker disabled"
fi

if grep -q 'scripts/ods-doctor.sh' "$BASH_WRAPPER_LOG" && grep -q 'scripts/resolve-compose-stack.sh' "$BASH_WRAPPER_LOG"; then
    pass "support bundle uses selected Bash for nested project scripts"
else
    fail "support bundle did not use selected Bash for nested project scripts"
fi

if python3 -m json.tool "$RESULT_JSON" >/dev/null; then
    pass "--json output is valid JSON"
else
    fail "--json output is not valid JSON"
fi

read_bundle_field() {
    python3 - "$RESULT_JSON" "$1" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(payload[sys.argv[2]])
PY
}

BUNDLE_DIR="$(read_bundle_field bundle_dir)"
ARCHIVE_PATH="$(read_bundle_field archive)"
MANIFEST_PATH="$(read_bundle_field manifest)"

if [[ "$BUNDLE_DIR" != *\\* ]] && [[ "$ARCHIVE_PATH" != *\\* ]]; then
    pass "--json paths are POSIX-safe (no backslashes)"
else
    fail "--json paths contain backslashes — not portable across shells"
fi

if [[ -d "$BUNDLE_DIR" ]]; then
    pass "bundle directory is created"
else
    fail "bundle directory was not created"
fi

if [[ -f "$ARCHIVE_PATH" ]]; then
    pass "bundle archive is created"
else
    fail "bundle archive was not created"
fi

if [[ -f "$MANIFEST_PATH" ]] && python3 -m json.tool "$MANIFEST_PATH" >/dev/null; then
    pass "manifest.json is valid JSON"
else
    fail "manifest.json is missing or invalid"
fi

TAR_LIST="$TMP_DIR/archive-list.txt"
tar -tzf "$ARCHIVE_PATH" > "$TAR_LIST"

if grep -q "/manifest.json$" "$TAR_LIST"; then
    pass "archive contains manifest.json"
else
    fail "archive does not contain manifest.json"
fi

if grep -q "/manifest/evidence.json$" "$TAR_LIST"; then
    pass "archive contains manifest/evidence.json"
else
    fail "archive does not contain manifest/evidence.json"
fi

if [[ -f "$BUNDLE_DIR/config/env.redacted" ]] && grep -q "DASHBOARD_API_KEY=\\[REDACTED\\]" "$BUNDLE_DIR/config/env.redacted"; then
    pass ".env is included only as redacted env"
else
    fail "redacted env file is missing expected redaction"
fi

# Schema secret:true user/email keys must be redacted too — they ship in the
# publicly shared bundle. The old keyword set omitted USER/EMAIL.
if grep -q "N8N_USER=\\[REDACTED\\]" "$BUNDLE_DIR/config/env.redacted" \
    && grep -q "LANGFUSE_INIT_USER_EMAIL=\\[REDACTED\\]" "$BUNDLE_DIR/config/env.redacted"; then
    pass "schema-secret user/email env keys are redacted"
else
    fail "N8N_USER / LANGFUSE_INIT_USER_EMAIL leaked into env.redacted"
fi

if grep -R "$SECRET_VALUE" "$BUNDLE_DIR" >/dev/null 2>&1; then
    fail "raw test secret leaked into bundle directory"
else
    pass "raw test secret is absent from bundle directory"
fi

EVIDENCE_PATH="$BUNDLE_DIR/manifest/evidence.json"
if [[ -f "$EVIDENCE_PATH" ]] && python3 -m json.tool "$EVIDENCE_PATH" >/dev/null; then
    pass "evidence.json is valid JSON"
else
    fail "evidence.json is missing or invalid"
fi

if python3 - "$EVIDENCE_PATH" <<'PY'
import json
import sys
evidence = json.load(open(sys.argv[1], encoding="utf-8"))
assert evidence["version"] == "1"
assert "platform" in evidence
assert isinstance(evidence["platform"]["wsl"], bool)
assert "backend" in evidence
assert "inference_contract" in evidence
assert "compose" in evidence
assert "config_hashes" in evidence
assert evidence["env_keys"]["DASHBOARD_API_KEY"]["redacted"] is True
assert evidence["env_keys"]["DASHBOARD_API_KEY"]["value"] is None
assert evidence["env_keys"]["NORMAL_VALUE"]["value"] == "visible-value"
assert "docker-compose.cloud.yml" in evidence["compose"]["files"]
assert "docker-compose.cpu.yml" not in evidence["compose"]["files"]
assert evidence["inference_contract"]["issue_counts"]["blockers"] == 0
PY
then
    pass "evidence.json records redacted env and support metadata"
else
    fail "evidence.json is missing expected support metadata"
fi

ENV_MEMBER="$(grep '/config/env.redacted$' "$TAR_LIST" | sed -n '1p')"
ARCHIVE_ENV="$TMP_DIR/env-from-archive"
if [[ -z "$ENV_MEMBER" ]]; then
    fail "archive does not contain config/env.redacted"
elif ! tar -xOf "$ARCHIVE_PATH" "$ENV_MEMBER" > "$ARCHIVE_ENV"; then
    fail "could not extract config/env.redacted from archive"
elif grep -q "$SECRET_VALUE" "$ARCHIVE_ENV"; then
    fail "raw test secret leaked into bundle archive"
else
    pass "raw test secret is absent from bundle archive env"
fi

if [[ -f "$BUNDLE_DIR/docker/unavailable.txt" ]]; then
    pass "Docker-disabled run records Docker as unavailable"
else
    fail "Docker-disabled run did not record Docker unavailable state"
fi

if python3 - "$MANIFEST_PATH" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
assert manifest["tool"] == "ods-support-bundle"
assert isinstance(manifest["files"], list) and manifest["files"]
assert isinstance(manifest["commands"], list) and manifest["commands"]
assert any(command["label"] == "docker-version" for command in manifest["commands"])
assert any(item["path"] == "manifest/evidence.json" for item in manifest["files"])
PY
then
    pass "manifest records files and command exit codes"
else
    fail "manifest is missing expected file/command metadata"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed, $SKIPPED skipped"
[[ "$FAILED" -eq 0 ]]
