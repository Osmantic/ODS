#!/bin/bash
# ods-update.sh - ODS Update Manager
#
# Commands:
#   check      - Check for updates against GitHub releases
#   status     - Show current version, install path, last check
#   backup     - Backup compose files, .env, and version state
#   update     - Pull new version, run migrations, restart services
#   rollback   - Restore from last backup
#   changelog  - Show version changelog
#   health     - Run health checks on all services

set -euo pipefail

# Prerequisites
command -v jq >/dev/null 2>&1 || { echo "Error: jq is required but not installed." >&2; echo "Install with: apt install jq (Debian/Ubuntu) or brew install jq (macOS)" >&2; exit 1; }

#==============================================================================
# CONFIGURATION
#==============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${SCRIPT_DIR}"
VERSION_FILE="${INSTALL_DIR}/.version"
BACKUP_DIR="${HOME}/.ods/backups"
ROLLBACK_DIR="${INSTALL_DIR}/data/backups"   # pre-update rollback snapshots live here
MAX_BACKUPS="${MAX_BACKUPS:-10}"
UPDATE_CHANNEL="${UPDATE_CHANNEL:-stable}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"
GITHUB_REPO="${GITHUB_REPO:-Osmantic/ODS}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Prerequisites check
command -v jq >/dev/null 2>&1 || { echo -e "${RED}Error: jq is required but not installed.${NC}" >&2; echo "Install with: apt install jq (Debian/Ubuntu) or brew install jq (macOS)" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo -e "${RED}Error: curl is required but not installed.${NC}" >&2; exit 1; }

#==============================================================================
# HELPER FUNCTIONS
#==============================================================================

log_info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

get_current_version() {
    if [[ -f "$VERSION_FILE" ]]; then
        jq -r '.version // "0.0.0"' "$VERSION_FILE" 2>/dev/null || echo "0.0.0"
    else
        echo "0.0.0"
    fi
}

env_file_value() {
    local key="$1"
    [[ -f "${INSTALL_DIR}/.env" ]] || return 0
    awk -F= -v key="$key" '
        $1 == key {
            value = substr($0, index($0, "=") + 1)
            gsub(/\r$/, "", value)
            gsub(/^["'\'']|["'\'']$/, "", value)
            print value
            exit
        }
    ' "${INSTALL_DIR}/.env" 2>/dev/null || true
}

