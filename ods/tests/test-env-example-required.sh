#!/usr/bin/env bash
# .env.example's REQUIRED block must match .env.schema.json "required" (#4201).
#
# The block used to list LIVEKIT_API_KEY, LIVEKIT_API_SECRET, DIFY_SECRET_KEY
# and OPENCODE_SERVER_PASSWORD under a header promising "docker compose will
# refuse to start" without them — none are in the schema's required[], and no
# compose file references any of them, so a base install starts fine. Telling
# operators to invent four secrets they do not need is a real cost.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXAMPLE="$ROOT/.env.example"
SCHEMA="$ROOT/.env.schema.json"

command -v jq >/dev/null 2>&1 || { echo "[SKIP] jq is required"; exit 0; }
[[ -f "$EXAMPLE" && -f "$SCHEMA" ]] || { echo "[FAIL] .env.example or .env.schema.json missing"; exit 1; }

PASS=0; FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

schema_required="$(jq -r '.required[]' "$SCHEMA" | sort)"

# Assignments between the REQUIRED banner and the next banner.
example_required="$(
  awk '
    /^# REQUIRED/           { inblock=1; next }
    inblock && /^# ═+$/     { if (seen) { exit } ; seen=1; next }
    inblock && /^[A-Z0-9_]+=/ { sub(/=.*/, ""); print }   # N8N_USER has a digit
  ' "$EXAMPLE" | sort
)"

if [[ "$example_required" == "$schema_required" ]]; then
    pass "REQUIRED block matches schema required[] ($(wc -l <<< "$schema_required" | tr -d ' ') keys)"
else
    fail "REQUIRED block and schema required[] disagree"
    echo "  only in .env.example:"; comm -23 <(echo "$example_required") <(echo "$schema_required") | sed 's/^/    /'
    echo "  only in schema:";       comm -13 <(echo "$example_required") <(echo "$schema_required") | sed 's/^/    /'
fi

# The four that caused #4201 must not be back in the REQUIRED block.
for key in LIVEKIT_API_KEY LIVEKIT_API_SECRET DIFY_SECRET_KEY OPENCODE_SERVER_PASSWORD; do
    if grep -qx "$key" <<< "$example_required"; then
        fail "$key is listed as REQUIRED but no compose file references it"
    fi
done
[[ "$FAIL" -eq 0 ]] && pass "no add-on credentials are presented as mandatory"

# Every REQUIRED key must still be a real schema property, not just in required[].
while read -r key; do
    [[ -z "$key" ]] && continue
    if ! jq -e --arg k "$key" '.properties[$k]' "$SCHEMA" >/dev/null; then
        fail "$key is in the REQUIRED block but absent from schema properties"
    fi
done <<< "$example_required"

echo "------------------------------------------------------------"
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
