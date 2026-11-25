#!/bin/bash
# Migration: pre-PR-#1069 → SHIELD_API_KEY present
# Description: Backfill SHIELD_API_KEY when missing so the dashboard
#              Privacy Shield stats panel works after upgrade. Existing
#              installs predating PR #1069 have no SHIELD_API_KEY in .env;
#              without it dashboard-api's authenticated /stats proxy
#              short-circuits and the UI shows a config error.
# Date: 2026-05-01
# Idempotent: only writes when the key is absent or empty.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$SCRIPT_DIR/..}"
ENV_FILE="${INSTALL_DIR}/.env"

[[ -f "$ENV_FILE" ]] || { echo "Migration v2.4.1: no .env at $ENV_FILE — skipping"; exit 0; }

# Empty-or-missing check: matches both "key not present" and "key="
# Match the CLI and runtime's last-assignment-wins .env semantics.
existing=$(awk 'index($0, "SHIELD_API_KEY=") == 1 { value=substr($0,16) } END { print value }' "$ENV_FILE" | tr -d '\r')
if [[ -z "$existing" ]]; then
    # Validate entropy before touching the owner's environment file. od is
    # available on minimal installations where openssl and xxd may be absent.
    new_key=$(openssl rand -hex 32 2>/dev/null) || new_key=""
    if [[ ! "$new_key" =~ ^[0-9a-fA-F]{64}$ ]]; then
        new_key=$(od -An -N 32 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n') || new_key=""
    fi
    [[ "$new_key" =~ ^[0-9a-fA-F]{64}$ ]] || {
        echo "Cannot generate SHIELD_API_KEY; .env was not changed." >&2
        exit 1
    }
    if grep -qE '^SHIELD_API_KEY=' "$ENV_FILE" 2>/dev/null; then
        # Update empty value in place. Use awk to dodge sed delimiter pitfalls.
        awk -v v="$new_key" '
            { if (index($0, "SHIELD_API_KEY=") == 1) print "SHIELD_API_KEY=" v; else print }
        ' "$ENV_FILE" > "${ENV_FILE}.tmp" && cat "${ENV_FILE}.tmp" > "$ENV_FILE" && rm -f "${ENV_FILE}.tmp"
    else
        echo "" >> "$ENV_FILE"
        echo "# Privacy Shield cross-service auth (PR #1069)" >> "$ENV_FILE"
        echo "SHIELD_API_KEY=${new_key}" >> "$ENV_FILE"
    fi
    echo "Added SHIELD_API_KEY to .env"
fi

echo "Migration v2.4.1 complete"
