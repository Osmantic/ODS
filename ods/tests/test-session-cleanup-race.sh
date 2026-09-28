#!/usr/bin/env bash
# Regression: a session created after the active set is read must survive the
# cleanup pass that is already running (#2931).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLEANUP="$ROOT/scripts/session-cleanup.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

SESSIONS="$tmp/sessions"
mkdir -p "$SESSIONS"

# sessions.json lists one active session; a second .jsonl is genuinely stale.
cat > "$SESSIONS/sessions.json" <<'JSON'
{
  "a": {"sessionId": "active-one"}
}
JSON
echo '{"role":"user"}' > "$SESSIONS/active-one.jsonl"
echo '{"role":"user"}' > "$SESSIONS/stale-one.jsonl"

# A third session appears *after* the cleanup stamps its snapshot — the race in
# #2931. What matters is the ordering (session mtime > snapshot mtime), so stage
# it deterministically with timestamps rather than trying to win a real race:
# the index and the pre-existing sessions are backdated, the new one is dated
# ahead of any marker the script can stamp during this run.
touch -t 202601010000 "$SESSIONS/sessions.json" \
    "$SESSIONS/active-one.jsonl" "$SESSIONS/stale-one.jsonl"
echo '{"role":"user"}' > "$SESSIONS/just-created.jsonl"
touch -t 209901010000 "$SESSIONS/just-created.jsonl"

out=$(SESSIONS_DIR="$SESSIONS" bash "$CLEANUP" 2>&1)

fail=0
if [ -f "$SESSIONS/just-created.jsonl" ]; then
    echo "PASS: session created mid-cleanup survives"
else
    echo "FAIL: session created mid-cleanup was deleted"
    echo "$out"
    fail=1
fi

if [ -f "$SESSIONS/active-one.jsonl" ]; then
    echo "PASS: active session retained"
else
    echo "FAIL: active session was deleted"
    fail=1
fi

if [ ! -f "$SESSIONS/stale-one.jsonl" ]; then
    echo "PASS: genuinely inactive session still pruned"
else
    echo "FAIL: inactive session was not pruned — the guard is too broad"
    fail=1
fi

# The marker must not be left behind in the sessions directory.
if ! ls "$SESSIONS"/.cleanup-snapshot.* >/dev/null 2>&1; then
    echo "PASS: snapshot marker cleaned up"
else
    echo "FAIL: snapshot marker left in $SESSIONS"
    fail=1
fi

[ "$fail" -eq 0 ] || exit 1
echo "All session-cleanup race tests passed"
