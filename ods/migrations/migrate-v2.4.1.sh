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
existing=$(grep -E '^SHIELD_API_KEY=' "$ENV_FILE" 2>/dev/null | sed -n '1p' | cut -d= -f2- | tr -d '\r' || true)
if [[ -z "$existing" ]]; then
    new_key=$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p | tr -d '\n')
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
