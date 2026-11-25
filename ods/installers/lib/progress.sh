#!/bin/bash
# ============================================================================
# ODS Installer — GUI Progress Protocol
# ============================================================================
# Part of: installers/lib/
# Purpose: Emit structured progress events for a graphical front end
#          (none ships today; the Tauri desktop installer was removed)
#
# Expects: ODS_INSTALLER_GUI (optional env var, set by such a front end)
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