compose_flags_files_exist() {
    local flags="$1"
    local prev="" tok
    for tok in $flags; do
        if [[ "$prev" == "-f" ]]; then
            if [[ "$tok" = /* ]]; then
                [[ -f "$tok" ]] || return 1
            else
                [[ -f "${INSTALL_DIR}/${tok}" ]] || return 1
            fi
        fi
        prev="$tok"
    done
    return 0
}

resolve_compose_flags() {
    local cached=""
    if [[ -f "${INSTALL_DIR}/.compose-flags" ]]; then
        cached="$(tr '\n' ' ' < "${INSTALL_DIR}/.compose-flags" | xargs 2>/dev/null || true)"
        if [[ -n "$cached" ]] && compose_flags_files_exist "$cached"; then
            echo "$cached"
            return 0
        fi
        log_warn "Cached compose flags are missing or stale; trying dynamic compose resolution." >&2
    fi

    local gpu_backend tier gpu_count ods_mode
    gpu_backend="$(env_file_value GPU_BACKEND)"
    tier="$(env_file_value TIER)"
    gpu_count="$(env_file_value GPU_COUNT)"
    ods_mode="$(env_file_value ODS_MODE)"

    local resolved=""
    if [[ -x "${INSTALL_DIR}/scripts/resolve-compose-stack.sh" ]]; then
        resolved=$(bash "${INSTALL_DIR}/scripts/resolve-compose-stack.sh" \
            --script-dir "$INSTALL_DIR" \
            --tier "${tier:-1}" \
            --gpu-backend "${gpu_backend:-nvidia}" \
            --gpu-count "${gpu_count:-1}" \
            --ods-mode "${ods_mode:-local}" | tail -1) || resolved=""
        if [[ -n "$resolved" ]] && compose_flags_files_exist "$resolved"; then
            echo "$resolved"
            return 0
        fi
    fi

    if [[ -f "${INSTALL_DIR}/docker-compose.yml" ]]; then
        echo "-f docker-compose.yml"
        return 0
    fi

    if [[ -f "${INSTALL_DIR}/docker-compose.base.yml" ]]; then
        local fallback="-f docker-compose.base.yml"
        case "${gpu_backend:-}" in
            nvidia|amd|cpu|apple|intel|sycl)
                if [[ -f "${INSTALL_DIR}/docker-compose.${gpu_backend}.yml" ]]; then
                    fallback="$fallback -f docker-compose.${gpu_backend}.yml"
                fi
                ;;
        esac
        echo "$fallback"
        return 0
    fi

    return 1
}

is_git_checkout() {
    command -v git >/dev/null 2>&1 || return 1
    git -C "${INSTALL_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 1

    local prefix top
    top=$(git -C "${INSTALL_DIR}" rev-parse --show-toplevel 2>/dev/null || return 1)
    prefix=$(git -C "${INSTALL_DIR}" rev-parse --show-prefix 2>/dev/null || return 1)
    git -C "${top}" ls-files --error-unmatch "${prefix}ods-update.sh" >/dev/null 2>&1
}

runtime_update_guidance() {
    log_info "For routine runtime/image updates, run: cd \"${INSTALL_DIR}\" && ./ods-cli update"
    log_info "For source-code updates, reinstall or run this command from a git-backed ODS source checkout."
}

ensure_source_checkout_for_update() {
    if ! command -v git >/dev/null 2>&1; then
        log_error "git is required for ods-update.sh source-code updates."
        runtime_update_guidance
        return 1
    fi

    if ! is_git_checkout; then
        log_error "ods-update.sh update only works from a git-backed ODS source checkout."
        log_info "Install path: ${INSTALL_DIR}"
        runtime_update_guidance
        log_info "No files, services, or rollback snapshots were changed."
        return 1
    fi
}

# Semver compare: returns 0 if equal, 1 if v1 > v2, 2 if v1 < v2
semver_compare() {
    local v1="${1#v}"
    local v2="${2#v}"
    
    if [[ "$v1" == "$v2" ]]; then
        return 0
    fi
    
    local IFS='.'
    local i v1_parts=($v1) v2_parts=($v2)
    
    for ((i=0; i<3; i++)); do
        local n1="${v1_parts[$i]:-0}"
        local n2="${v2_parts[$i]:-0}"
        # Strip any non-numeric suffix
        n1="${n1%%[!0-9]*}"
        n2="${n2%%[!0-9]*}"
        
        if ((n1 > n2)); then
            return 1
        elif ((n1 < n2)); then
            return 2
        fi
    done
    return 0
}

# _prune_rollback_snapshots
#   Removes oldest pre-update snapshots beyond MAX_BACKUPS.
#   Guards against misconfigured ROLLBACK_DIR before any rm -rf.
_prune_rollback_snapshots() {
    if [[ -z "$ROLLBACK_DIR" || "$ROLLBACK_DIR" != */data/backups ]]; then
        log_warn "ROLLBACK_DIR '${ROLLBACK_DIR}' does not end in /data/backups; skipping prune." >&2
        return 0
    fi
    [[ -d "$ROLLBACK_DIR" ]] || return 0
    local count=0
    while IFS= read -r old_snap; do
        count=$(( count + 1 ))
        if (( count > MAX_BACKUPS )); then
            log_info "Pruning old rollback snapshot: $(basename "$old_snap")" >&2
            rm -rf "$old_snap"
        fi
    done < <(find "${ROLLBACK_DIR}" -maxdepth 1 -type d -name "pre-update-*" | sort -r)
}

