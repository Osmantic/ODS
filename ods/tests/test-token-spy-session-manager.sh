#!/usr/bin/env bash
# Contract: token-spy's session manager must never delete a live session file.
#
# clean_inactive() decides what to delete by diffing the *.jsonl files on disk
# against the sessionIds in sessions.json. Two ways that went wrong:
#
#   1. a greedy `sed 's/.*"sessionId": *"\([^"]*\)".*/\1/'` reports only the
#      LAST id on a line, so a compact sessions.json (one line, every entry)
#      left all but one live session looking inactive; and
#   2. an empty extraction was read as "nothing is active" rather than "the
#      parse failed", which deletes every session file.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANAGER="$SCRIPT_DIR/../extensions/services/token-spy/session-manager.sh"

pass() { echo "  ✓ $1"; }
fail() { echo "  ✗ $1"; exit 1; }

# Source only the helpers: the script's top level defines config and then
# main() runs under `set -euo pipefail`, so extract the two functions we drive.
FUNCS="$(mktemp)"
trap 'rm -f "$FUNCS"' EXIT
sed -n '/^extract_active_ids()/,/^}/p;/^clean_inactive()/,/^}/p' "$MANAGER" > "$FUNCS"
log() { :; }   # silence the manager's logger
# shellcheck disable=SC1090
source "$FUNCS"

echo "Test 1: every id is extracted from a compact (single-line) sessions.json"
WORK="$(mktemp -d)"
printf '{"a":{"sessionId":"aaa"},"b":{"sessionId":"bbb"},"c":{"sessionId":"ccc"}}\n' \
  > "$WORK/sessions.json"
got="$(extract_active_ids "$WORK/sessions.json" | tr '\n' ' ')"
[ "$got" = "aaa bbb ccc " ] || fail "expected all three ids, got '$got'"
pass "compact sessions.json yields every id, not just the last"

echo "Test 2: live sessions survive cleanup with a compact sessions.json"
for id in aaa bbb ccc; do echo '{}' > "$WORK/$id.jsonl"; done
echo '{}' > "$WORK/orphan.jsonl"
clean_inactive "$WORK"
for id in aaa bbb ccc; do
  [ -f "$WORK/$id.jsonl" ] || fail "deleted live session $id"
done
pass "all three live sessions kept"
[ -f "$WORK/orphan.jsonl" ] && fail "orphan session was not cleaned"
pass "orphan session still removed"
rm -rf "$WORK"

echo "Test 3: an unparseable sessions.json cancels cleanup instead of wiping"
WORK="$(mktemp -d)"
# Non-empty but carrying no sessionId key — a schema change or partial write.
printf '{"a":{"id":"aaa"},"b":{"id":"bbb"}}\n' > "$WORK/sessions.json"
for id in aaa bbb; do echo '{}' > "$WORK/$id.jsonl"; done
clean_inactive "$WORK"
for id in aaa bbb; do
  [ -f "$WORK/$id.jsonl" ] || fail "wiped $id on an unparseable sessions.json"
done
pass "no session deleted when no id could be parsed"
rm -rf "$WORK"

echo "Test 4: pretty-printed sessions.json still works (no regression)"
WORK="$(mktemp -d)"
cat > "$WORK/sessions.json" <<'JSON'
{
  "a": { "sessionId": "aaa" },
  "b": { "sessionId": "bbb" }
}
JSON
for id in aaa bbb; do echo '{}' > "$WORK/$id.jsonl"; done
echo '{}' > "$WORK/gone.jsonl"
clean_inactive "$WORK"
[ -f "$WORK/aaa.jsonl" ] && [ -f "$WORK/bbb.jsonl" ] || fail "deleted a live session"
[ -f "$WORK/gone.jsonl" ] && fail "orphan not cleaned"
pass "pretty-printed layout keeps live sessions and cleans orphans"
rm -rf "$WORK"

echo "Test 5: a zero-byte sessions.json (partial write) cancels cleanup"
WORK="$(mktemp -d)"
: > "$WORK/sessions.json"
for id in aaa bbb; do echo '{}' > "$WORK/$id.jsonl"; done
clean_inactive "$WORK"
for id in aaa bbb; do
  [ -f "$WORK/$id.jsonl" ] || fail "wiped $id on a zero-byte sessions.json"
done
pass "no session deleted when sessions.json is a partial write"
rm -rf "$WORK"

echo ""
echo "✓ All token-spy session-manager tests passed"
