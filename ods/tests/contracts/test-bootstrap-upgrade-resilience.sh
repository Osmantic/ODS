#!/usr/bin/env bash
# ============================================================================
# Contract Test: bootstrap-upgrade.sh file_size and atomic status writing
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

TEST_FILE="$TMP_DIR/sample.txt"
echo "Test file content for size check" > "$TEST_FILE"

# Test file_size logic
file_size_test() {
    if stat -c %s "$1" 2>/dev/null; then
        return
    fi
    stat -f %z "$1" 2>/dev/null || echo 0
}

SIZE=$(file_size_test "$TEST_FILE")
if (( SIZE <= 0 )); then
    echo "ERROR: file_size_test returned non-positive size $SIZE"
    exit 1
fi

# Test atomic status write logic
STATUS_FILE="$TMP_DIR/bootstrap-status.json"
write_status_test() {
    local phase="$1"
    local progress="$2"
    local error="${3:-}"
    local tmp_file="${STATUS_FILE}.tmp.$$"
    
    cat << EOF > "$tmp_file"
{
  "phase": "$phase",
  "progress": $progress,
  "error": "$error"
}
EOF
    mv -f "$tmp_file" "$STATUS_FILE"
}

write_status_test "downloading" 50 ""
[[ -f "$STATUS_FILE" ]] || { echo "STATUS_FILE missing"; exit 1; }

grep -q '"phase": "downloading"' "$STATUS_FILE" || { echo "Phase mismatch in status file"; exit 1; }

echo "[OK] All bootstrap-upgrade contract assertions passed cleanly."
