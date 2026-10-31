#!/usr/bin/env bash
# Contract: token-spy must not let remote-derived session ids re-parse in the
# remote shell when it cleans them up (#2929).
#
# The stub below is not a mock that merely records argv — it runs the command
# ssh was handed in a real shell, in a real directory, with the real stdin. If
# a session id can inject a second command, that command actually executes and
# the test sees its side effect.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANAGER="$SCRIPT_DIR/../extensions/services/token-spy/session-manager.sh"

pass() { echo "  ✓ $1"; }
fail() { echo "  ✗ $1"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

REMOTE_ROOT="$WORK/remote"          # stands in for the remote host's cwd
REMOTE_DIR="$REMOTE_ROOT/sessions"
mkdir -p "$REMOTE_DIR"

# Sessions as the remote host would report them. "live" is active; the other
# three are inactive and therefore cleanup candidates.
INJECT='evil; touch pwned'          # a second command, if it ever re-parses
TRAVERSE='../escaped'               # a path-like id
for sid in "live" "stale" "$INJECT" "$TRAVERSE"; do
  echo '{}' > "$REMOTE_DIR/${sid}.jsonl"
done

cat > "$WORK/remote-info" <<EOF
SESSION_LIST_START
live|100|1
stale|100|1
${INJECT}|100|1
${TRAVERSE}|100|1
SESSION_LIST_END
ACTIVE_IDS_START
live
ACTIVE_IDS_END
TOTAL_SIZE=400
EOF

# ── ssh stub ────────────────────────────────────────────────────────────────
mkdir -p "$WORK/bin"
cat > "$WORK/bin/ssh" <<EOF
#!/bin/bash
set -eu
while [ "\${1:-}" = "-o" ]; do shift 2; done
shift                                  # drop the host
if [ "\${1:-}" = "bash" ]; then
    cat > /dev/null                    # consume the inventory heredoc
    cat "$WORK/remote-info"
    exit 0
fi
# Anything else is a real remote command line: run it as the remote shell
# would, from the remote home directory, with ssh's stdin passed through.
cd "$REMOTE_ROOT"
exec /bin/sh -c "\$*"
EOF
chmod +x "$WORK/bin/ssh"
PATH="$WORK/bin:$PATH"

# ── drive the real function ─────────────────────────────────────────────────
# Same approach as test-token-spy-session-manager.sh: the script's top level
# runs a main loop, so extract the one function under test.
FUNCS="$WORK/funcs.sh"
sed -n '/^manage_remote_agent()/,/^}/p' "$MANAGER" > "$FUNCS"
log() { echo "$*" >> "$WORK/log"; }
: > "$WORK/log"
REMOTE_FILE_SIZE_LIMIT=999999
RECENT_MINUTES=10
# shellcheck disable=SC1090
source "$FUNCS"

manage_remote_agent "testagent" "fake-host" "$REMOTE_DIR"

echo "Test 1: an injected command in a session id never executes remotely"
[ -e "$REMOTE_ROOT/pwned.jsonl" ] && fail "injected 'touch pwned' ran on the remote host"
[ -e "$REMOTE_ROOT/pwned" ] && fail "injected 'touch pwned' ran on the remote host"
pass "no injected side effect"

echo "Test 2: the literal session file is still the thing that got removed"
[ -e "$REMOTE_DIR/${INJECT}.jsonl" ] && fail "the awkwardly-named session was not removed"
pass "session with metacharacters removed by exact name"

echo "Test 3: a path-like session id is refused, not deleted through"
[ -f "$REMOTE_DIR/${TRAVERSE}.jsonl" ] || fail "path-like id was acted on instead of skipped"
grep -q "Refusing path-like session id" "$WORK/log" || fail "no log line for the refused id"
pass "path-like id skipped and logged"

echo "Test 4: ordinary cleanup still works"
[ -e "$REMOTE_DIR/stale.jsonl" ] && fail "inactive session was not cleaned up"
[ -f "$REMOTE_DIR/live.jsonl" ] || fail "active session was deleted"
pass "inactive removed, active kept"

echo "All token-spy remote cleanup tests passed"
