#!/bin/bash
# ods-backup.sh - ODS Backup Utility
# Part of M11: Update & Lifecycle Management
# Backs up user data and config before updates

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="${ODS_DIR:-$SCRIPT_DIR}"
BACKUP_ROOT="${ODS_DIR}/.backups"
RETENTION_COUNT="${RETENTION_COUNT:-5}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Prerequisites check
command -v rsync >/dev/null 2>&1 || { echo -e "${RED}Error: rsync is required but not installed.${NC}" >&2; echo "Install with: apt install rsync (Debian/Ubuntu) or brew install rsync (macOS)" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo -e "${RED}Error: jq is required but not installed.${NC}" >&2; echo "Install with: apt install jq (Debian/Ubuntu) or brew install jq (macOS)" >&2; exit 1; }

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Source shared rsync utilities
. "$ODS_DIR/lib/rsync.sh"

# Convert bytes to a human-friendly string (best-effort)
fmt_bytes() {
    local bytes="${1:-0}"
    if command -v numfmt >/dev/null 2>&1; then
        numfmt --to=iec --suffix=B "$bytes" 2>/dev/null || echo "${bytes}B"
    else
        # Fallback: show MiB rounding
        local mib=$(( (bytes + 1048575) / 1048576 ))
        echo "${mib}MiB"
    fi
}

# Available bytes on filesystem containing a path
free_bytes_for_path() {
    local path="$1"
    # df -P gives POSIX output; field 4 = available 1K-blocks
    df -Pk "$path" 2>/dev/null | awk 'NR==2 { print $4 * 1024 }'
}

# Estimate bytes needed for a backup type (rough but safe)
estimate_backup_bytes() {
    local backup_type="$1"

    local total=0

    # user data volumes
    if [[ "$backup_type" == "full" || "$backup_type" == "user-data" ]]; then
        local -a user_data_paths=(
            "data/open-webui"
            "data/n8n"
            "data/qdrant"
            "data/openclaw"
            "data/litellm"
            "data/livekit"
            "data/ollama"
        )

        for p in "${user_data_paths[@]}"; do
            if [[ -d "$ODS_DIR/$p" ]]; then
                local b
                b=$(du -sk "$ODS_DIR/$p" 2>/dev/null | awk '{print $1 * 1024}')
                total=$(( total + ${b:-0} ))
            fi
        done
    fi

    # config files/dir
    if [[ "$backup_type" == "full" || "$backup_type" == "config" ]]; then
        if [[ -d "$ODS_DIR/config" ]]; then
            local b
            b=$(du -sk "$ODS_DIR/config" 2>/dev/null | awk '{print $1 * 1024}')
            total=$(( total + ${b:-0} ))
        fi
        for f in "$ODS_DIR"/.env "$ODS_DIR"/.version "$ODS_DIR"/docker-compose*.y*ml "$ODS_DIR"/ods-preflight.sh "$ODS_DIR"/ods-update.sh; do
            if [[ -f "$f" ]]; then
                local s
                s=$(wc -c < "$f" 2>/dev/null || echo 0)
                total=$(( total + ${s:-0} ))
            fi
        done
    fi

    # cache (models + some caches)
    if [[ "$backup_type" == "full" ]]; then
        if [[ -d "$ODS_DIR/models" ]]; then
            local b
            b=$(du -sk "$ODS_DIR/models" 2>/dev/null | awk '{print $1 * 1024}')
            total=$(( total + ${b:-0} ))
        fi
        local -a cache_paths=("data/whisper/cache" "data/kokoro/cache")
        for p in "${cache_paths[@]}"; do
            if [[ -d "$ODS_DIR/$p" ]]; then
                local b
                b=$(du -sk "$ODS_DIR/$p" 2>/dev/null | awk '{print $1 * 1024}')
                total=$(( total + ${b:-0} ))
            fi
        done
    fi

    # Add a small overhead buffer (manifest, filesystem slack, etc.)
    total=$(( total + 50 * 1024 * 1024 ))

    echo "$total"
}

ensure_backup_space() {
    local backup_type="$1"

    mkdir -p "$BACKUP_ROOT"

    local need
    need=$(estimate_backup_bytes "$backup_type")

    local free
    free=$(free_bytes_for_path "$BACKUP_ROOT")

    if [[ -n "$free" && "$free" -gt 0 && "$free" -lt "$need" ]]; then
        log_error "Not enough disk space in $(dirname "$BACKUP_ROOT") to create backup."
        log_error "Need ~$(fmt_bytes "$need"), have ~$(fmt_bytes "$free")."
        log_error "Free up space or use --output to write backups to another disk."
        exit 1
    fi
}

