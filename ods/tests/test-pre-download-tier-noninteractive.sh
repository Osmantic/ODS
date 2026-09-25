#!/usr/bin/env bash
# `pre-download.sh --tier <tier>` is documented as a non-interactive
# invocation, but download_tier() prompts "Continue? [Y/n]" and the script
# runs under `set -euo pipefail`. With a closed stdin (CI, a pipe, nohup) the
# read returns non-zero and aborts before the download. This asserts the
# --tier path runs to completion with stdin closed.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$candidate" ]] && exec "$candidate" "$0" "$@"
    done
    echo "[SKIP] pre-download.sh requires Bash 4+; this host only has Bash ${BASH_VERSION}"
    echo "Result: 0 passed, 0 failed, 1 skipped"
    exit 0
fi
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_PRE_DOWNLOAD_UNDER_TEST:-$ROOT_DIR/scripts/pre-download.sh}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$TARGET" ]] || fail "missing $TARGET"

# Stub python3/pip3 so check_dependencies passes without a real install and
# download_model's snapshot_download heredoc does nothing but succeed. No
# network, no pip, no Hugging Face access.
BIN="$TMP_DIR/bin"; mkdir -p "$BIN"
cat > "$BIN/python3" <<'EOF'
#!/bin/sh
# `-c "import ..."` probes (check_dependencies) and the download heredoc on
# stdin both just succeed; nothing is downloaded.
if [ "$1" = "-c" ]; then exit 0; fi
cat >/dev/null 2>&1 || true
echo "Downloaded to: /tmp/fake-model-cache"
exit 0
EOF
cat > "$BIN/pip3" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$BIN/python3" "$BIN/pip3"

out="$TMP_DIR/out.txt"
set +e
PATH="$BIN:$PATH" ODS_PYTHON_CMD="$BIN/python3" "$BASH" "$TARGET" --tier nano </dev/null >"$out" 2>&1
rc=$?
set -e

if [[ $rc -ne 0 ]]; then
    fail "pre-download.sh --tier nano aborted with stdin closed (rc=$rc): $(grep -iE 'error|cancel' "$out" | head -n 1)"
fi
grep -q "Pre-download complete" "$out" \
    || fail "the --tier download did not run to completion with stdin closed: $(tail -n 3 "$out" | tr '\n' ' ')"
pass "pre-download.sh --tier runs to completion with stdin closed"

# It must still honour an explicit decline when one is piped in.
out2="$TMP_DIR/out2.txt"
set +e
printf 'n' | PATH="$BIN:$PATH" ODS_PYTHON_CMD="$BIN/python3" "$BASH" "$TARGET" --tier nano >"$out2" 2>&1
rc2=$?
set -e
[[ $rc2 -eq 0 ]] || fail "declining should exit 0, got $rc2"
grep -q "Cancelled" "$out2" || fail "a piped 'n' should cancel the download"
grep -q "Pre-download complete" "$out2" && fail "download ran despite a piped 'n'"
pass "a piped 'n' still cancels the download"
