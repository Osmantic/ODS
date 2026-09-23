#!/bin/bash
# ods-restore.sh - ODS Restore Utility
# Part of M11: Update & Lifecycle Management
# Restores user data and config from backups

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="${ODS_DIR:-$SCRIPT_DIR}"
BACKUP_ROOT="${ODS_DIR}/.backups"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step() { echo -e "${CYAN}[STEP]${NC} $*"; }

# Source shared rsync utilities
. "$ODS_DIR/lib/rsync.sh"
. "$ODS_DIR/lib/backup-paths.sh"

# Convert bytes to a human-friendly string (best-effort)
fmt_bytes() {
    local bytes="${1:-0}"
    if command -v numfmt >/dev/null 2>&1; then
        numfmt --to=iec --suffix=B "$bytes" 2>/dev/null || echo "${bytes}B"
    else
        local mib=$(( (bytes + 1048575) / 1048576 ))
        echo "${mib}MiB"
    fi
}

# Available bytes on filesystem containing a path
free_bytes_for_path() {
    local path="$1"
    # Bash arithmetic requires decimal integers, including above 2 GiB.
    df -Pk "$path" 2>/dev/null | awk 'NR==2 { printf "%.0f\n", $4 * 1024 }'
}

# Estimate the backup size on disk (uncompressed)
estimate_restore_bytes_dir() {
    local backup_dir="$1"
    du -sk "$backup_dir" 2>/dev/null | awk '{printf "%.0f\n", $1 * 1024}'
}

# Estimate restore size for a tar.gz (uncompressed file sizes)
estimate_restore_bytes_tar() {
    local tar_path="$1"
    # tar -tv lists size in column 3
    tar -tvzf "$tar_path" 2>/dev/null | awk '{sum += $3} END {printf "%.0f\n", sum+0}'
}

ensure_restore_space() {
    local backup_id="$1"

    local compressed="$BACKUP_ROOT/$backup_id.tar.gz"
    local uncompressed="$BACKUP_ROOT/$backup_id"

    local need=""
    if [[ -d "$uncompressed" ]]; then
        need=$(estimate_restore_bytes_dir "$uncompressed")
    elif [[ -f "$compressed" ]]; then
        need=$(estimate_restore_bytes_tar "$compressed")
    fi

    # Best-effort only
    [[ -n "$need" && "$need" -gt 0 ]] || return 0

    local free
    free=$(free_bytes_for_path "$ODS_DIR")

    if [[ -n "$free" && "$free" -lt "$need" ]]; then
        log_error "Not enough disk space to restore into: $ODS_DIR"
        log_error "Need ~$(fmt_bytes "$need"), have ~$(fmt_bytes "$free")."
        log_error "Free up space or restore to a different location (set ODS_DIR)."
        return 1
    fi

    return 0
}

# Show usage
usage() {
    cat << EOF
ODS Restore Utility

Usage: $(basename "$0") [OPTIONS] [BACKUP_ID]

OPTIONS:
    -h, --help              Show this help message
    -l, --list              List available backups
    -f, --force             Skip confirmation prompts
    -d, --dry-run           Show what would be restored without doing it
    -s, --stop-containers   Stop containers before restore (recommended)
    --data-only             Restore only user data, not config
    --config-only           Restore only config, not user data
    --skip-verify           Skip checksum verification (NOT RECOMMENDED)

BACKUP_ID:
    The backup identifier to restore from (e.g., 20260212-071500)
    If not provided, shows interactive selection

EXAMPLES:
    $(basename "$0") -l                          # List all backups
    $(basename "$0") 20260212-071500             # Restore specific backup
    $(basename "$0") -f 20260212-071500          # Force restore without prompts
    $(basename "$0") -d 20260212-071500          # Dry run (preview only)
    $(basename "$0") --data-only 20260212-071500 # Restore only user data

EOF
}