# Collect backup entries (dirs or .tar.gz archives) under BACKUP_ROOT into
# the global COLLECTED_BACKUPS array, newest first.
#
# Backup IDs are YYYYMMDD-HHMMSS (this script) or <prefix>-YYYYMMDD-HHMMSS
# (dashboard/host-agent). Match that shape explicitly: retention deletes
# whatever this collects, so an operator's unrelated files in the backup
# directory must never qualify.
COLLECTED_BACKUPS=()
collect_backups() {
    COLLECTED_BACKUPS=()
    local entry base
    while IFS= read -r -d '' entry; do
        base=$(basename "$entry")
        [[ "$base" =~ ^([A-Za-z0-9_]+-)?[0-9]{8}-[0-9]{6}(\.tar\.gz)?$ ]] || continue
        COLLECTED_BACKUPS+=("$entry")
    done < <(find "$BACKUP_ROOT" -maxdepth 1 \( -type d -o -name "*.tar.gz" \) -print0 2>/dev/null | sort -z -r)
}

# Show usage
usage() {
    cat << EOF
ODS Backup Utility

Usage: $(basename "$0") <command> [OPTIONS]

Commands:
    backup                   Create a backup (default)
    verify <backup_id>        Verify checksums for an existing backup


OPTIONS:
    -h, --help              Show this help message
    -o, --output DIR        Custom backup directory (default: .backups/)
    -t, --type TYPE         Backup type: full, user-data, config (default: full)
    -c, --compress          Compress backup to .tar.gz
    -l, --list              List existing backups
    -d, --delete ID         Delete specific backup by ID
    --description DESC      Add description to backup manifest

BACKUP TYPES:
    full        Backup everything (user data + config + cache)
    user-data   Backup only user data volumes (default)
    config      Backup only configuration files

EXAMPLES:
    $(basename "$0")                          # Backup (default: user-data)
    $(basename "$0") -t full -c               # Compressed full backup
    $(basename "$0") -l                       # List all backups
    $(basename "$0") -d 20260212-071500       # Delete specific backup
    $(basename "$0") verify 20260212-071500   # Verify checksum integrity
    $(basename "$0") verify 20260212-071500.tar.gz
    $(basename "$0") backup -t config         # Explicit backup subcommand

EOF
}

