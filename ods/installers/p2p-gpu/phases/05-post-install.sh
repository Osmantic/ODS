#!/usr/bin/env bash
# ============================================================================
# ODS — Vast.ai Phase 05: Post-Install Fixes
# ============================================================================
# Part of: p2p-gpu/phases/
# Purpose: Locate active ods directory, apply all post-install fixes
#
# Expects: ODS_HOME, REPO_DIR, GPU_BACKEND, ODS_USER,
#          log(), warn(), err(), find_dream_dir(), fix_ownership(),
#          apply_post_install_fixes()
# Provides: ODS_DIR (active ods path)
#
# Fixes covered: #03 (/tmp), #04 (CPU overflow), #05 (n8n uid), #06 (dashboard-api),
#                #07 (comfyui write), #08 (WEBUI_SECRET), #15 (.env dupes)
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

step "Phase 5/12: Locating directory & applying fixes"

ODS_DIR=$(find_dream_dir) || {
  err "Could not find ods directory after install"
  err "Expected at: ${ODS_HOME}/ods or ${REPO_DIR}/ods"
  exit 1
}

log "Active directory: ${ODS_DIR}"
fix_ownership "$ODS_DIR" "$ODS_USER"

apply_post_install_fixes "$ODS_DIR" "$GPU_BACKEND"

# Fix secondary directory if dual-install occurred
alt_dir=""
if [[ "$ODS_DIR" == "${ODS_HOME}/ods" && -d "${REPO_DIR}/ods" ]]; then
  alt_dir="${REPO_DIR}/ods"
elif [[ "$ODS_DIR" == "${REPO_DIR}/ods" && -d "${ODS_HOME}/ods" ]]; then
  alt_dir="${ODS_HOME}/ods"
fi

if [[ -n "$alt_dir" && -f "${alt_dir}/.env" ]]; then
  apply_post_install_fixes "$alt_dir" "$GPU_BACKEND"
  log "Also fixed secondary directory: ${alt_dir}"
fi

# Cap llama-server context based on GPU VRAM budget
_cap_context_for_vram "$ODS_DIR"

# -- Ensure data/persona/SOUL.md exists ------------------------------------
# Hermes compose bind-mounts this file. If missing, Docker creates it as a
# directory -> container crashes with "not a directory" error.
_ensure_persona_file() {
  local ds_dir="$1"
  local persona_file="${ds_dir}/data/persona/SOUL.md"
  local template="${ds_dir}/extensions/services/hermes/SOUL.md.template"

  if [[ -f "$persona_file" ]]; then
    return 0
  fi

  mkdir -p "${ds_dir}/data/persona"

  # If Docker already created it as a directory, remove it
  if [[ -d "$persona_file" ]]; then
    log "Removing Docker-created directory at ${persona_file}"
    # [NON-FATAL: cleanup] Best-effort cleanup; template fallback still works.
    rm -rf "$persona_file" 2>>"$LOGFILE" || warn "Could not remove directory at ${persona_file} (non-fatal)"
  fi

  # Try rendering via upstream script first
  local context_script="${ds_dir}/scripts/build-installation-context.py"
  if [[ -x "$context_script" ]] && command -v python3 &>/dev/null; then
    if su - "$ODS_USER" -c "cd ${ds_dir} && python3 scripts/build-installation-context.py" \
      >> "$LOGFILE" 2>&1; then
      if [[ -f "$persona_file" ]]; then
        log "Persona file rendered via build-installation-context.py"
        return 0
      fi
    else
      warn "build-installation-context.py failed (non-fatal) - using template"
    fi
  fi

  # Fallback: copy template directly
  if [[ -f "$template" ]]; then
    cp "$template" "$persona_file"
    chown "${ODS_USER}:${ODS_USER}" "$persona_file"
    log "Persona file created from template at ${persona_file}"
  else
    # Last resort: create minimal placeholder so the mount does not fail
    cat > "$persona_file" << 'SOUL_EOF'
# ODS Persona
You are Dream, a helpful AI assistant powered by ODS.
SOUL_EOF
    chown "${ODS_USER}:${ODS_USER}" "$persona_file"
    log "Minimal persona placeholder created at ${persona_file}"
  fi

  # Final verification - if still not a regular file, something is wrong
  if [[ ! -f "$persona_file" ]]; then
    warn "SOUL.md is still not a regular file at ${persona_file} - hermes container will fail to mount"
    warn "Manual fix: rm -rf ${persona_file} && cp ${template} ${persona_file}"
  fi
}

_ensure_persona_file "$ODS_DIR"

# Ensure llama-server config mount points are regular files, not Docker-created directories
_ensure_mount_files() {
  local ds_dir="$1"
  local models_ini="${ds_dir}/config/llama-server/models.ini"

  # models.ini - llama-server bind mount
  if [[ -d "$models_ini" ]]; then
    log "Removing Docker-created directory at ${models_ini}"
    rm -rf "$models_ini"
  fi
  if [[ ! -f "$models_ini" ]]; then
    mkdir -p "${ds_dir}/config/llama-server"
    touch "$models_ini"
    chown "${ODS_USER}:${ODS_USER}" "$models_ini"
    log "Created empty ${models_ini}"
  fi
}

_ensure_mount_files "$ODS_DIR"
