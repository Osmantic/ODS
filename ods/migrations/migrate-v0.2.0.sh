#!/bin/bash
# Migration: v0.1.0 → v0.2.0
# Description: Add new voice configuration variables
# Date: 2026-02-11

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$SCRIPT_DIR/..}"
ENV_FILE="${INSTALL_DIR}/.env"

# Add new voice-related environment variables if not present
if [[ -f "$ENV_FILE" ]]; then
    # Check if ENABLE_VOICE exists
    if ! grep -q "^ENABLE_VOICE=" "$ENV_FILE"; then
        echo "" >> "$ENV_FILE"
        echo "# Voice Services (v0.2.0+)" >> "$ENV_FILE"
        echo "ENABLE_VOICE=true" >> "$ENV_FILE"
        echo "Added ENABLE_VOICE to .env"
    fi
    
    # Check if VOICE_PROFILE exists
    if ! grep -q "^VOICE_PROFILE=" "$ENV_FILE"; then
        echo "VOICE_PROFILE=voice" >> "$ENV_FILE"
        echo "Added VOICE_PROFILE to .env"
    fi
fi

echo "Migration v0.2.0 complete"