# snapshot_pre_update <timestamp>
#   Creates data/backups/pre-update-<timestamp>/ and copies:
#     • .env and .env.* variants
#     • docker-compose*.yml overlays (tracks active stack)
#     • config/{litellm,n8n,openclaw,searxng}/ (per-extension config)
#     • .version
#   Validates timestamp format, writes snapshot.json, verifies integrity,
#   then prints the snapshot directory path on stdout.
snapshot_pre_update() {
    local timestamp="${1:-$(date +%Y%m%d-%H%M%S)}"

    # All log calls redirect to stderr so command-substitution callers
    # (snap_dir=$(snapshot_pre_update ...)) only capture the path on stdout.

    if [[ ! "$timestamp" =~ ^[0-9]{8}-[0-9]{6}$ ]]; then
        log_error "Invalid timestamp format '${timestamp}'; expected YYYYMMDD-HHMMSS." >&2
        return 1
    fi

    local snap_dir="${ROLLBACK_DIR}/pre-update-${timestamp}"
    log_info "Creating rollback snapshot: pre-update-${timestamp}" >&2
    mkdir -p "${snap_dir}"

    local files_saved=0

    # .env and .env.* variants
    for pattern in ".env" ".env.*"; do
        for f in "${INSTALL_DIR}"/${pattern}; do
            [[ -f "$f" ]] || continue
            cp "$f" "${snap_dir}/"
            files_saved=$(( files_saved + 1 ))
        done
    done

    # Active compose overlays — needed to re-create the exact stack on rollback
    for f in "${INSTALL_DIR}"/docker-compose*.yml "${INSTALL_DIR}"/docker-compose*.yaml; do
        [[ -f "$f" ]] || continue
        cp "$f" "${snap_dir}/"
        files_saved=$(( files_saved + 1 ))
    done

    # Per-extension config directories
    for ext_dir in litellm n8n openclaw searxng; do
        local src="${INSTALL_DIR}/config/${ext_dir}"
        if [[ -d "$src" ]]; then
            cp -r "$src" "${snap_dir}/config-${ext_dir}"
            files_saved=$(( files_saved + 1 ))
        fi
    done

    # Version file
    if [[ -f "$VERSION_FILE" ]]; then
        cp "$VERSION_FILE" "${snap_dir}/.version"
        files_saved=$(( files_saved + 1 ))
    fi

    # Snapshot metadata
    jq -n \
        --arg ts  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg ver "$(get_current_version)" \
        --argjson fc "$files_saved" \
        --arg dir "$INSTALL_DIR" \
        '{type:"pre-update", timestamp:$ts, version:$ver, files_count:$fc, install_dir:$dir}' \
        > "${snap_dir}/snapshot.json"

    # Integrity check: verify metadata is valid JSON before declaring success
    if ! jq empty "${snap_dir}/snapshot.json"; then
        log_error "Snapshot metadata is not valid JSON; aborting snapshot." >&2
        rm -rf "${snap_dir}"
        return 1
    fi

    log_ok "Rollback snapshot ready (${files_saved} items): ${snap_dir}" >&2

    _prune_rollback_snapshots

    echo "${snap_dir}"
}

