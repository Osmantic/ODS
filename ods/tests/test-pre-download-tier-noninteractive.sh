#!/usr/bin/env bash
# Legacy snapshots lack a catalog artifact/terms identity. Reject before pip.
set -euo pipefail
if (( BASH_VERSINFO[0] < 4 )); then
    echo "[SKIP] pre-download.sh requires Bash 4+"
    exit 0
fi
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_PRE_DOWNLOAD_UNDER_TEST:-$ROOT_DIR/scripts/pre-download.sh}"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/bin"
cat > "$scratch/bin/python3" <<'EOF'
#!/bin/sh
printf invoked > "$ODS_PRE_DOWNLOAD_PROBE"
exit 0
EOF
chmod +x "$scratch/bin/python3"
for response in '' yes n; do
    if printf '%s\n' "$response" | PATH="$scratch/bin:$PATH" ODS_PRE_DOWNLOAD_PROBE="$scratch/invoked" \
        "$BASH" "$TARGET" --tier nano > "$scratch/output" 2>&1; then
        echo '[FAIL] unreviewed legacy snapshot was allowed' >&2
        exit 1
    fi
    grep -q 'Legacy snapshot downloads are blocked' "$scratch/output"
    [[ ! -e "$scratch/invoked" ]] || { echo '[FAIL] Python/download invoked before rejection'; exit 1; }
done
if PATH="$scratch/bin:$PATH" ODS_PRE_DOWNLOAD_PROBE="$scratch/invoked" \
    "$BASH" "$TARGET" --tier nano </dev/null > "$scratch/output" 2>&1; then
    echo '[FAIL] EOF authorized a legacy download' >&2
    exit 1
fi
[[ ! -e "$scratch/invoked" ]]
echo '[PASS] legacy tier snapshots reject EOF/yes/no before dependency or model download'
