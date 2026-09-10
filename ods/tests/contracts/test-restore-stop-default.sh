#!/bin/bash
set -euo pipefail

# Mock ODS environment
ODS_DIR=$(mktemp -d)
export ODS_DIR
trap 'rm -rf "$ODS_DIR"' EXIT

# Create required library and marker files
mkdir -p "$ODS_DIR/lib"
cat << 'LIB' > "$ODS_DIR/lib/rsync.sh"
rsync_with_progress() {
    echo "Mock: rsyncing \$1 to \$2"
}
export -f rsync_with_progress
LIB

touch "$ODS_DIR/docker-compose.yml"

# Mock docker compose
docker() {
    if [[ "$1" == "compose" && "$2" == "down" ]]; then
        echo "Mock: containers stopped"
        return 0
    fi
    return 0
}
export -f docker

# Create a dummy backup
BACKUP_ROOT="$ODS_DIR/.backups"
mkdir -p "$BACKUP_ROOT/test-backup"
echo '{"manifest_version": "1.0", "backup_type": "full"}' > "$BACKUP_ROOT/test-backup/manifest.json"
mkdir -p "$BACKUP_ROOT/test-backup/data/open-webui"
touch "$BACKUP_ROOT/test-backup/data/open-webui/test.txt"

# Run restore without -s
OUTPUT=$(ODS_DIR="$ODS_DIR" bash ods/ods-restore.sh -f test-backup 2>&1)

if echo "$OUTPUT" | grep -q "Stopping containers..."; then
    echo "SUCCESS: stop_first is true by default"
    exit 0
else
    echo "FAILURE: stop_first was not triggered by default"
    echo "Output: $OUTPUT"
    exit 1
fi
