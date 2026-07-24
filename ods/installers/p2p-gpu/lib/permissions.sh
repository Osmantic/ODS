#!/usr/bin/env bash
# ============================================================================
# ODS — P2P GPU Permission System
# ============================================================================
# Part of: ods/installers/p2p-gpu/lib/
# Purpose: POSIX ACLs, setgid, UID-specific ownership, data dir scaffolding
#
# Expects: ODS_USER, ODS_HOME, LOGFILE, log(), warn(), err()
# Provides: ensure_acl_tools(), apply_data_acl(), apply_multi_uid_perms(),
#           fix_known_uid_requirements(), precreate_extension_data_dirs(),
#           configure_dream_umask(), create_permission_fix_script()
#
# Modder notes:
#   Three-layer permission system:
#     1. POSIX ACLs with default entries on data/
#     2. Setgid bit (2775) on directories
#     3. Known UID overrides for services that check ownership at startup
#
#   [FIX: shared-acl] Permission strategy:
#     - Primary: setgid (2775) + POSIX ACLs → group-based access
#     - Shared dirs get explicit per-UID ACLs for the writers we know about
#     - setfacl is required; fail fast when unavailable
#
#   Error handling — two tiers:
#     HARD-FAIL (exit 1): setfacl application, acl package install,
#       primary chown/chmod on data dirs — if these fail the stack
#       cannot start safely.
#     WARN-AND-CONTINUE (|| warn): service-specific chown for individual
#       extensions (qdrant, whisper, dashboard-api) — one service failing
#       ownership should not prevent the other 16 from starting. Also used
#       for UID extraction (parse helper) and generated repair scripts
#       (which should fix as much as possible per run).
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

# Install ACL tools if missing
ensure_acl_tools() {
  if ! command -v setfacl &>/dev/null; then
    if ! apt-get install -y -qq acl 2>>"$LOGFILE"; then
      err "Failed to install acl package — setfacl is required. Install manually: apt-get install acl"
      exit 1
    fi
  fi
}

# Apply POSIX ACLs + setgid so every container UID can coexist.
# This is the PRIMARY permission mechanism — covers most services.
apply_data_acl() {
  local dir="$1"
  [[ ! -d "$dir" ]] && return 0

  if ! chown -R "${ODS_USER}:${ODS_USER}" "$dir"; then
    err "chown failed on ${dir} — cannot set base ownership for data directory"
    exit 1
  fi
  if ! find "$dir" -type d -exec chmod 2775 {} +; then
    err "chmod dirs failed on ${dir} — cannot set setgid on data directories"
    exit 1
  fi
  if ! find "$dir" -type f -exec chmod 0664 {} +; then
    err "chmod files failed on ${dir} — cannot set group-writable on data files"
    exit 1
  fi

  if ! command -v setfacl &>/dev/null; then
    err "setfacl unavailable — install with: apt-get install acl"
    exit 1
  fi

  # dashboard-api runs as uid 1000 (dreamer) and needs write access to /data
  # for .extensions-lock and token_counter.json.
  if ! setfacl -R -d -m "u::rwx,u:1000:rwx,g::rwx,o::rx" "$dir"; then
    err "Failed to apply default ACLs on ${dir} — mount may be ACL-incompatible"
    exit 1
  fi
  if ! setfacl -R -m "u:1000:rwx,g::rwx" "$dir"; then
    err "Failed to apply current ACLs on ${dir} — mount may be ACL-incompatible"
    exit 1
  fi
  log "Applied POSIX ACLs on ${dir}"
}

