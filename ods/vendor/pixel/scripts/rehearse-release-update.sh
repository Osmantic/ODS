#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
pixel_load_env
pixel_require_linux
pixel_acquire_deployment_lock exclusive
staging_root="$PIXEL_INSTALL_DIR/update-staging"
pixel_safe_absolute_dir "$staging_root" PIXEL_UPDATE_STAGING_ROOT
exec python3 "$ROOT/scripts/release-update.py" rehearse "$@" --staging-root "$staging_root"
