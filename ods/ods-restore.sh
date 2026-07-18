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
    df -Pk "$path" 2>/dev/null | awk 'NR==2 { print $4 * 1024 }'
}

# Estimate the backup size on disk (uncompressed)
estimate_restore_bytes_dir() {
    local backup_dir="$1"
    du -sk "$backup_dir" 2>/dev/null | awk '{print $1 * 1024}'
}

# Estimate restore size for a tar.gz (uncompressed file sizes)
estimate_restore_bytes_tar() {
    local tar_path="$1"
    # tar -tv lists size in column 3
    tar -tvzf "$tar_path" 2>/dev/null | awk '{sum += $3} END {print sum+0}'
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

    if [[ -n "$free" && "$free" -gt 0 && "$free" -lt "$need" ]]; then
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
    read -r selection

    local backups=()
    while IFS= read -r -d '' backup; do
        backups+=("$backup")
    done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 \( -type d -o -name "*.tar.gz" \) -print0 2>/dev/null | sort -z -r)

    local index=$((selection - 1))
    if [[ $index -lt 0 || $index -ge ${#backups[@]} ]]; then
        log_error "Invalid selection: $selection"
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

    if [[ -d "$uncompressed" ]]; then
        # Already extracted
        echo "$uncompressed"
        return 0
    fi

    if [[ -f "$compressed" ]]; then
        # Validate: reject archives with absolute paths or path traversal
        if tar -tzf "$compressed" 2>/dev/null | grep -qE '(^/|\.\./)'; then
            log_error "Backup archive contains unsafe paths (absolute or ../) — refusing to extract" >&2
            return 1
        fi
        log_info "Extracting compressed backup..." >&2
        mkdir -p "$uncompressed"
        if ! tar xzf "$compressed" --no-same-owner -C "$BACKUP_ROOT"; then
            log_error "Failed to extract backup archive" >&2
            rm -rf "$uncompressed"
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
    local -a expected_data=(
        "data/open-webui"
        "data/n8n"
        "data/qdrant"
        "data/openclaw"
        "data/litellm"
        "data/livekit"
        "data/ollama"
    )

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
        local data_dirs=("data/open-webui" "data/n8n" "data/qdrant" "data/openclaw" "data/litellm" "data/livekit" "data/ollama")
        for dir in "${data_dirs[@]}"; do
            if [[ -d "$backup_dir/$dir" ]]; then
                local size
                size=$(du -sh "$backup_dir/$dir" 2>/dev/null | cut -f1)
                echo "  ✓ $dir ($size)"
            fi
        done
        echo ""
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

    if ! docker compose ls --quiet 2>/dev/null | grep -q "$(basename "$ODS_DIR")"; then
        log_info "No running containers found"
        return 0
    fi

    cd "$ODS_DIR"
    if docker compose down; then
        log_success "Containers stopped"
    else
        log_warn "Some containers may not have stopped cleanly"
    fi
}

# Restore user data
restore_user_data() {
    local backup_dir="$1"
    log_step "Restoring user data..."

    local data_dirs=("data/open-webui" "data/n8n" "data/qdrant" "data/openclaw" "data/litellm" "data/livekit" "data/ollama")

    local restored_any=false
    for dir in "${data_dirs[@]}"; do
        if [[ -d "$backup_dir/$dir" ]]; then
            restored_any=true
            mkdir -p "$ODS_DIR/$(dirname "$dir")"
            # Note: Using -a without --delete to preserve any new files created after backup
            # Use --force flag or manually delete target if you need exact restoration
            rsync_with_progress "$backup_dir/$dir" "$ODS_DIR/$(dirname "$dir")/" "Restoring $dir"
            log_success "Restored: $dir"
        else
            log_warn "Skipped (not in backup): $dir"
        fi
    done

    if [[ "$restored_any" == "false" ]]; then
        log_warn "No user data directories were found in this backup."
    fi
}

# Restore configuration
restore_config() {
    local backup_dir="$1"
    log_step "Restoring configuration..."

    local restored_any=false

    # Dynamically discover config files (dotfiles + compose overlays + scripts)
    for file in "$backup_dir"/.env "$backup_dir"/.version "$backup_dir"/docker-compose*.y*ml "$backup_dir"/ods-*.sh; do
        if [[ -f "$file" ]]; then
            restored_any=true
            cp "$file" "$ODS_DIR/"
            log_success "Restored: $(basename "$file")"
        fi
    done

    if [[ -d "$backup_dir/config" ]]; then
        restored_any=true
        if [[ -d "$ODS_DIR/config" ]]; then
            rm -rf "$ODS_DIR/config"
        fi
        cp -r "$backup_dir/config" "$ODS_DIR/"
        log_success "Restored: config/"
    else
        log_warn "Skipped (not in backup): config/"
    fi

    if [[ "$restored_any" == "false" ]]; then
        log_warn "No configuration files were found in this backup."
    fi
}

# Verify restore
verify_restore() {
    log_step "Verifying restore..."

    local all_good=true

    # Check critical paths
    # Check that at least one compose file exists (base or standalone)
    local has_compose=false
    for f in "$ODS_DIR"/docker-compose*.y*ml; do
        [[ -f "$f" ]] && has_compose=true && break
    done
    if [[ "$has_compose" == "false" ]]; then
        log_warn "Missing after restore: no docker-compose*.yml files found"
        all_good=false
    fi
    if [[ ! -d "$ODS_DIR/data/open-webui" ]]; then
        log_warn "Missing after restore: data/open-webui"
        all_good=false
    fi

    if [[ "$all_good" == "true" ]]; then
        log_success "Restore verification passed"
        return 0
    else
        log_warn "Some paths may be missing (this may be normal if they weren't in backup)"
        return 0
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
        read -rp "Type the backup ID ('$backup_id') to continue, or press Enter to cancel: " confirm
        if [[ "$confirm" != "$backup_id" ]]; then
            log_info "Restore cancelled"
            return 0
        fi
    fi

    # Stop containers if requested
    if [[ "$stop_first" == "true" ]]; then
        stop_containers
    fi

    # Perform restore
    if [[ "$restore_data" == "true" ]]; then
        restore_user_data "$backup_dir"
    fi

    if [[ "$restore_config" == "true" ]]; then
        restore_config "$backup_dir"
    fi

    # Verify
    verify_restore

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
        read -rp "Continue anyway? [y/N] " confirm
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi

    # Perform restore
    do_restore "$backup_id" "$force" "$dry_run" "$stop_first" "$restore_data" "$restore_config" "$skip_verify"
}

main "$@"
