#!/bin/bash
# ============================================================================
# ODS Installer — GUI Progress Protocol
# ============================================================================
# Part of: installers/lib/
# Purpose: Emit structured progress events for the Tauri GUI installer
#
# Expects: ODS_INSTALLER_GUI (optional env var, set by Tauri)
# Provides: ods_progress()
#
# Modder notes:
#   When ODS_INSTALLER_GUI=1, progress lines are emitted to stdout in a
#   machine-readable format. When unset, this is a complete no-op.
#   Format: ODS_PROGRESS:<percent>:<phase_id>:<human_message>
# ============================================================================

ods_progress() {
  local percent="$1"
  local phase="$2"
  local message="$3"

  if [[ "${ODS_INSTALLER_GUI:-0}" == "1" ]]; then
    echo "ODS_PROGRESS:${percent}:${phase}:${message}"
  fi
}

# Emit an installed Portal endpoint only after successful, real Pixel setup.
# Callers resolve the published dashboard port from the installed configuration.
ods_portal_receipt() {
  local enabled="$1" port="$2" dry_run="$3"
  if [[ "${ODS_INSTALLER_GUI:-0}" == "1" && "$enabled" == "true" && "$dry_run" == "false" ]]; then
    if [[ "$port" =~ ^[0-9]{1,5}$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 )); then
      printf 'ODS_PORTAL_URL=http://localhost:%s/pixel\n' "$port"
    fi
  fi
}
