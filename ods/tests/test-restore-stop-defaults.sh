#!/bin/bash
# Test for ods-restore.sh default stop_first behavior
# Validates that -s is now default

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_RESTORE="$SCRIPT_DIR/../ods/ods-restore.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }

info() { echo -e "${BLUE}ℹ${NC} $1"; }

# Mock docker compose down to verify call
mock_docker() {
    echo "MOCK_DOCKER_DOWN_CALLED" > "$MOCK_LOG"
}

test_default_stop_first() {
    info "Testing if stop_first defaults to true..."
    
    # Create a dummy ODS directory with a compose file to trigger stop_containers
    export ODS_DIR="$SCRIPT_DIR/test_ods_env"
    mkdir -p "$ODS_DIR"
    touch "$ODS_DIR/docker-compose.yml"
    
    # Mock docker compose
    # We create a fake docker binary in PATH
    mkdir -p "$SCRIPT_DIR/bin"
    cat <<EOF > "$SCRIPT_DIR/bin/docker"
#!/bin/bash
if [[ "\$*" == *"compose down"* ]]; then
    echo "DOCKER_DOWN_CALLED"
fi
EOF
    chmod +x "$SCRIPT_DIR/bin/docker"
    # Do NOT overwrite PATH globally here, just for the command

    
    # We need a dummy backup to avoid early exit
    mkdir -p "$ODS_DIR/.backups/test-backup"
    echo '{"manifest_version": "1.0", "backup_type": "full"}' > "$ODS_DIR/.backups/test-backup/manifest.json"
    
    # Run restore with -f (force) to skip prompts, and a dummy ID
    # We capture output to see if "Stopping containers..." appears
    OUTPUT=$( (export PATH="$SCRIPT_DIR/bin:/usr/bin:/bin"; bash "$ODS_RESTORE" -f test-backup) 2>&1 )

    
    if echo "$OUTPUT" | grep -q "Stopping containers..."; then
        pass "Default restore triggers container stop"
    else
        fail "Default restore did NOT trigger container stop"
    fi
    
    # Cleanup
    rm -rf "$ODS_DIR" "$SCRIPT_DIR/bin"
}

echo ""
echo -e "${BLUE}━━━ ods-restore.sh Default Stop-First Test ━━━${NC}"
echo ""

test_default_stop_first

echo ""
pass "All tests passed"
