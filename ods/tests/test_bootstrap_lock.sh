#!/bin/bash
# Test for issue #4178: bootstrap-upgrade.sh duplicate lock should exit 1

SCRIPT="C:\Users\patil\DreamServer\ods\scripts\bootstrap-upgrade.sh"
INSTALL_DIR=$(mktemp -d)
trap 'rm -rf "$INSTALL_DIR"' EXIT

# Setup minimal environment
mkdir -p "$INSTALL_DIR/data"
touch "$INSTALL_DIR/.env"
mkdir -p "$INSTALL_DIR/config/llama-server"
touch "$INSTALL_DIR/config/llama-server/models.ini"

# Simulate another process holding the lock
# The lock dir is in /tmp/ods-bootstrap-upgrade-<hash>.lock
# We need to find the lock dir or simulate the lock acquire failure.
# Since we can't easily predict the hash, we'll run the script once in background.

echo "Starting first instance..."
# Use a dummy model and URL to avoid actual downloads
bash "$SCRIPT" "$INSTALL_DIR" "model.gguf" "http://example.com/model.gguf" "hash" "model" "2048" > /dev/null 2>&1 &
PID1=$!
sleep 2

echo "Starting second instance (should fail with exit 1)..."
bash "$SCRIPT" "$INSTALL_DIR" "model.gguf" "http://example.com/model.gguf" "hash" "model" "2048" > /dev/null 2>&1
EXIT_CODE=$?

kill $PID1 2>/dev/null || true

if [ $EXIT_CODE -eq 1 ]; then
    echo "SUCCESS: Duplicate lock returned exit 1"
else
    echo "FAILURE: Duplicate lock returned $EXIT_CODE"
    exit 1
fi

echo "Test passed!"