# List existing backups
list_backups() {
    if [[ ! -d "$BACKUP_ROOT" ]]; then
        log_info "No backups found (backup directory doesn't exist)"
        return 0
    fi

    collect_backups

    if [[ ${#COLLECTED_BACKUPS[@]} -eq 0 ]]; then
        log_info "No backups found"
        return 0
    fi

    echo ""
    echo "Existing Backups:"
    echo "═══════════════════════════════════════════════════════════════════"
    printf "%-20s %-12s %-10s %s\n" "ID" "Type" "Size" "Description"
    echo "───────────────────────────────────────────────────────────────────"

    for backup in "${COLLECTED_BACKUPS[@]}"; do
        local id
        id=$(basename "$backup")
        local backup_type="unknown"
        local description=""
        local size
        size=$(du -sh "$backup" 2>/dev/null | cut -f1)

        if [[ "$backup" == *.tar.gz ]]; then
            # Compressed archive — extract manifest from inside the tar
            local manifest_data
            local archive_name="${id%.tar.gz}"
            if manifest_data=$(tar xzf "$backup" -O "${archive_name}/manifest.json" 2>/dev/null); then
                backup_type=$(echo "$manifest_data" | grep -o '"backup_type": "[^"]*"' 2>/dev/null | cut -d'"' -f4 || echo "compressed")
                description=$(echo "$manifest_data" | grep -o '"description": "[^"]*"' 2>/dev/null | cut -d'"' -f4 || echo "")
            else
                backup_type="compressed"
            fi
        elif [[ -f "$backup/manifest.json" ]]; then
            backup_type=$(grep -o '"backup_type": "[^"]*"' "$backup/manifest.json" 2>/dev/null | cut -d'"' -f4 || echo "unknown")
            description=$(grep -o '"description": "[^"]*"' "$backup/manifest.json" 2>/dev/null | cut -d'"' -f4 || echo "")
        fi

        printf "%-20s %-12s %-10s %s\n" "$id" "$backup_type" "$size" "$description"
    done
    echo ""
}

# Delete specific backup
delete_backup() {
    local backup_id="$1"

    # Reject path traversal attempts
    if [[ "$backup_id" == *..* || "$backup_id" == */* || "$backup_id" == *\\* ]]; then
        log_error "Invalid backup ID: $backup_id"
        return 1
    fi

    local target="$BACKUP_ROOT/$backup_id"

    # Compressed backups are .tar.gz files, not directories; also accept the
    # bare ID for an archive-only backup.
    if [[ ! -e "$target" && -f "$target.tar.gz" ]]; then
        target="$target.tar.gz"
    fi

    if [[ ! -e "$target" ]]; then
        log_error "Backup not found: $backup_id"
        return 1
    fi

    read -rp "Are you sure you want to delete backup $(basename "$target")? [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        rm -rf "$target"
        log_success "Deleted backup: $(basename "$target")"
    else
        log_info "Deletion cancelled"
    fi
}

# Create backup manifest
create_manifest() {
    local backup_dir="$1"
    local backup_type="$2"
    local description="${3:-}"
    local version
    # .version is a JSON file written by ods-update.sh; extract the version
    # string rather than embedding the entire JSON blob (see get_current_version
    # in ods-update.sh for the canonical reader).
    version=$(jq -r '.version // "unknown"' "$ODS_DIR/.version" 2>/dev/null || echo "unknown")

    # Use jq to safely construct JSON (prevents injection via $description)
    local has_user_data="false" has_config="false" has_cache="false"
    [[ "$backup_type" == "full" || "$backup_type" == "user-data" ]] && has_user_data="true"
    [[ "$backup_type" == "full" || "$backup_type" == "config" ]] && has_config="true"
    [[ "$backup_type" == "full" ]] && has_cache="true"

    jq -n \
        --arg mv "1.0" \
        --arg bd "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        --arg bi "$(basename "$backup_dir")" \
        --arg bt "$backup_type" \
        --arg dv "$version" \
        --arg hn "$(hostname)" \
        --arg desc "$description" \
        --argjson ud "$has_user_data" \
        --argjson cfg "$has_config" \
        --argjson ca "$has_cache" \
        '{
          manifest_version: $mv,
          backup_date: $bd,
          backup_id: $bi,
          backup_type: $bt,
          ods_version: $dv,
          hostname: $hn,
          description: $desc,
          contents: { user_data: $ud, config: $cfg, cache: $ca },
          paths: {
            data_open_webui: "data/open-webui",
            data_n8n: "data/n8n",
            data_qdrant: "data/qdrant",
            data_openclaw: "data/openclaw",
            env: ".env",
            compose: "docker-compose.yml",
            config: "config"
          }
        }' > "$backup_dir/manifest.json"
    log_info "Created backup manifest"
}

# Backup user data volumes
backup_user_data() {
    local backup_dir="$1"
    log_info "Backing up user data volumes..."

    local user_data_paths=(
        "data/open-webui"
        "data/n8n"
        "data/qdrant"
        "data/openclaw"
        "data/litellm"
        "data/livekit"
        "data/ollama"
    )

    for path in "${user_data_paths[@]}"; do
        local full_path="$ODS_DIR/$path"
        if [[ -d "$full_path" ]]; then
            local dest_dir="$backup_dir/$(dirname "$path")"
            mkdir -p "$dest_dir"
            rsync_with_progress "$full_path" "$dest_dir/" "Backing up $path"
            log_success "Backed up: $path"
        else
            log_warn "Skipped (not found): $path"
        fi
    done
}

# Backup configuration
backup_config() {
    local backup_dir="$1"
    log_info "Backing up configuration..."

    # Essential config files: discover compose overlays + dotfiles dynamically
    for file in "$ODS_DIR"/.env "$ODS_DIR"/.version "$ODS_DIR"/docker-compose*.y*ml "$ODS_DIR"/ods-preflight.sh "$ODS_DIR"/ods-update.sh; do
        if [[ -f "$file" ]]; then
            cp "$file" "$backup_dir/"
            log_success "Backed up: $(basename "$file")"
        fi
    done

    # Config directory
    if [[ -d "$ODS_DIR/config" ]]; then
        rsync_with_progress "$ODS_DIR/config" "$backup_dir/" "Backing up config/"
        log_success "Backed up: config/"
    fi
}

# Generate a SHA256 manifest for all backed up files
# Writes: $backup_dir/checksums.sha256 (format: sha256  relative/path)
create_checksums() {
    local backup_dir="$1"
    log_info "Generating checksums..."

    local checksum_file="$backup_dir/checksums.sha256"

    if command -v sha256sum >/dev/null 2>&1; then
        (
            cd "$backup_dir"
            # Deterministic ordering; exclude the checksum file itself.
            find . -type f ! -name "checksums.sha256" -print0 \
                | sort -z \
                | xargs -0 sha256sum \
                | sed 's#  \./#  #' \
                > "$(basename "$checksum_file")"
        )
    elif command -v shasum >/dev/null 2>&1; then
        (
            cd "$backup_dir"
            find . -type f ! -name "checksums.sha256" -print0 \
                | sort -z \
                | xargs -0 shasum -a 256 \
                | sed 's#  \./#  #' \
                > "$(basename "$checksum_file")"
        )
    else
        log_warn "Skipping checksums: sha256sum/shasum not found"
        return 0
    fi

    log_info "Wrote checksums.sha256"
}

# Backup cache (optional, for full backups)
backup_cache() {
    local backup_dir="$1"
    log_info "Backing up cache (models, etc.)..."

    if [[ -d "$ODS_DIR/models" ]]; then
        rsync_with_progress "$ODS_DIR/models" "$backup_dir/" "Backing up models/"
        log_success "Backed up: models/"
    fi

    # Docker volumes that contain cache data
    local cache_paths=(
        "data/whisper/cache"
        "data/kokoro/cache"
    )

    for path in "${cache_paths[@]}"; do
        if [[ -d "$ODS_DIR/$path" ]]; then
            local dest_dir="$backup_dir/$(dirname "$path")"
            mkdir -p "$dest_dir"
            rsync_with_progress "$ODS_DIR/$path" "$dest_dir/" "Backing up $path"
            log_success "Backed up: $path"
        fi
    done
}

# Apply retention policy - keep only N most recent backups
apply_retention() {
    log_info "Applying retention policy (keeping last $RETENTION_COUNT backups)..."

    collect_backups

    local count=${#COLLECTED_BACKUPS[@]}
    if [[ $count -gt $RETENTION_COUNT ]]; then
        local to_delete=$((count - RETENTION_COUNT))
        log_info "Removing $to_delete old backup(s)..."

        for ((i=RETENTION_COUNT; i<count; i++)); do
            local old_backup="${COLLECTED_BACKUPS[$i]}"
            log_warn "Removing old backup: $(basename "$old_backup")"
            rm -rf "$old_backup"
        done
    else
        log_info "Retention policy satisfied ($count/$RETENTION_COUNT backups)"
    fi
}

# Compress backup
compress_backup() {
    local backup_dir="$1"
    log_info "Compressing backup..."

    local backup_name
    backup_name=$(basename "$backup_dir")
    local parent_dir
    parent_dir=$(dirname "$backup_dir")

    tar czf "$parent_dir/$backup_name.tar.gz" -C "$parent_dir" "$backup_name"
    # The archive bundles the raw .env (DASHBOARD_API_KEY, session secret, service
    # passwords). Restrict it to the owner rather than leaving it world-readable
    # at the umask default, matching the 0600 the .env itself carries.
    chmod 600 "$parent_dir/$backup_name.tar.gz"
    local compressed_size
    compressed_size=$(du -sh "$parent_dir/$backup_name.tar.gz" | cut -f1)

    # Remove uncompressed version
    rm -rf "$backup_dir"

    log_success "Compressed backup: $backup_name.tar.gz ($compressed_size)"
}

# Main backup function
do_backup() {
    local backup_type="${1:-user-data}"
    local compress="${2:-false}"
    local description="${3:-}"

    # Generate backup ID
    local backup_id
    backup_id=$(date +%Y%m%d-%H%M%S)
    local backup_dir="$BACKUP_ROOT/$backup_id"

    log_info "Starting $backup_type backup: $backup_id"
    log_info "Backup directory: $backup_dir"

    # Disk space preflight (best-effort)
    ensure_backup_space "$backup_type"

    # Create backup directory
    mkdir -p "$backup_dir"

    # Create manifest
    create_manifest "$backup_dir" "$backup_type" "$description"

    # Perform backup based on type
    case "$backup_type" in
        full)
            backup_user_data "$backup_dir"
            backup_config "$backup_dir"
            backup_cache "$backup_dir"
            ;;
        user-data)
            backup_user_data "$backup_dir"
            ;;
        config)
            backup_config "$backup_dir"
            ;;
        *)
            log_error "Unknown backup type: $backup_type"
            rm -rf "$backup_dir"
            exit 1
            ;;
    esac

    # Generate checksums after files are copied into place
    create_checksums "$backup_dir"

    # Compress if requested
    if [[ "$compress" == "true" ]]; then
        compress_backup "$backup_dir"
        backup_dir="$BACKUP_ROOT/$backup_id.tar.gz"
    fi

    # Apply retention policy
    apply_retention

    log_success "Backup complete: $backup_id"
    echo ""
    echo "To restore this backup, run:"
    echo "  ods-restore.sh $backup_id"
}

# Verify checksums for an existing backup directory or archive
verify_backup() {
    local backup_id="$1"

    # Reject path traversal attempts
    if [[ "$backup_id" == *..* || "$backup_id" == */* || "$backup_id" == *\\* ]]; then
        log_error "Invalid backup ID: $backup_id"
        return 1
    fi

    local target="$BACKUP_ROOT/$backup_id"

    if [[ -d "$target" ]]; then
        if [[ ! -f "$target/checksums.sha256" ]]; then
            log_error "checksums.sha256 not found for backup: $backup_id"
            return 1
        fi

        (
            cd "$target"
            if command -v sha256sum >/dev/null 2>&1; then
                sha256sum -c "checksums.sha256"
            else
                shasum -a 256 -c "checksums.sha256"
            fi
        )
        log_success "Backup verified: $backup_id"
        return 0
    fi

    if [[ -f "$target" && "$target" == *.tar.gz ]]; then
        local tmpdir
        tmpdir=$(mktemp -d)
        trap 'rm -rf "$tmpdir"' RETURN

        # Extract checksums first (fast fail if missing)
        local archive_name="${backup_id%.tar.gz}"
        if ! tar xzf "$target" -C "$tmpdir" "${archive_name}/checksums.sha256" >/dev/null 2>&1; then
            log_error "checksums.sha256 not found in archive: $backup_id"
            return 1
        fi

        tar xzf "$target" -C "$tmpdir"

        (
            cd "$tmpdir/$archive_name"
            if command -v sha256sum >/dev/null 2>&1; then
                sha256sum -c "checksums.sha256"
            else
                shasum -a 256 -c "checksums.sha256"
            fi
        )

        log_success "Backup verified: $backup_id"
        return 0
    fi

    log_error "Backup not found: $backup_id"
    return 1
}

# Main entry point
main() {
    local subcommand="backup"

    # Allow subcommands to appear either first (e.g. `verify ...`) or after options
    # (e.g. `--output /path verify ...`). We'll detect `backup`/`verify` during arg parsing.


    local backup_type="user-data"
    local compress="false"
    local description=""
    local list_mode="false"
    local delete_id=""
    local verify_id=""

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            backup)
                subcommand="backup"
                shift
                ;;
            verify)
                subcommand="verify"
                shift
                verify_id="${1:-}"
                if [[ -z "$verify_id" ]]; then
                    log_error "Usage: $(basename "$0") verify <backup_id|backup_id.tar.gz>"
                    exit 1
                fi
                shift
                # No more flags after verify are supported
                break
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            -o|--output)
                BACKUP_ROOT="$2"
                shift 2
                ;;
            -t|--type)
                backup_type="$2"
                shift 2
                ;;
            -c|--compress)
                compress="true"
                shift
                ;;
            -l|--list)
                list_mode="true"
                shift
                ;;
            -d|--delete)
                delete_id="$2"
                shift 2
                ;;
            --description)
                description="$2"
                shift 2
                ;;
            *)
                log_error "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done

    # List mode
    if [[ "$list_mode" == "true" ]]; then
        list_backups
        exit 0
    fi

    # Delete mode
    if [[ -n "$delete_id" ]]; then
        delete_backup "$delete_id"
        exit 0
    fi

    # Verify mode
    if [[ "$subcommand" == "verify" ]]; then
        # Create backup root so --output works even if dir doesn't exist yet
        mkdir -p "$BACKUP_ROOT"

        verify_backup "$verify_id"
        exit $?
    fi

    # Check if running in ODS directory
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

    # Create backup root
    mkdir -p "$BACKUP_ROOT"

    # Perform backup
    do_backup "$backup_type" "$compress" "$description"
}

main "$@"
