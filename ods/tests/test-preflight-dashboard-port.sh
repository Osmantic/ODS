#!/bin/bash
# ============================================================================
# Regression Test: Preflight Dashboard Port Resolution
# ============================================================================
# Proves that ods-preflight.sh respects the configured DASHBOARD_PORT.
#
# Hermetic test: does not require running Docker.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PREFLIGHT="$ROOT_DIR/ods-preflight.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo "Running Preflight Dashboard Port Regression Test..."

# Create a temporary directory for stubs
STUB_DIR=$(mktemp -d)
export STUB_DIR
trap 'rm -rf "$STUB_DIR"' EXIT

# Write stub curl to intercept requested URLs
cat > "$STUB_DIR/curl" << 'EOF'
#!/bin/bash
# Append all arguments to a log file in the stub directory
echo "$@" >> "$STUB_DIR/curl_calls.log"
# Return failure so the check continues to try other endpoints / doesn't pass prematurely
exit 1
EOF
chmod +x "$STUB_DIR/curl"

# Prepend stub directory to PATH so our fake curl is used
export PATH="$STUB_DIR:$PATH"

# Run ods-preflight.sh with a custom DASHBOARD_PORT override
export DASHBOARD_PORT=4500
export SERVICE_HOST=localhost

echo "Executing preflight check..."
set +e
bash "$PREFLIGHT" > "$STUB_DIR/preflight.log" 2>&1
preflight_status=$?
set -e

# Verify what URLs were requested by curl
LOG_FILE="$STUB_DIR/curl_calls.log"
if [[ ! -f "$LOG_FILE" ]]; then
    echo -e "${RED}FAIL:${NC} curl was not called by the preflight script."
    echo "Preflight exit status: $preflight_status"
    echo "Preflight log:"
    cat "$STUB_DIR/preflight.log"
    exit 1
fi

echo "Captured curl probe arguments:"
cat "$LOG_FILE"

# Track failures
FAILED_ASSERTION=false

# 1. Assert http://localhost:4500 was probed
if ! grep -q -F "http://localhost:4500" "$LOG_FILE"; then
    echo -e "${RED}FAIL:${NC} Expected probe http://localhost:4500 was missing."
    FAILED_ASSERTION=true
fi

# 2. Assert http://127.0.0.1:4500 was probed
if ! grep -q -F "http://127.0.0.1:4500" "$LOG_FILE"; then
    echo -e "${RED}FAIL:${NC} Expected probe http://127.0.0.1:4500 was missing."
    FAILED_ASSERTION=true
fi

# 3. Assert http://localhost:3001 was NOT probed
if grep -q -F "http://localhost:3001" "$LOG_FILE"; then
    echo -e "${RED}FAIL:${NC} Stale Dashboard probe http://localhost:3001 was found."
    FAILED_ASSERTION=true
fi

# 4. Assert http://127.0.0.1:3001 was NOT probed
if grep -q -F "http://127.0.0.1:3001" "$LOG_FILE"; then
    echo -e "${RED}FAIL:${NC} Stale Dashboard probe http://127.0.0.1:3001 was found."
    FAILED_ASSERTION=true
fi

if [ "$FAILED_ASSERTION" = true ]; then
    echo "Preflight exit status: $preflight_status"
    exit 1
else
    echo -e "${GREEN}SUCCESS:${NC} Dashboard probe respected DASHBOARD_PORT=4500 and did not probe default port 3001."
    exit 0
fi