# List available backups
list_backups() {
    if [[ ! -d "$BACKUP_ROOT" ]]; then
        log_error "No backup directory found at: $BACKUP_ROOT"
        return 1
    fi

    local backups=()
    while IFS= read -r -d '' backup; do
        backups+=("$backup")
    done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 \( -type d -o -name "*.tar.gz" \) -print0 2>/dev/null | sort -z -r)

    if [[ ${#backups[@]} -eq 0 ]]; then
        log_error "No backups found in: $BACKUP_ROOT"
        return 1
    fi

    echo ""
    echo "Available Backups:"
    echo "═══════════════════════════════════════════════════════════════════"
    printf "%-5s %-20s %-12s %-10s %s\n" "#" "ID" "Type" "Size" "Description"
    echo "───────────────────────────────────────────────────────────────────"

    local i=1
    for backup in "${backups[@]}"; do
        local id
        id=$(basename "$backup" .tar.gz)
        local manifest="$backup/manifest.json"
        local backup_type="unknown"
        local description=""
        local size

        if [[ -f "$backup" ]]; then
            # Compressed backup
            size=$(du -sh "$backup" 2>/dev/null | cut -f1)
            # Try to read manifest from tar
            if tar -tzf "$backup" 2>/dev/null | grep -q "manifest.json"; then
                manifest=$(tar -xzf "$backup" -O */manifest.json 2>/dev/null || echo "")
                if [[ -n "$manifest" ]]; then
                    backup_type=$(echo "$manifest" | grep -o '"backup_type": "[^"]*"' | cut -d'"' -f4 || echo "unknown")
                    description=$(echo "$manifest" | grep -o '"description": "[^"]*"' | cut -d'"' -f4 || echo "")
                fi
            fi
        else
            # Uncompressed backup
            size=$(du -sh "$backup" 2>/dev/null | cut -f1)
            if [[ -f "$manifest" ]]; then
                backup_type=$(grep -o '"backup_type": "[^"]*"' "$manifest" 2>/dev/null | cut -d'"' -f4 || echo "unknown")
                description=$(grep -o '"description": "[^"]*"' "$manifest" 2>/dev/null | cut -d'"' -f4 || echo "")
            fi
        fi

        printf "%-5s %-20s %-12s %-10s %s\n" "$i" "$id" "$backup_type" "$size" "$description"
        ((i++))
    done
    echo ""
    return 0
}

# Select backup interactively
# The caller captures stdout (backup_id=$(select_backup)), so the table and
# prompt go to stderr — on stdout they'd be swallowed into the captured ID
# and the user would stare at a blank screen.
select_backup() {
    if ! list_backups >&2; then
        return 1
    fi

    echo "Select a backup to restore (enter number):" >&2
    read -r selection || selection=""

    local backups=()
    while IFS= read -r -d '' backup; do
        backups+=("$backup")
    done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 \( -type d -o -name "*.tar.gz" \) -print0 2>/dev/null | sort -z -r)

    local index=$((selection - 1))
    if [[ $index -lt 0 || $index -ge ${#backups[@]} ]]; then
        # stdout is the captured backup ID; the error must reach the user.
        log_error "Invalid selection: $selection" >&2
        return 1
    fi

    basename "${backups[$index]}" .tar.gz
}

# Extract compressed backup
# Callers capture stdout (backup_dir=$(extract_backup ...)), so every log
# line must go to stderr — anything on stdout becomes part of the returned
# path and breaks the restore.
extract_backup() {
    local backup_id="$1"
    local compressed="$BACKUP_ROOT/$backup_id.tar.gz"
    local uncompressed="$BACKUP_ROOT/$backup_id"

    if [[ -L "$uncompressed" ]]; then
        log_error "Refusing a symlinked backup directory" >&2
        return 1
    fi
    if [[ -d "$uncompressed" ]]; then
        # Already extracted
        echo "$uncompressed"
        return 0
    fi

    if [[ -f "$compressed" ]]; then
        if ! command -v python3 >/dev/null 2>&1; then
            log_error "python3 is required to validate and extract compressed backups" >&2
            return 1
        fi
        log_info "Extracting compressed backup..." >&2
        if ! python3 "$SCRIPT_DIR/lib/backup-archive.py" "$compressed" "$BACKUP_ROOT" "$backup_id"; then
            log_error "Failed to extract backup archive" >&2
            return 1
        fi
        echo "$uncompressed"
        return 0
    fi

    log_error "Backup not found: $backup_id" >&2
    return 1
}

# Validate backup
validate_backup() {
    local backup_dir="$1"
    local skip_checksum="${2:-false}"
    local manifest="$backup_dir/manifest.json"

    log_step "Validating backup..."

    if [[ ! -f "$manifest" ]]; then
        log_error "Backup manifest not found: $manifest"
        return 1
    fi

    # Check manifest version compatibility
    local manifest_version
    manifest_version=$(grep -o '"manifest_version": "[^"]*"' "$manifest" | cut -d'"' -f4 || echo "1.0")

    if [[ "$manifest_version" != "1.0" ]]; then
        log_warn "Backup manifest version mismatch: $manifest_version (expected 1.0)"
        log_warn "Restore may not work correctly"
    fi

    # Display backup info
    echo ""
    echo "Backup Information:"
    echo "───────────────────────────────────────────────────────────────────"
    grep -E '"(backup_date|backup_type|ods_version|description)"' "$manifest" | \
        sed 's/^[[:space:]]*/  /' | sed 's/"//g' | sed 's/,//'
    echo ""

    # Verify checksums (CRITICAL SECURITY CHECK)
    if [[ "$skip_checksum" != "true" ]]; then
        local checksum_file="$backup_dir/checksums.sha256"
        if [[ -f "$checksum_file" ]]; then
            log_info "Verifying backup integrity (SHA256)..."

            local verify_cmd=""
            if command -v sha256sum >/dev/null 2>&1; then
                verify_cmd="sha256sum -c"
            elif command -v shasum >/dev/null 2>&1; then
                verify_cmd="shasum -a 256 -c"
            else
                log_warn "Neither sha256sum nor shasum available, skipping checksum verification"
                log_warn "Use --skip-verify to suppress this warning"
            fi

            if [[ -n "$verify_cmd" ]]; then
                (
                    cd "$backup_dir"
                    if $verify_cmd "checksums.sha256" >/dev/null 2>&1; then
                        log_success "Backup integrity verified (all checksums match)"
                    else
                        log_error "Backup integrity check FAILED - checksums do not match"
                        log_error "This backup may be corrupted or tampered with"
                        log_error "Use --skip-verify to restore anyway (NOT RECOMMENDED)"
                        return 1
                    fi
                )
                if [[ $? -ne 0 ]]; then
                    return 1
                fi
            fi
        else
            log_warn "No checksums.sha256 found in backup (older backup format)"
            log_warn "Cannot verify backup integrity - proceed with caution"
        fi
    else
        log_warn "Checksum verification SKIPPED (--skip-verify flag used)"
    fi

    # Warn if backup looks partial / missing common paths.
    # (Informational only; older/minimal backups are still valid.)
    local -a expected_data=("${ODS_USER_DATA_PATHS[@]}")

    local missing_any=false
    for p in "${expected_data[@]}"; do
        if [[ ! -d "$backup_dir/$p" ]]; then
            missing_any=true
            break
        fi
    done

    if [[ "$missing_any" == "true" ]]; then
        log_warn "This backup does not contain some common data directories."
        log_warn "That may be normal (services not used), but restore will be partial for missing paths."
    fi

    log_success "Backup validated"
    return 0
}

# Preview what would be restored
dry_run_preview() {
    local backup_dir="$1"
    local restore_data="$2"
    local restore_config="$3"

    log_step "DRY RUN - Preview of restore operation:"
    echo ""

    if [[ "$restore_data" == "true" ]]; then
        echo "User Data to Restore:"
        echo "───────────────────────────────────────────────────────────────────"
        local data_dirs=("${ODS_USER_DATA_PATHS[@]}")
        for dir in "${data_dirs[@]}"; do
            if [[ -d "$backup_dir/$dir" ]]; then
                local size
                size=$(du -sh "$backup_dir/$dir" 2>/dev/null | cut -f1)
                echo "  ✓ $dir ($size)"
            fi
        done
        echo ""

        # Cache tier (full backups only): models and model caches.
        local -a cache_dirs=("models" "${ODS_BACKUP_CACHE_PATHS[@]}")
        local cache_listed=false
        for dir in "${cache_dirs[@]}"; do
            if [[ -d "$backup_dir/$dir" ]]; then
                if [[ "$cache_listed" == "false" ]]; then
                    echo "Cache to Restore (models):"
                    echo "───────────────────────────────────────────────────────────────────"
                    cache_listed=true
                fi
                local size
                size=$(du -sh "$backup_dir/$dir" 2>/dev/null | cut -f1)
                echo "  ✓ $dir ($size)"
            fi
        done
        if [[ "$cache_listed" == "true" ]]; then
            echo ""
        fi
    fi

    if [[ "$restore_config" == "true" ]]; then
        echo "Config Files to Restore:"
        echo "───────────────────────────────────────────────────────────────────"
        # Dynamically discover config files (dotfiles + compose overlays + scripts)
        for file in "$backup_dir"/.env "$backup_dir"/.version "$backup_dir"/docker-compose*.y*ml "$backup_dir"/ods-*.sh; do
            if [[ -f "$file" ]]; then
                echo "  ✓ $(basename "$file")"
            fi
        done
        if [[ -d "$backup_dir/config" ]]; then
            echo "  ✓ config/ directory"
        fi
        echo ""
    fi

    log_info "Dry run complete. No changes were made."
}

# Stop running containers
stop_containers() {
    log_step "Stopping containers..."

    local projects
    if ! projects=$(docker compose ls --quiet); then
        log_error "Cannot determine running containers; refusing to restore."
        return 1
    fi
    if ! printf '%s\n' "$projects" | grep -Fxq "$(basename "$ODS_DIR")"; then
        log_info "No running containers found"
        return 0
    fi

    cd "$ODS_DIR"
    if docker compose down; then
        log_success "Containers stopped"
    else
        log_error "Containers did not stop; refusing to restore live data."
        return 1
    fi
}

# Restore all selected paths as one transaction. User data remains additive:
# stage its current contents, overlay the backup, then publish by rename.
_restore_selected_paths() (
    local backup_dir="$1" restore_data="$2" restore_configuration="$3"
    local -a sources=() destinations=() workspaces=() publishing=() merge_data=()
    local dir file parent workspace i changes transfer_status completed=false recovery_failed=false
    shopt -s nullglob
    if [[ "$restore_data" == true ]]; then
        for dir in "${ODS_USER_DATA_PATHS[@]}" "models" "${ODS_BACKUP_CACHE_PATHS[@]}"; do
            [[ -d "$backup_dir/$dir" ]] || continue
            sources+=("$backup_dir/$dir")
            destinations+=("$ODS_DIR/$dir")
            merge_data+=(true)
        done
    fi
    if [[ "$restore_configuration" == true ]]; then
        for file in "$backup_dir"/.env "$backup_dir"/.version "$backup_dir"/docker-compose*.y*ml "$backup_dir"/ods-*.sh; do
            [[ -f "$file" ]] || continue
            sources+=("$file")
            destinations+=("$ODS_DIR/$(basename "$file")")
            merge_data+=(false)
        done
        if [[ -d "$backup_dir/config" ]]; then
            sources+=("$backup_dir/config")
            destinations+=("$ODS_DIR/config")
            merge_data+=(false)
        fi
    fi
    if [[ ${#sources[@]} == 0 ]]; then
        log_warn "No selected data or configuration paths are present in this backup."
        return 0
    fi

    restore_cleanup() {
        local status=$? index target work
        trap - EXIT INT TERM
        if [[ "$completed" != true ]]; then
            for ((index=${#workspaces[@]}-1; index>=0; index--)); do
                target="${destinations[index]}"
                work="${workspaces[index]}"
                if [[ "${publishing[index]:-false}" == true && ! -e "$work/new" && ! -L "$work/new"
                    && ( -e "$target" || -L "$target" ) ]]; then
                    if ! mv -- "$target" "$work/new"; then
                        recovery_failed=true
                        log_error "Cannot withdraw restored item ${target}; recovery files retained at ${work}"
                        continue
                    fi
                fi
                if [[ -e "$work/old" || -L "$work/old" ]]; then
                    if ! mv -- "$work/old" "$target"; then
                        recovery_failed=true
                        log_error "Cannot restore original ${target}; original retained at ${work}/old"
                        continue
                    fi
                fi
            done
        fi
        for ((index=0; index<${#workspaces[@]}; index++)); do
            # If recovery failed, never remove a workspace containing originals.
            if [[ "$completed" == true || ( ! -e "${workspaces[index]}/old" && ! -L "${workspaces[index]}/old" ) ]]; then
                rm -rf -- "${workspaces[index]}" || log_warn "Could not remove staging ${workspaces[index]}"
            fi
        done
        if [[ "$recovery_failed" == true ]]; then
            log_error "Rollback could not restore every original. Manual recovery required from the retained paths above."
        fi
        if [[ "$completed" != true && "$status" == 0 ]]; then
            status=1
        fi
        exit "$status"
    }
    trap restore_cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM


    for ((i=0; i<${#sources[@]}; i++)); do
        file="${destinations[i]}"
        if [[ -L "$file" ]]; then
            log_error "Refusing to replace symlinked restore destination: $file"
            return 1
        fi
        parent="$(dirname "$file")"
        mkdir -p -- "$parent" || return 1
        workspace="$(mktemp -d "$parent/.ods-restore.XXXXXX")" || return 1
        workspaces+=("$workspace")
        if [[ "${merge_data[i]}" == true ]]; then
            if [[ -e "$file" ]]; then
                [[ -d "$file" ]] || { log_error "User-data destination is not a directory: $file"; return 1; }
                cp -a -- "$file" "$workspace/new" || return 1
            else
                mkdir -- "$workspace/new" || return 1
            fi
            if rsync_with_progress "${sources[i]}/" "$workspace/new/" "Staging ${sources[i]}"; then
                :
            else
                transfer_status=$?
                log_error "Failed to stage user data; live paths are unchanged."
                return "$transfer_status"
            fi
        elif ! cp -a -- "${sources[i]}" "$workspace/new"; then
            log_error "Failed to stage configuration; live paths are unchanged."
            return 1
        fi
        # Verify the actual selected payload before any live path is moved.
        # No Open WebUI or other optional service is required for Pixel-only
        # and configuration-only backups. Additive local files are not deleted.
        if [[ -d "${sources[i]}" ]]; then
            if ! changes=$(rsync -a --checksum --dry-run --itemize-changes "${sources[i]}/" "$workspace/new/"); then
                log_error "Could not verify staged directory: ${sources[i]}"
                return 1
            fi
            [[ -z "$changes" ]] || { log_error "Staged directory differs from backup: ${sources[i]}"; return 1; }
        elif ! cmp -s -- "${sources[i]}" "$workspace/new"; then
            log_error "Staged file differs from backup: ${sources[i]}"
            return 1
        fi
    done
    for ((i=0; i<${#sources[@]}; i++)); do
        file="${destinations[i]}"
        workspace="${workspaces[i]}"
        publishing[i]=true
        if [[ -e "$file" || -L "$file" ]]; then
            if ! mv -- "$file" "$workspace/old"; then
                log_error "Cannot preserve original $file; undoing restore."
                return 1
            fi
        fi
        if ! mv -- "$workspace/new" "$file"; then
            log_error "Cannot activate $file; undoing restore."
            return 1
        fi
    done
    for ((i=0; i<${#sources[@]}; i++)); do
        verify_restore "${sources[i]}" "${destinations[i]}" || return 1
    done
    completed=true
    for file in "${destinations[@]}"; do
        log_success "Restored: ${file#"$ODS_DIR/"}"
    done
)

# Retain the individual entry points for callers restoring just one category.
restore_user_data() { _restore_selected_paths "$1" true false; }
restore_config() { _restore_selected_paths "$1" false true; }

validate_restore_config_source() {
    local backup_dir="$1"

    # A present-but-empty config/ means the backup was truncated. This check
    # runs before the restore plan mutates user data or stops containers.
    if [[ -d "$backup_dir/config" && -z "$(ls -A "$backup_dir/config")" ]]; then
        log_error "Backup's config directory is empty: $backup_dir/config"
        log_error "This backup is incomplete — refusing to replace the live config at $ODS_DIR/config"
        log_error "Restore from a complete backup, or re-run with --data-only to skip configuration."
        return 1
    fi
}

# Check publication against the selected payload, not unrelated services.
verify_restore() {
    local source="$1" destination="$2"
    if [[ -L "$destination" || ( -d "$source" && ! -d "$destination" )
        || ( -f "$source" && ! -f "$destination" ) ]]; then
        log_error "Selected path missing after restore: $destination"
        return 1
    fi
}

# Main restore function
do_restore() {
    local backup_id="$1"
    local force="$2"
    local dry_run="$3"
    local stop_first="$4"
    local restore_data="$5"
    local restore_config="$6"
    local skip_verify="$7"

    if [[ -z "$backup_id" || "$backup_id" == . || "$backup_id" == ..
        || "$backup_id" == */* || "$backup_id" == *\\* ]]; then
        log_error "Invalid backup ID"
        return 1
    fi

    log_info "Starting restore from backup: $backup_id"

    # Disk space preflight (best-effort)
    if ! ensure_restore_space "$backup_id"; then
        return 1
    fi

    # Extract if compressed
    local backup_dir
    if ! backup_dir=$(extract_backup "$backup_id"); then
        return 1
    fi

    # Validate backup (with optional checksum verification)
    if ! validate_backup "$backup_dir" "$skip_verify"; then
        log_error "Backup validation failed"
        return 1
    fi

    if [[ "$restore_config" == "true" ]] \
        && ! validate_restore_config_source "$backup_dir"; then
        return 1
    fi

    # Dry run mode
    if [[ "$dry_run" == "true" ]]; then
        dry_run_preview "$backup_dir" "$restore_data" "$restore_config"
        return 0
    fi

    # Confirmation
    if [[ "$force" != "true" ]]; then
        echo ""
        log_warn "This will copy backup data into: $ODS_DIR"
        log_warn "Existing files may be overwritten."
        echo ""
        read -rp "Type the backup ID ('$backup_id') to continue, or press Enter to cancel: " confirm || confirm=""
        if [[ "$confirm" != "$backup_id" ]]; then
            log_info "Restore cancelled"
            return 0
        fi
    fi

    # Stop containers if requested
    if [[ "$stop_first" == "true" ]]; then
        stop_containers || return 1
    fi

    local restore_status
    if _restore_selected_paths "$backup_dir" "$restore_data" "$restore_config"; then
        :
    else
        restore_status=$?
        log_error "Restore failed; inspect recovery diagnostics above."
        return "$restore_status"
    fi

    log_success "Restore complete!"
    echo ""
    echo "Next steps:"
    echo "  1. Review restored configuration: cat $ODS_DIR/.env"
    echo "  2. Start services: docker compose up -d"
    echo "  3. Check status: ./ods-preflight.sh"
}

# Main entry point
main() {
    local backup_id=""
    local force="false"
    local dry_run="false"
    local stop_first="false"
    local restore_data="true"
    local restore_config="true"
    local list_mode="false"
    local skip_verify="false"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            -l|--list)
                list_mode="true"
                shift
                ;;
            -f|--force)
                force="true"
                shift
                ;;
            -d|--dry-run)
                dry_run="true"
                shift
                ;;
            -s|--stop-containers)
                stop_first="true"
                shift
                ;;
            --data-only)
                restore_data="true"
                restore_config="false"
                shift
                ;;
            --config-only)
                restore_data="false"
                restore_config="true"
                shift
                ;;
            --skip-verify)
                skip_verify="true"
                shift
                ;;
            -*)
                log_error "Unknown option: $1"
                usage
                exit 1
                ;;
            *)
                if [[ -n "$backup_id" ]]; then
                    log_error "Multiple backup IDs specified: '$backup_id' and '$1'"
                    exit 1
                fi
                backup_id="$1"
                shift
                ;;
        esac
    done

    # List mode
    if [[ "$list_mode" == "true" ]]; then
        list_backups
        exit 0
    fi

    # Interactive selection if no backup specified
    if [[ -z "$backup_id" ]]; then
        if ! backup_id=$(select_backup); then
            exit 1
        fi
    fi

    # Check if running in ODS directory
    # Check for any compose file (standalone or overlay) or data directory
    local has_compose=false
    for f in "$ODS_DIR"/docker-compose*.y*ml; do
        [[ -f "$f" ]] && has_compose=true && break
    done
    if [[ "$has_compose" == "false" && ! -d "$ODS_DIR/data" ]]; then
        log_warn "This doesn't appear to be a ODS directory"
        log_warn "Expected: docker-compose.yml or data/ directory"
        read -rp "Continue anyway? [y/N] " confirm || confirm=""
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi

    # Perform restore
    do_restore "$backup_id" "$force" "$dry_run" "$stop_first" "$restore_data" "$restore_config" "$skip_verify"
}

main "$@"