# [FIX: shared-acl] Apply explicit ACLs to directories with multiple writers.
# The caller must name the additional UIDs that need write access.
apply_multi_uid_perms() {
  local dir="$1" reason="$2"
  shift 2
  [[ ! -d "$dir" ]] && return 0

  if ! chown -R "${ODS_USER}:${ODS_USER}" "$dir"; then
    err "chown failed on ${dir} — cannot set base ownership for shared directory"
    exit 1
  fi
  if ! find "$dir" -type d -exec chmod 2775 {} +; then
    err "chmod dirs failed on ${dir} — cannot set setgid on shared directories"
    exit 1
  fi
  if ! find "$dir" -type f -exec chmod 0664 {} +; then
    err "chmod files failed on ${dir} — cannot set group-writable on shared files"
    exit 1
  fi

  if ! command -v setfacl &>/dev/null; then
    err "setfacl unavailable — install with: apt-get install acl"
    exit 1
  fi

  local acl_suffix=""
  if [[ $# -gt 0 ]]; then
    acl_suffix=",$*"
  fi

  if ! setfacl -R -d -m "u::rwx,g::rwx,o::rx${acl_suffix}" "$dir"; then
    err "Failed to apply shared default ACLs on ${dir} — mount may be ACL-incompatible"
    exit 1
  fi
  if ! setfacl -R -m "u::rwx,g::rwx${acl_suffix}" "$dir"; then
    err "Failed to apply shared current ACLs on ${dir} — mount may be ACL-incompatible"
    exit 1
  fi
  log "Applied shared ACLs on ${dir} (reason: ${reason})"
}

# Extract numeric UID from a compose.yaml user: directive
_extract_compose_uid() {
  local compose_file="$1"
  [[ ! -f "$compose_file" ]] && return 0
  # [NON-FATAL: discovery] One bad compose file should not block others.
  python3 -c "
import yaml, re, sys
try:
    data = yaml.safe_load(open(sys.argv[1]))
    services = data.get('services') or {}
    for sdef in services.values():
        user = str(sdef.get('user', ''))
        if not user: continue
        resolved = re.sub(r'\\\$\{[A-Za-z_]+:-(\d+)\}', r'\1', user)
        uid = resolved.split(':')[0].strip()
        if uid.isdigit():
            print(uid)
            break
except yaml.YAMLError as e:
    print(f'YAML parse error in {sys.argv[1]}: {e}', file=sys.stderr)
except OSError as e:
    print(f'File read error {sys.argv[1]}: {e}', file=sys.stderr)
" "$compose_file" || warn "UID extraction failed for ${compose_file} (non-fatal)"
}

# Fix UID-specific ownership that ACLs alone don't solve
fix_known_uid_requirements() {
  local data_dir="$1"
  local gpu_backend="${2:-nvidia}"
  local ds_dir
  ds_dir=$(dirname "$data_dir")

  _fix_dynamic_uids "$ds_dir" "$data_dir"
  _fix_uid_exceptions "$data_dir" "$gpu_backend"

  log "Fixed UID-specific ownership for services (dynamic + exceptions)"
}

_fix_dynamic_uids() {
  local ds_dir="$1" data_dir="$2"
  local ext_dirs=("${ds_dir}/extensions/services" "${ds_dir}/user-extensions")
  local dream_uid
  dream_uid=$(id -u "$ODS_USER" 2>>"$LOGFILE" || echo "")
  for ext_root in "${ext_dirs[@]}"; do
    [[ ! -d "$ext_root" ]] && continue
    for ext_path in "${ext_root}"/*/; do
      [[ ! -d "$ext_path" ]] && continue
      local ext_name
      ext_name=$(basename "$ext_path")
      local ext_data="${data_dir}/${ext_name}"
      local compose_file=""
      for candidate in "${ext_path}compose.yaml" "${ext_path}compose.yml"; do
        [[ -f "$candidate" ]] && compose_file="$candidate" && break
      done
      [[ -z "$compose_file" ]] && continue
      local uid
      uid=$(_extract_compose_uid "$compose_file")
      if [[ -n "$uid" && "$uid" != "0" ]]; then
        mkdir -p "$ext_data"
        # best-effort: one extension failing ownership should not block others
        if [[ -n "$dream_uid" && "$uid" == "$dream_uid" ]]; then
          continue
        fi
        if ! chown -R "${uid}:${uid}" "$ext_data" 2>>"$LOGFILE"; then
          warn "chown ${ext_name} to uid ${uid} failed (non-fatal) — attempting ACL fallback"
          if command -v setfacl &>/dev/null; then
            # [NON-FATAL: ${ext_name}] Individual service failure does not block others.
            setfacl -R -m "u:${uid}:rwx" "$ext_data" 2>>"$LOGFILE" \
              || warn "setfacl ${ext_name} uid ${uid} failed (non-fatal)"
            # [NON-FATAL: ${ext_name}] Individual service failure does not block others.
            setfacl -R -d -m "u:${uid}:rwx" "$ext_data" 2>>"$LOGFILE" \
              || warn "setfacl default ${ext_name} uid ${uid} failed (non-fatal)"
          fi
        fi
      fi
    done
  done
}

_fix_uid_exceptions() {
  local data_dir="$1" gpu_backend="$2"

  # qdrant: uid 1000, no user: in compose.yaml — explicit chown required
  if [[ -d "${data_dir}/qdrant" ]]; then
    # best-effort: qdrant-specific ownership — does not block other services
    # [NON-FATAL: qdrant] Individual service failure does not block others.
    chown -R 1000:1000 "${data_dir}/qdrant" || warn "qdrant ownership fix failed (non-fatal)"
  fi

  # searxng: uid varies by image version (977 or 1000) — grant both known UIDs
  if [[ -d "${data_dir}/searxng" ]]; then
    apply_multi_uid_perms "${data_dir}/searxng" "uid varies by image version (977/1000)" "u:977:rwx,u:1000:rwx"
  fi

  # comfyui: AMD vs NVIDIA layout
  fix_comfyui_permissions "$data_dir" "$gpu_backend"

  # open-webui: grant both root (container) and uid 1000 (dream/dashboard-api)
  if [[ -d "${data_dir}/open-webui" ]]; then
    if ! setfacl -R -d -m "u::rwx,u:0:rwx,u:1000:rwx,g::rwx,o::rx" "${data_dir}/open-webui"; then
      err "Failed to apply default ACLs on ${data_dir}/open-webui — mount may be ACL-incompatible"
      exit 1
    fi
    if ! setfacl -R -m "u:0:rwx,u:1000:rwx,g::rwx" "${data_dir}/open-webui"; then
      err "Failed to apply ACLs on ${data_dir}/open-webui — mount may be ACL-incompatible"
      exit 1
    fi
  fi

  # whisper: grant known writers uid 1000 + root for cache/bootstrap flows
  if [[ -d "${data_dir}/whisper" ]]; then
    # best-effort: whisper ownership — ACLs above enforce access regardless
    # [NON-FATAL: whisper] Individual service failure does not block others.
    chown -R 1000:1000 "${data_dir}/whisper" || warn "whisper chown failed (non-fatal)"
    if ! setfacl -R -d -m "u::rwx,u:0:rwx,u:1000:rwx,g::rwx,o::rx" "${data_dir}/whisper"; then
      err "Failed to apply default ACLs on ${data_dir}/whisper — mount may be ACL-incompatible"
      exit 1
    fi
    if ! setfacl -R -m "u:0:rwx,u:1000:rwx,g::rwx" "${data_dir}/whisper"; then
      err "Failed to apply ACLs on ${data_dir}/whisper — mount may be ACL-incompatible"
      exit 1
    fi
  fi

  # dashboard-api: uid 1000 (dreamer) — needs rw on data/ and .env
  local ds_dir
  ds_dir=$(dirname "$data_dir")
  if [[ -d "${data_dir}/dashboard-api" ]]; then
    # best-effort: dashboard-api ownership — service starts as uid 1000 regardless
    # [NON-FATAL: dashboard-api] Individual service failure does not block others.
    chown -R 1000:1000 "${data_dir}/dashboard-api" || warn "dashboard-api chown failed (non-fatal)"
  fi
  if command -v setfacl &>/dev/null && [[ -f "${ds_dir}/.env" ]]; then
    if ! setfacl -m u:1000:rw "${ds_dir}/.env"; then
      err "Failed to apply ACL on ${ds_dir}/.env for dashboard-api"
      exit 1
    fi
  fi

  # models (shared): grant the non-root writer used by the p2p-gpu toolkit
  if [[ -d "${data_dir}/models" ]]; then
    apply_multi_uid_perms "${data_dir}/models" "multi-service write: llama-server, comfyui, aria2c" "u:1000:rwx"
  fi
}

# Pre-create data directories for all known extensions
precreate_extension_data_dirs() {
  local ds_dir="$1"
  local data_dir="${ds_dir}/data"
  local ext_dirs=("${ds_dir}/extensions/services" "${ds_dir}/user-extensions")

  for ext_root in "${ext_dirs[@]}"; do
    [[ ! -d "$ext_root" ]] && continue
    for manifest in "${ext_root}"/*/manifest.yaml; do
      [[ ! -f "$manifest" ]] && continue
      local ext_name
      ext_name=$(basename "$(dirname "$manifest")")
      mkdir -p "${data_dir}/${ext_name}"
    done
  done

  # Pre-create ComfyUI bind-mount paths so Docker doesn't auto-create root-owned
  # 0755 directories that are unwritable for the non-root comfyui user.
  mkdir -p "${data_dir}/comfyui/models" \
    "${data_dir}/comfyui/models/checkpoints" \
    "${data_dir}/comfyui/output" \
    "${data_dir}/comfyui/input" \
    "${data_dir}/comfyui/workflows" \
    "${data_dir}/comfyui/ComfyUI/models" \
    "${data_dir}/comfyui/ComfyUI/output" \
    "${data_dir}/comfyui/ComfyUI/input" \
    "${data_dir}/comfyui/ComfyUI/custom_nodes"

  # [NON-FATAL: extensions] Optional user-extensions directory.
  mkdir -p "${ds_dir}/user-extensions" || warn "could not create user-extensions (non-fatal)"
  log "Pre-created data directories for all known extensions"
}

# Set dream user's umask for group-writable files
configure_dream_umask() {
  for f in "${ODS_HOME}/.bashrc" "${ODS_HOME}/.profile"; do
    if [[ -f "$f" ]] && ! grep -q 'umask 0002' "$f"; then
      printf '\n# ODS: group-writable files by default\numask 0002\n' >> "$f"
    fi
  done
}

# Generate standalone permission-fix script
create_permission_fix_script() {
  local ds_dir="$1"
  local uid_fix_lines=""

  local ext_dirs=("${ds_dir}/extensions/services" "${ds_dir}/user-extensions")
  for ext_root in "${ext_dirs[@]}"; do
    [[ ! -d "$ext_root" ]] && continue
    for ext_path in "${ext_root}"/*/; do
      [[ ! -d "$ext_path" ]] && continue
      local ext_name
      ext_name=$(basename "$ext_path")
      for candidate in "${ext_path}compose.yaml" "${ext_path}compose.yml"; do
        [[ ! -f "$candidate" ]] && continue
        local uid
        uid=$(_extract_compose_uid "$candidate")
        if [[ -n "$uid" && "$uid" != "0" ]]; then
          # [NON-FATAL: fix-script] Generated fixer is best-effort by design.
          uid_fix_lines+="[[ -d \"\${DATA_DIR}/${ext_name}\" ]] && chown -R ${uid}:${uid} \"\${DATA_DIR}/${ext_name}\" || warn \"${ext_name} chown failed (non-fatal)\""$'\n'
        fi
        break
      done
    done
  done

  mkdir -p "${ds_dir}/scripts"
  cat > "${ds_dir}/scripts/fix-permissions.sh" << PERMFIX_EOF
#!/usr/bin/env bash
set -euo pipefail
# ODS permission fixer — auto-generated, safe to run anytime.
SCRIPT_DIR="\$(cd "\$(dirname "\$0")/.." && pwd)"
DATA_DIR="\${SCRIPT_DIR}/data"
warn() { echo -e "\033[1;33m[!]\033[0m \$*" >&2; }

echo "[*] Fixing permissions on \${DATA_DIR}..."

if ! command -v setfacl &>/dev/null; then
  echo "[x] setfacl unavailable — install with: apt-get install acl" >&2
  exit 1
fi

find "\$DATA_DIR" -type d -exec chmod 2775 {} + || warn "chmod dirs failed (non-fatal)"
find "\$DATA_DIR" -type f -exec chmod 0664 {} + || warn "chmod files failed (non-fatal)"
if ! setfacl -R -d -m "u::rwx,u:1000:rwx,g::rwx,o::rx" "\$DATA_DIR"; then
  echo "[x] Failed to apply default ACLs on \$DATA_DIR — mount may be ACL-incompatible" >&2
  exit 1
fi
if ! setfacl -R -m "u:1000:rwx,g::rwx" "\$DATA_DIR"; then
  echo "[x] Failed to apply current ACLs on \$DATA_DIR — mount may be ACL-incompatible" >&2
  exit 1
fi

${uid_fix_lines}
[[ -d "\${DATA_DIR}/qdrant" ]] && chown -R 1000:1000 "\${DATA_DIR}/qdrant" || warn "qdrant fix failed (non-fatal)"
if [[ -d "\${DATA_DIR}/open-webui" ]]; then
  if ! setfacl -R -d -m "u::rwx,u:0:rwx,u:1000:rwx,g::rwx,o::rx" "\${DATA_DIR}/open-webui"; then
    echo "[x] Failed to apply ACLs — mount may be ACL-incompatible" >&2
    exit 1
  fi
  if ! setfacl -R -m "u:0:rwx,u:1000:rwx,g::rwx" "\${DATA_DIR}/open-webui"; then
    echo "[x] Failed to apply ACLs — mount may be ACL-incompatible" >&2
    exit 1
  fi
fi
[[ -d "\${DATA_DIR}/whisper" ]] && chown -R 1000:1000 "\${DATA_DIR}/whisper" || warn "whisper chown failed (non-fatal)"
if [[ -d "\${DATA_DIR}/whisper" ]]; then
  if ! setfacl -R -d -m "u::rwx,u:0:rwx,u:1000:rwx,g::rwx,o::rx" "\${DATA_DIR}/whisper"; then
    echo "[x] Failed to apply ACLs — mount may be ACL-incompatible" >&2
    exit 1
  fi
  if ! setfacl -R -m "u:0:rwx,u:1000:rwx,g::rwx" "\${DATA_DIR}/whisper"; then
    echo "[x] Failed to apply ACLs — mount may be ACL-incompatible" >&2
    exit 1
  fi
fi
# Multi-UID directories: searxng (uid varies), models (non-root writer)
if [[ -d "\${DATA_DIR}/searxng" ]]; then
  if ! setfacl -R -d -m "u::rwx,u:977:rwx,u:1000:rwx,g::rwx,o::rx" "\${DATA_DIR}/searxng"; then
    echo "[x] Failed to apply ACLs — mount may be ACL-incompatible" >&2
    exit 1
  fi
  if ! setfacl -R -m "u:977:rwx,u:1000:rwx,g::rwx" "\${DATA_DIR}/searxng"; then
    echo "[x] Failed to apply ACLs — mount may be ACL-incompatible" >&2
    exit 1
  fi
fi
if [[ -d "\${DATA_DIR}/models" ]]; then
  if ! setfacl -R -d -m "u::rwx,u:1000:rwx,g::rwx,o::rx" "\${DATA_DIR}/models"; then
    echo "[x] Failed to apply ACLs — mount may be ACL-incompatible" >&2
    exit 1
  fi
  if ! setfacl -R -m "u:1000:rwx,g::rwx" "\${DATA_DIR}/models"; then
    echo "[x] Failed to apply ACLs — mount may be ACL-incompatible" >&2
    exit 1
  fi
fi

for d in \
  "\${DATA_DIR}/comfyui/models" \
  "\${DATA_DIR}/comfyui/models/checkpoints" \
  "\${DATA_DIR}/comfyui/output" \
  "\${DATA_DIR}/comfyui/input" \
  "\${DATA_DIR}/comfyui/workflows" \
  "\${DATA_DIR}/comfyui/ComfyUI/models" \
  "\${DATA_DIR}/comfyui/ComfyUI/output" \
  "\${DATA_DIR}/comfyui/ComfyUI/input" \
  "\${DATA_DIR}/comfyui/ComfyUI/custom_nodes"; do
  mkdir -p "\$d" || warn "comfyui mkdir failed on \$d (non-fatal)"
  [[ -d "\$d" ]] && chmod 2775 "\$d" || warn "comfyui dir mode fix failed on \$d (non-fatal)"
done

find "\${SCRIPT_DIR}/scripts" -name "*.sh" -exec chmod +x {} + || warn "scripts chmod failed (non-fatal)"
echo "[✓] Permissions fixed"
PERMFIX_EOF

  chmod +x "${ds_dir}/scripts/fix-permissions.sh"
  log "Created reusable permission fixer: ${ds_dir}/scripts/fix-permissions.sh"
}
