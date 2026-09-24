#!/usr/bin/env bash
# Regression contract: the update pull step must (a) run git non-interactively
# (the updater is invoked headless by the dashboard host agent) and (b) route a
# `git fetch` failure through _update_rollback instead of dying under `set -e`.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT_DIR/ods/ods-update.sh"

fail=0
check() {
    if eval "$2"; then
        echo "PASS: $1"
    else
        echo "FAIL: $1"
        fail=1
    fi
}

# ── Static contract ─────────────────────────────────────────────────────────

check "git fetch is guarded and routed through rollback" \
    'grep -q "if ! \"\${git_env\[@\]}\" git fetch origin" "$SCRIPT" &&
     sed -n "/git fetch origin/,/return 1/p" "$SCRIPT" | grep -q "_update_rollback"'

check "remote git ops run with GIT_TERMINAL_PROMPT=0 default" \
    'grep -q "GIT_TERMINAL_PROMPT=\"\${GIT_TERMINAL_PROMPT:-0}\"" "$SCRIPT"'

check "remote git ops default to ssh BatchMode (no host-key/auth prompts)" \
    'grep -q "GIT_SSH_COMMAND=\"\${GIT_SSH_COMMAND:-ssh -oBatchMode=yes}\"" "$SCRIPT"'

check "git pull --ff-only also runs under the non-interactive env" \
    'grep -q "if ! \"\${git_env\[@\]}\" git pull --ff-only origin \"\$update_branch\"" "$SCRIPT"'

# ── Behavioral: eval the real pull block under a stubbed git ────────────────

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Extract the pull block (Step 2) verbatim and wrap it in a function so `local`
# and `return` are legal.
python3 - "$SCRIPT" "$TMP/pull-block.sh" <<'PY'
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.index("# ── Step 2: pull latest changes")
end = text.index("# ── Step 3: migrations", start)
block = text[start:end]
assert "git fetch origin" in block and "git pull --ff-only" in block
pathlib.Path(sys.argv[2]).write_text(
    "#!/usr/bin/env bash\nrun_pull_block() {\n" + block + "\n}\n",
    encoding="utf-8",
)
PY

cat > "$TMP/git" <<'EOF'
#!/usr/bin/env bash
echo "GIT_TERMINAL_PROMPT=${GIT_TERMINAL_PROMPT-<unset>} GIT_SSH_COMMAND=${GIT_SSH_COMMAND-<unset>} args=$*" >> "$ODS_TEST_CAPTURE"
if [[ "$1 $2" == "branch --show-current" ]]; then
    echo "public-beta"
    exit 0
fi
exit "${GIT_STUB_RC:-0}"
EOF
chmod +x "$TMP/git"

cat > "$TMP/harness.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export PATH="$1:$PATH"
export ODS_TEST_CAPTURE="$1/capture.log"
INSTALL_DIR="$1/repo"
mkdir -p "$INSTALL_DIR"
rollback_log="$1/rollback.log"
snap_dir="$1/snap"
compose_flags=""
_update_rollback() { echo "ROLLBACK: $1" >> "$rollback_log"; }
log_info() { :; }
source "$1/pull-block.sh"
if run_pull_block; then echo "RC=0"; else echo "RC=$?"; fi
EOF

# Scenario 1: fetch+pull succeed → rc 0, non-interactive env on every git call.
GIT_STUB_RC=0 bash "$TMP/harness.sh" "$TMP" > "$TMP/out1.txt" 2>&1
check "fetch+pull success returns 0" 'grep -q "RC=0" "$TMP/out1.txt"'
check "git ran with GIT_TERMINAL_PROMPT=0 and BatchMode ssh" \
    'grep -q "GIT_TERMINAL_PROMPT=0" "$TMP/capture.log" &&
     grep -q "BatchMode=yes" "$TMP/capture.log"'
check "both fetch and pull used the env" \
    'grep -q "args=fetch origin" "$TMP/capture.log" &&
     grep -q "args=pull --ff-only origin" "$TMP/capture.log"'

# Scenario 2: fetch fails → rollback invoked, block returns 1 (no set -e abort).
rm -f "$TMP/capture.log" "$TMP/rollback.log"
GIT_STUB_RC=1 bash "$TMP/harness.sh" "$TMP" > "$TMP/out2.txt" 2>&1 || true
check "fetch failure returns 1" 'grep -q "RC=1" "$TMP/out2.txt"'
check "fetch failure routes through _update_rollback" \
    'grep -q "ROLLBACK: Git fetch failed" "$TMP/rollback.log"'

# Scenario 3: pull fails after good fetch → rollback still invoked.
cat > "$TMP/git" <<'EOF'
#!/usr/bin/env bash
echo "args=$*" >> "$ODS_TEST_CAPTURE"
if [[ "$1 $2" == "branch --show-current" ]]; then
    echo "public-beta"
    exit 0
fi
[[ "$1" == "pull" ]] && exit 1 || exit 0
EOF
chmod +x "$TMP/git"
rm -f "$TMP/capture.log" "$TMP/rollback.log"
bash "$TMP/harness.sh" "$TMP" > "$TMP/out3.txt" 2>&1 || true
check "pull failure returns 1" 'grep -q "RC=1" "$TMP/out3.txt"'
check "pull failure routes through _update_rollback" \
    'grep -q "ROLLBACK: Git pull failed" "$TMP/rollback.log"'

if [[ $fail -eq 0 ]]; then
    echo "PASS: update git ops are non-interactive and fetch failure rolls back"
else
    exit 1
fi
