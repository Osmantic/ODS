#!/usr/bin/env bash
# ============================================================================
# Contract Test: safe-env.sh resilience and edge case parsing
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Source safe-env.sh
source "$REPO_ROOT/ods/lib/safe-env.sh"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

TEST_ENV="$TMP_DIR/.env"
cat << 'EOF' > "$TEST_ENV"
# Sample comments
PLAIN_KEY=plain_value
QUOTED_KEY="quoted_value"
SINGLE_QUOTED_KEY='single_value'
BASE64_KEY=abc123==
CRLF_KEY=crlf_value
UID=1000
INVALID-KEY=should_be_ignored
EOF

# Execute load_env_file
load_env_file "$TEST_ENV"

# Verify assertions
[[ "${PLAIN_KEY:-}" == "plain_value" ]] || { echo "PLAIN_KEY mismatch"; exit 1; }
[[ "${QUOTED_KEY:-}" == "quoted_value" ]] || { echo "QUOTED_KEY mismatch"; exit 1; }
[[ "${SINGLE_QUOTED_KEY:-}" == "single_value" ]] || { echo "SINGLE_QUOTED_KEY mismatch"; exit 1; }
[[ "${BASE64_KEY:-}" == "abc123==" ]] || { echo "BASE64_KEY mismatch"; exit 1; }

echo "[OK] All safe-env contract assertions passed cleanly."
