#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
pixel_load_env
[[ -f "$PIXEL_GOOGLE_CLIENT_FILE" ]] || pixel_die "Place the Desktop OAuth JSON at $PIXEL_GOOGLE_CLIENT_FILE with mode 0600"
install -d -m 700 "$(dirname "$PIXEL_GOOGLE_TOKEN_PATH")"
chmod 600 "$PIXEL_GOOGLE_CLIENT_FILE"
node "$ROOT/plugin/authorize.mjs"
[[ -f "$PIXEL_GOOGLE_TOKEN_PATH" ]] || pixel_die "Authorization completed without a token file"
chmod 600 "$PIXEL_GOOGLE_TOKEN_PATH"
pixel_log "Google Workspace authorization staged at $PIXEL_GOOGLE_TOKEN_PATH; run ./pixel source-broker --confirm to migrate it into the isolated service identity"
