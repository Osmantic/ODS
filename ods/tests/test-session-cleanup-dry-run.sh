#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLEANUP="$ROOT_DIR/scripts/session-cleanup.sh"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-session-plan.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

cat > "$FIXTURE/sessions.json" <<'JSON'
{"agent:main:talk":{"sessionId":"active-bloated"}}
JSON
printf 'x%.0s' {1..100} > "$FIXTURE/active-bloated.jsonl"
printf '{"inactive":true}\n' > "$FIXTURE/inactive.jsonl"
printf 'debris\n' > "$FIXTURE/stale.deleted.1"
printf 'backup\n' > "$FIXTURE/stale.bak"

before_index="$(sha256sum "$FIXTURE/sessions.json" | cut -d' ' -f1)"
output="$(SESSIONS_DIR="$FIXTURE" MAX_SIZE=10 bash "$CLEANUP" --dry-run)"
after_index="$(sha256sum "$FIXTURE/sessions.json" | cut -d' ' -f1)"

[[ "$before_index" == "$after_index" ]]
[[ -f "$FIXTURE/active-bloated.jsonl" ]]
[[ -f "$FIXTURE/inactive.jsonl" ]]
[[ -f "$FIXTURE/stale.deleted.1" ]]
[[ -f "$FIXTURE/stale.bak" ]]
grep -q 'active-bloated' "$FIXTURE/sessions.json"

[[ "$output" == *"Would clean up 1 .deleted files, 1 .bak files"* ]]
[[ "$output" == *"Would remove inactive session: inactive"* ]]
[[ "$output" == *"Would clear 1 session reference(s)"* ]]
[[ "$output" == *"Dry run complete: would remove 1 inactive, 1 bloated"* ]]

echo "Session cleanup dry-run tests passed"
