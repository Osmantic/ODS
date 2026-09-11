#!/bin/bash
set -euo pipefail

# Mock setup
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

# Create mock ODS structure
mkdir -p "$TEST_DIR/data/open-webui"
touch "$TEST_DIR/docker-compose.yml"
mkdir -p "$TEST_DIR/.backups/test-backup"
touch "$TEST_DIR/.backups/test-backup/manifest.json"
echo '{"manifest_version": "1.0", "backup_date": "2026-01-01", "backup_type": "full"}' > "$TEST_DIR/.backups/test-backup/manifest.json"
mkdir -p "$TEST_DIR/.backups/test-backup/data/open-webui"
touch "$TEST_DIR/.backups/test-backup/data/open-webui/testfile"

# Copy real lib/rsync.sh to mock dir to avoid source error
mkdir -p "$TEST_DIR/lib"
cp ods/lib/rsync.sh "$TEST_DIR/lib/rsync.sh"

# Mock docker compose to fail
mkdir -p "$TEST_DIR/bin"
cat << 'EOF' > "$TEST_DIR/bin/docker"
#!/bin/bash
if [[ "$*" == *"compose down"* ]]; then
    echo "Error: docker compose down failed" >&2
    exit 1
fi
# Force trigger stop_containers logic by returning a match for the ODS_DIR basename
if [[ "$*" == *"compose ls"* ]]; then
    echo "$(basename "$ODS_DIR")"
    exit 0
fi
exit 0
EOF
chmod +x "$TEST_DIR/bin/docker"

# Add mock bin to PATH
export PATH="$TEST_DIR/bin:$PATH"
export ODS_DIR="$TEST_DIR"

# Create a wrapper to run ods-restore.sh with the mocked environment
# We use -f to skip confirmation and a specific backup ID
# We force stop_first="true" (default)

echo "Running test: restore should abort if stop_containers fails..."
if bash ods/ods-restore.sh -f test-backup; then
    echo "FAIL: ods-restore.sh succeeded despite docker compose down failure"
    exit 1
else
    echo "SUCCESS: ods-restore.sh aborted as expected"
fi

# Verify no data was modified (the mock testfile should NOT be in ODS_DIR)
if [[ -f "$TEST_DIR/data/open-webui/testfile" ]]; then
    echo "FAIL: Data was restored despite container stop failure"
    exit 1
fi

echo "All gates passed."
