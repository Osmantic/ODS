#!/usr/bin/env bash
# ============================================================================
# ODS preflight-engine.sh Shared Resolver Bug Regression Test
# ============================================================================
set -euo pipefail

# Derive ODS root directory from test file location
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Create temporary directory layout
TEMP_WORKSPACE="$(mktemp -d)"
trap 'rm -rf "$TEMP_WORKSPACE"' EXIT

# Create temporary ODS root directory
TEMP_ODS="$TEMP_WORKSPACE/ods"
mkdir -p "$TEMP_ODS/lib"
mkdir -p "$TEMP_ODS/scripts"

# Create a mock python-cmd.sh
cat > "$TEMP_ODS/lib/python-cmd.sh" <<'EOF'
ods_detect_python_cmd() {
  echo "$MOCK_PYTHON"
}
EOF

# Copy the real preflight-engine.sh to our mock location
cp "$ROOT_DIR/scripts/preflight-engine.sh" "$TEMP_ODS/scripts/"

# Create a mock python script that preflight-engine.sh should run
MOCK_PYTHON_BIN="$TEMP_WORKSPACE/mock_python"
export MOCK_PYTHON="$MOCK_PYTHON_BIN"
cat > "$MOCK_PYTHON_BIN" <<'EOF'
#!/bin/bash
echo "MOCK PYTHON CALLED"
exit 0
EOF
chmod +x "$MOCK_PYTHON_BIN"

# Mask system python3 and python to prevent fallback from succeeding.
MASK_DIR="$TEMP_WORKSPACE/mask"
mkdir -p "$MASK_DIR"
cat > "$MASK_DIR/python3" <<'EOF'
#!/bin/bash
echo "FAIL: System python3 called" >&2
exit 99
EOF
cat > "$MASK_DIR/python" <<'EOF'
#!/bin/bash
echo "FAIL: System python called" >&2
exit 99
EOF
chmod +x "$MASK_DIR/python3" "$MASK_DIR/python"

echo "Running preflight-engine.sh with --script-dir $TEMP_ODS"
set +e
OUTPUT=$(PATH="$MASK_DIR:$PATH" bash "$TEMP_ODS/scripts/preflight-engine.sh" \
  --report "$TEMP_WORKSPACE/report.json" \
  --script-dir "$TEMP_ODS" \
  --tier 1 \
  --ram-gb 16 \
  --disk-gb 50 \
  --gpu-backend cpu \
  --platform-id linux 2>&1)
EXIT_CODE=$?
set -e

echo "Exit code: $EXIT_CODE"
echo "Output: $OUTPUT"

if [[ "$OUTPUT" == *"MOCK PYTHON CALLED"* ]]; then
  echo "SUCCESS: Shared resolver was loaded and used!"
  exit 0
else
  echo "FAILURE: Shared resolver was NOT loaded/used!"
  exit 1
fi