# _restore_snapshot <snap_dir>
#   Validates snapshot integrity, then restores .env files, compose overlays,
#   and per-extension config dirs.  Does NOT restart services.
_restore_snapshot() {
    local snap_dir="$1"
    if [[ ! -d "$snap_dir" ]]; then
        log_error "Rollback snapshot not found: ${snap_dir}"
        return 1
    fi

    # Integrity: snapshot.json must exist and be valid JSON
    if [[ ! -f "${snap_dir}/snapshot.json" ]]; then
        log_error "Snapshot is missing snapshot.json; cannot verify integrity: ${snap_dir}"
        return 1
    fi
    if ! jq empty "${snap_dir}/snapshot.json"; then
        log_error "snapshot.json is not valid JSON; snapshot may be corrupt: ${snap_dir}"
        return 1
    fi

    # Warn about absent critical files (non-fatal — install may not have had them)
    for required in ".env" ".version"; do
        if [[ ! -f "${snap_dir}/${required}" ]]; then
            log_warn "Snapshot is missing ${required} — snapshot may be incomplete."
        fi
    done

    log_info "Restoring from rollback snapshot: $(basename "${snap_dir}")"

    # Flat files: .env*, .version, docker-compose*.yml
    shopt -s dotglob
    for f in "${snap_dir}"/*; do
        local base
        base="$(basename "$f")"
        [[ -f "$f" && "$base" != "snapshot.json" && "$base" != "metadata.json" ]] || continue
        cp "$f" "${INSTALL_DIR}/"
        log_info "  Restored: ${base}"
    done
    shopt -u dotglob

    # Per-extension config directories
    for ext_dir in litellm n8n openclaw searxng; do
        local src="${snap_dir}/config-${ext_dir}"
        if [[ -d "$src" ]]; then
            rm -rf "${INSTALL_DIR}/config/${ext_dir}"
            cp -r "$src" "${INSTALL_DIR}/config/${ext_dir}"
            log_info "  Restored: config/${ext_dir}/"
        fi
    done

    log_ok "Snapshot restored."
}

# wait_for_healthy
#   Polls cmd_health every 10 s until it passes or HEALTH_TIMEOUT expires.
#   Health output is captured to a temp log; shown in full only on timeout.
#   Returns 0 on success, 1 on timeout.
wait_for_healthy() {
    local deadline=$(( SECONDS + HEALTH_TIMEOUT ))
    local attempt=0
    local delay=10
    local health_log
    health_log=$(mktemp /tmp/ods-health-XXXXXX.log)

    log_info "Waiting for services (timeout: ${HEALTH_TIMEOUT}s)..."

    while (( SECONDS < deadline )); do
        attempt=$(( attempt + 1 ))
        if cmd_health > "$health_log" 2>&1; then
            log_ok "Services healthy after ${attempt} attempt(s)."
            rm -f "$health_log"
            return 0
        fi
        local remaining=$(( deadline - SECONDS ))
        if (( remaining > delay )); then
            log_info "  Not yet healthy — retrying in ${delay}s (${remaining}s remaining)..."
            sleep "$delay"
        elif (( remaining > 0 )); then
            sleep "$remaining"
        fi
    done

    log_error "Health-check timeout after ${HEALTH_TIMEOUT}s. Final status:"
    cat "$health_log"
    rm -f "$health_log"
    return 1
}

# _update_rollback <reason> <snap_dir> [compose_flags]
#   Restores the given snapshot and restarts services.
#   Called when cmd_update encounters a non-zero exit at any step.
_update_rollback() {
    local reason="$1"
    local snap_dir_arg="$2"
    local compose_flags_arg="${3:-}"

    log_error "${reason}"
    log_warn "Auto-restoring rollback snapshot and restarting services..."

    if ! _restore_snapshot "$snap_dir_arg"; then
        log_error "CRITICAL: Snapshot restore failed. Manual recovery required."
        log_error "  Snapshot : ${snap_dir_arg}"
        log_error "  Steps    :"
        log_error "    1. cp \"${snap_dir_arg}/.env\" \"${INSTALL_DIR}/.env\""
        log_error "    2. cd \"${INSTALL_DIR}\" && docker compose up -d"
        return 1
    fi

    cd "$INSTALL_DIR"
    if [[ -n "${compose_flags_arg}" ]]; then
        if ! docker compose ${compose_flags_arg} down --remove-orphans; then
            log_warn "docker compose v2 down failed, trying v1..."
            docker-compose ${compose_flags_arg} down --remove-orphans
        fi
        if ! docker compose ${compose_flags_arg} up -d; then
            log_warn "docker compose v2 up failed, trying v1..."
            docker-compose ${compose_flags_arg} up -d
        fi
    else
        if ! docker compose down --remove-orphans; then
            log_warn "docker compose v2 down failed, trying v1..."
            docker-compose down --remove-orphans
        fi
        if ! docker compose up -d; then
            log_warn "docker compose v2 up failed, trying v1..."
            docker-compose up -d
        fi
    fi
    log_warn "Rollback complete. Run 'ods-update.sh health' to verify."
}

#==============================================================================
# COMMAND: CHECK
#==============================================================================

cmd_check() {
    log_info "Checking for updates..."
    
    local current_version
    current_version=$(get_current_version)
    log_info "Current version: ${current_version}"
    
    # Fetch latest release from GitHub
    local api_url="https://api.github.com/repos/${GITHUB_REPO}/releases/latest"
    local response
    local curl_args=(-sf --max-time 15)
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        curl_args+=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
    fi

    if ! response=$(curl "${curl_args[@]}" "${api_url}" 2>/dev/null); then
        log_error "Failed to check for updates. Check network or GITHUB_TOKEN."
        return 1
    fi
    
    local latest_version
    latest_version=$(echo "$response" | jq -r '.tag_name // empty')
    
    if [[ -z "$latest_version" ]]; then
        log_warn "No releases found on GitHub. You may be on a development version."
        return 0
    fi
    
    log_info "Latest version: ${latest_version}"
    
    # Compare versions
    set +e
    semver_compare "$current_version" "$latest_version"
    local cmp_result=$?
    set -e
    
    case $cmp_result in
        0)
            log_ok "You are on the latest version."
            ;;
        1)
            log_warn "You are ahead of the latest release (development version)."
            ;;
        2)
            log_info "Update available: ${current_version} → ${latest_version}"
            echo ""
            echo "Run 'ods update' or './ods-cli update' for normal runtime updates."
            if is_git_checkout; then
                echo "Source checkout detected: run 'ods-update.sh update' only when you intend to pull source code."
            fi
            ;;
    esac
    
    # Update last check timestamp
    mkdir -p "$(dirname "$VERSION_FILE")"
    local version_data
    if [[ -f "$VERSION_FILE" ]]; then
        version_data=$(cat "$VERSION_FILE")
    else
        version_data='{}'
    fi
    echo "$version_data" | jq --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '.last_check = $ts' > "$VERSION_FILE"
}

#==============================================================================
# COMMAND: STATUS
#==============================================================================

cmd_status() {
    echo "ODS Status"
    echo "==================="
    echo ""
    echo "Version:        $(get_current_version)"
    echo "Install path:   ${INSTALL_DIR}"
    echo "Backup path:    ${BACKUP_DIR}"
    echo "Update channel: ${UPDATE_CHANNEL}"
    echo ""
    
    if [[ -f "$VERSION_FILE" ]]; then
        local last_check
        last_check=$(jq -r '.last_check // "never"' "$VERSION_FILE" 2>/dev/null || echo "never")
        local last_update
        last_update=$(jq -r '.last_update // "never"' "$VERSION_FILE" 2>/dev/null || echo "never")
        echo "Last check:     ${last_check}"
        echo "Last update:    ${last_update}"
    else
        echo "Last check:     never"
        echo "Last update:    never"
    fi
    
    echo ""
    
    # Count rollback snapshots
    local snap_count=0
    if [[ -d "$ROLLBACK_DIR" ]]; then
        snap_count=$(find "$ROLLBACK_DIR" -maxdepth 1 -type d -name "pre-update-*" 2>/dev/null | wc -l)
    fi
    echo "Rollback snaps: ${snap_count} (max: ${MAX_BACKUPS}, path: ${ROLLBACK_DIR})"

    # Show last rollback point recorded in version file
    if [[ -f "$VERSION_FILE" ]]; then
        local last_snap
        last_snap=$(jq -r '.last_rollback_point // "none"' "$VERSION_FILE" 2>/dev/null || echo "none")
        echo "Last snap path: ${last_snap}"
    fi

    echo ""

    # Count general backups
    if [[ -d "$BACKUP_DIR" ]]; then
        local backup_count
        backup_count=$(find "$BACKUP_DIR" -maxdepth 1 -type d -name "backup-*" 2>/dev/null | wc -l)
        echo "General backups: ${backup_count} (max: ${MAX_BACKUPS}, path: ${BACKUP_DIR})"
    else
        echo "General backups: 0 (max: ${MAX_BACKUPS})"
    fi
}

#==============================================================================
# COMMAND: BACKUP
#==============================================================================

cmd_backup() {
    local backup_name="${1:-}"
    local timestamp
    timestamp=$(date +%Y%m%d-%H%M%S)
    local backup_id="backup-${timestamp}"
    
    if [[ -n "$backup_name" ]]; then
        backup_id="backup-${backup_name}-${timestamp}"
    fi
    
    local backup_path="${BACKUP_DIR}/${backup_id}"
    
    log_info "Creating backup: ${backup_id}"
    
    mkdir -p "$backup_path"
    
    # Backup compose files
    local files_backed_up=0
    for pattern in "docker-compose*.yml" "docker-compose*.yaml" ".env" ".env.*"; do
        for file in "${INSTALL_DIR}"/${pattern}; do
            if [[ -f "$file" ]]; then
                cp "$file" "$backup_path/"
                ((files_backed_up++))
            fi
        done
    done
    
    # Backup version file
    if [[ -f "$VERSION_FILE" ]]; then
        cp "$VERSION_FILE" "$backup_path/.version"
        ((files_backed_up++))
    fi
    
    # Generate metadata (use jq for safe JSON construction)
    jq -n \
        --arg bid "$backup_id" \
        --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg ver "$(get_current_version)" \
        --argjson fc "$files_backed_up" \
        --arg dir "$INSTALL_DIR" \
        '{backup_id: $bid, timestamp: $ts, version: $ver, files_count: $fc, install_dir: $dir}' \
        > "$backup_path/metadata.json"
    
    log_ok "Backup created: ${backup_path}"
    log_info "Files backed up: ${files_backed_up}"
    
    # Cleanup old backups
    local backup_dirs
    backup_dirs=$(find "$BACKUP_DIR" -maxdepth 1 -type d -name "backup-*" | sort -r)
    local count=0
    for dir in $backup_dirs; do
        ((count++))
        if ((count > MAX_BACKUPS)); then
            log_info "Removing old backup: $(basename "$dir")"
            rm -rf "$dir"
        fi
    done
}

#==============================================================================
# COMMAND: UPDATE
#==============================================================================

cmd_update() {
    log_info "Starting ODS update..."

    local current_version
    current_version=$(get_current_version)

    if ! ensure_source_checkout_for_update; then
        return 1
    fi

    # ── Step 1: rollback snapshot ─────────────────────────────────────────────
    local timestamp
    timestamp=$(date +%Y%m%d-%H%M%S)
    local snap_dir
    snap_dir=$(snapshot_pre_update "$timestamp")

    # Resolve compose flags once — used in restart and rollback paths.
    local compose_flags=""
    compose_flags=$(resolve_compose_flags 2>/dev/null || true)

    # ── Step 2: pull latest changes ───────────────────────────────────────────
    log_info "Pulling latest changes..."
    cd "$INSTALL_DIR"
    git fetch origin
    if ! git pull origin main && ! git pull origin master; then
        _update_rollback "Git pull failed." "$snap_dir" "$compose_flags"
        return 1
    fi

    # ── Step 3: migrations ────────────────────────────────────────────────────
    local migrations_dir="${INSTALL_DIR}/migrations"
    if [[ -d "$migrations_dir" ]]; then
        log_info "Running migrations..."
        for migration in "$migrations_dir"/migrate-v*.sh; do
            if [[ -f "$migration" && -x "$migration" ]]; then
                log_info "Running: $(basename "$migration")"
                if ! bash "$migration"; then
                    _update_rollback "Migration failed: $(basename "$migration")." \
                        "$snap_dir" "$compose_flags"
                    return 1
                fi
            fi
        done
    fi

    # ── Step 4: restart services ──────────────────────────────────────────────
    log_info "Restarting services..."
    cd "$INSTALL_DIR"
    if [[ -n "${compose_flags}" ]]; then
        if ! docker compose ${compose_flags} down --remove-orphans; then
            log_warn "docker compose v2 down failed, trying v1..."
            docker-compose ${compose_flags} down --remove-orphans
        fi
        if ! docker compose ${compose_flags} up -d; then
            log_warn "docker compose v2 up failed, trying v1..."
            docker-compose ${compose_flags} up -d
        fi
    elif [[ -f "${INSTALL_DIR}/docker-compose.yml" ]]; then
        if ! docker compose down --remove-orphans; then
            log_warn "docker compose v2 down failed, trying v1..."
            docker-compose down --remove-orphans
        fi
        if ! docker compose up -d; then
            log_warn "docker compose v2 up failed, trying v1..."
            docker-compose up -d
        fi
    else
        log_warn "No compose files found. Skipping container restart."
    fi

    # ── Step 5: health-check with timeout ────────────────────────────────────
    if ! wait_for_healthy; then
        _update_rollback \
            "Services failed to become healthy after update (timeout: ${HEALTH_TIMEOUT}s)." \
            "$snap_dir" "$compose_flags"
        return 1
    fi

    # ── Step 6: record new version ────────────────────────────────────────────
    local new_version
    new_version=$(git describe --tags 2>/dev/null || git rev-parse --short HEAD)
    local version_data='{}'
    [[ -f "$VERSION_FILE" ]] && version_data=$(cat "$VERSION_FILE")
    echo "$version_data" | jq \
        --arg v    "$new_version" \
        --arg ts   "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg snap "$snap_dir" \
        '.version = $v | .last_update = $ts | .last_rollback_point = $snap' \
        > "$VERSION_FILE"

    log_ok "Update complete! Version: ${new_version}"
    log_info "Rollback point retained at: ${snap_dir}"
}

#==============================================================================
# COMMAND: ROLLBACK
#==============================================================================

cmd_rollback() {
    local target="${1:-}"
    local backup_path=""

    if [[ -n "$target" ]]; then
        # Explicit target: search rollback snapshots first, then general backups.
        for candidate in \
            "${ROLLBACK_DIR}/${target}" \
            "${ROLLBACK_DIR}/pre-update-${target}" \
            "${BACKUP_DIR}/${target}" \
            "${BACKUP_DIR}/backup-${target}"; do
            if [[ -d "$candidate" ]]; then
                backup_path="$candidate"
                break
            fi
        done
    else
        # No target: prefer the most recent pre-update rollback snapshot,
        # fall back to the most recent general backup.
        backup_path=$(find "${ROLLBACK_DIR}" -maxdepth 1 -type d -name "pre-update-*" \
            2>/dev/null | sort -r | head -1)
        if [[ -z "$backup_path" ]]; then
            backup_path=$(find "${BACKUP_DIR}" -maxdepth 1 -type d -name "backup-*" \
                2>/dev/null | sort -r | head -1)
        fi
    fi

    if [[ -z "$backup_path" || ! -d "$backup_path" ]]; then
        log_error "No backup or rollback snapshot found to restore from."
        echo ""
        echo "Pre-update rollback snapshots (${ROLLBACK_DIR}):"
        ls -1 "${ROLLBACK_DIR}" 2>/dev/null | grep '^pre-update-' || echo "  (none)"
        echo ""
        echo "General backups (${BACKUP_DIR}):"
        ls -1 "${BACKUP_DIR}" 2>/dev/null | grep '^backup-' || echo "  (none)"
        return 1
    fi

    log_info "Rolling back from: $(basename "$backup_path")"

    # Show metadata (snapshot.json or legacy metadata.json)
    local meta_file="${backup_path}/snapshot.json"
    [[ -f "$meta_file" ]] || meta_file="${backup_path}/metadata.json"
    if [[ -f "$meta_file" ]]; then
        local bver btime
        bver=$(jq -r '.version  // "unknown"' "$meta_file")
        btime=$(jq -r '.timestamp // "unknown"' "$meta_file")
        log_info "Snapshot version : ${bver}"
        log_info "Snapshot time    : ${btime}"
    fi

    # Stop services
    log_info "Stopping services..."
    cd "$INSTALL_DIR"
    if ! docker compose down; then
        log_warn "docker compose v2 down failed, trying v1..."
        docker-compose down
    fi

    # Restore — use _restore_snapshot for pre-update snapshots (they include
    # config-* dirs); fall back to flat-file copy for legacy general backups.
    if [[ -f "${backup_path}/snapshot.json" ]]; then
        if ! _restore_snapshot "$backup_path"; then
            log_error "Restore failed. Manual recovery required."
            log_error "  Source: ${backup_path}"
            return 1
        fi
    else
        log_info "Restoring configuration files (legacy backup)..."
        shopt -s dotglob
        for file in "$backup_path"/*; do
            if [[ -f "$file" && "$(basename "$file")" != "metadata.json" ]]; then
                cp "$file" "$INSTALL_DIR/"
                log_info "  Restored: $(basename "$file")"
            fi
        done
        shopt -u dotglob
    fi

    # Restart services
    log_info "Restarting services..."
    if ! docker compose up -d; then
        log_warn "docker compose v2 up failed, trying v1..."
        docker-compose up -d
    fi

    # Verify health using the same timeout-aware poller as cmd_update
    if wait_for_healthy; then
        log_ok "Rollback complete!"
    else
        log_warn "Rollback complete but health checks failed. Manual intervention may be required."
        return 1
    fi
}

#==============================================================================
# COMMAND: CHANGELOG
#==============================================================================

cmd_changelog() {
    local version="${1:-}"
    
    if [[ -n "$version" ]]; then
        # Fetch specific version from GitHub
        log_info "Fetching changelog for version ${version}..."
        local api_url="https://api.github.com/repos/${GITHUB_REPO}/releases/tags/${version}"
        local response
        if response=$(curl -sf --max-time 15 "${api_url}" 2>/dev/null); then
            echo "$response" | jq -r '.body // "No changelog available."'
        else
            log_error "Could not fetch changelog for ${version}"
            return 1
        fi
    else
        # Show local CHANGELOG.md
        local changelog_file="${INSTALL_DIR}/CHANGELOG.md"
        if [[ -f "$changelog_file" ]]; then
            # Show first 50 lines (most recent entries)
            head -50 "$changelog_file"
        else
            log_warn "No local CHANGELOG.md found."
            log_info "Fetching latest release notes from GitHub..."
            cmd_changelog "$(curl -sf --max-time 15 "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" | jq -r '.tag_name // empty')" || true
        fi
    fi
}

#==============================================================================
# COMMAND: HEALTH
#==============================================================================

cmd_health() {
    log_info "Running health checks..."
    local all_healthy=true
    local timeout_start=$SECONDS
    
    # Check Docker is running
    if ! docker info &>/dev/null; then
        log_error "Docker is not running"
        return 1
    fi
    log_ok "Docker is running"
    
    # Check containers
    cd "$INSTALL_DIR"
    local -a compose_cmd
    if docker compose version &>/dev/null; then
        compose_cmd=(docker compose)
    elif command -v docker-compose >/dev/null 2>&1; then
        compose_cmd=(docker-compose)
    else
        log_error "Docker Compose is not available"
        return 1
    fi

    local compose_flags=""
    local -a compose_args=()
    compose_flags=$(resolve_compose_flags 2>/dev/null || true)
    if [[ -n "$compose_flags" ]]; then
        read -ra compose_args <<< "$compose_flags"
    fi
    
    local services
    services=$("${compose_cmd[@]}" "${compose_args[@]}" ps --services 2>/dev/null || echo "")
    
    if [[ -z "$services" ]]; then
        if [[ -n "$compose_flags" ]]; then
            log_warn "No services found for resolved compose stack: ${compose_flags}"
        else
            log_warn "No compose stack could be resolved for this install"
        fi
        return 1
    fi
    
    for service in $services; do
        local status
        status=$("${compose_cmd[@]}" "${compose_args[@]}" ps --format json "$service" 2>/dev/null \
            | jq -r 'if type == "array" then (.[0].State // "unknown") else (.State // "unknown") end' 2>/dev/null \
            || echo "unknown")
        
        if [[ "$status" == "running" ]]; then
            log_ok "Service ${service}: running"
        else
            log_error "Service ${service}: ${status}"
            all_healthy=false
        fi
    done
    
    # Check dashboard API health endpoint
    local dashboard_api_port="${DASHBOARD_API_PORT:-}"
    [[ -n "$dashboard_api_port" ]] || dashboard_api_port="$(env_file_value DASHBOARD_API_PORT)"
    dashboard_api_port="${dashboard_api_port:-3002}"
    if curl -sf "http://127.0.0.1:${dashboard_api_port}/health" &>/dev/null; then
        log_ok "Dashboard API: healthy"
    elif curl -sf "http://127.0.0.1:${dashboard_api_port}/api/status" &>/dev/null; then
        log_ok "Dashboard API: responding"
    else
        log_warn "Dashboard API: not responding on port ${dashboard_api_port}"
    fi
    
    # Check llama-server health
    local llama_server_port="${OLLAMA_PORT:-${LLAMA_SERVER_PORT:-}}"
    [[ -n "$llama_server_port" ]] || llama_server_port="$(env_file_value OLLAMA_PORT)"
    [[ -n "$llama_server_port" ]] || llama_server_port="$(env_file_value LLAMA_SERVER_PORT)"
    llama_server_port="${llama_server_port:-8080}"
    if curl -sf "http://127.0.0.1:${llama_server_port}/v1/models" &>/dev/null; then
        log_ok "llama-server: healthy"
    else
        log_warn "llama-server: not responding on port ${llama_server_port}"
    fi
    
    if $all_healthy; then
        log_ok "All health checks passed"
        return 0
    else
        log_error "Some health checks failed"
        return 1
    fi
}

#==============================================================================
# USAGE
#==============================================================================

usage() {
    cat << EOF
ODS Update Manager

Usage: ods-update.sh <command> [options]

Commands:
  check          Check for available updates
  status         Show current version, update status, and rollback info
  backup [name]  Create a named general backup of current configuration
  update         Source-checkout only: pull latest source, run migrations,
                 restart, health-check, and auto-restore on failure
  rollback [id]  Restore from a rollback snapshot or general backup
                 (default: most recent pre-update snapshot)
  changelog [v]  Show changelog (optional: specific version)
  health         Run health checks on all services

Rollback snapshots:
  Stored in:  <install_dir>/data/backups/pre-update-<timestamp>/
  Contents:   .env, docker-compose overlays, config/{litellm,n8n,openclaw,searxng}/
  Retained:   MAX_BACKUPS most recent snapshots (oldest pruned automatically)

Environment Variables:
  GITHUB_TOKEN        GitHub API token (for higher rate limits)
  UPDATE_CHANNEL      stable|beta|nightly (default: stable)
  MAX_BACKUPS         Number of snapshots/backups to retain (default: 10)
  HEALTH_TIMEOUT      Seconds to wait for healthy services (default: 120)
  DASHBOARD_API_PORT  Dashboard API port (default: 3002)
  OLLAMA_PORT         llama-server port (default: 8080)

Examples:
  ods-update.sh check
  ods-update.sh status
  ods-update.sh backup pre-experiment
  ods update                    # normal runtime/image update
  ods-update.sh update          # source checkout only
  ods-update.sh rollback
  ods-update.sh rollback 20260317-120000
  ods-update.sh changelog v1.1.0
  ods-update.sh health

EOF
}

#==============================================================================
# MAIN
#==============================================================================

main() {
    local command="${1:-help}"
    shift || true

    case "$command" in
        check)
            cmd_check "$@"
            ;;
        status)
            cmd_status "$@"
            ;;
        backup)
            cmd_backup "$@"
            ;;
        update)
            cmd_update "$@"
            ;;
        rollback)
            cmd_rollback "$@"
            ;;
        changelog)
            cmd_changelog "$@"
            ;;
        health)
            cmd_health "$@"
            ;;
        help|--help|-h)
            usage
            ;;
        *)
            log_error "Unknown command: $command"
            echo ""
            usage
            exit 1
            ;;
    esac
}

main "$@"
