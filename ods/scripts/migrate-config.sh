#!/bin/bash
#=============================================================================
# Config Migration Manager for ODS
# 
# Handles configuration changes between versions.
# Automatically detects changes and migrates user configs safely.
#
# Usage:
#   ./migrate-config.sh check              # Check if migration needed
#   ./migrate-config.sh migrate            # Run pending migrations
#   ./migrate-config.sh diff               # Show what changed
#   ./migrate-config.sh backup             # Backup current config
#=============================================================================

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$(dirname "$SCRIPT_DIR")}"
DATA_DIR="${DATA_DIR:-$HOME/.ods}"
BACKUP_DIR="${DATA_DIR}/backups"
# Migrations live in INSTALL_DIR/migrations/ in deployed installs;
# in source tree they live at ods/migrations/ (peer of scripts/).
# SCRIPT_DIR is ods/scripts/ when run from source tree, or
# INSTALL_DIR/scripts/ when run from a deployed install — both resolve correctly.
MIGRATIONS_DIR="${SCRIPT_DIR}/../migrations"
VERSION_FILE="${INSTALL_DIR}/.version"
MIGRATION_STATE="${DATA_DIR}/.migration-state"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Get current version
get_current_version() {
    if [[ -f "$VERSION_FILE" ]]; then
        if jq -e '.version' "$VERSION_FILE" >/dev/null 2>&1; then
            jq -r '.version' "$VERSION_FILE"
        else
            cat "$VERSION_FILE" | tr -d '[:space:]'
        fi
    else
        echo "0.0.0"
    fi
}

# Get last migrated version
get_last_migrated_version() {
    if [[ -f "$MIGRATION_STATE" ]]; then
        cat "$MIGRATION_STATE" | tr -d '[:space:]'
    else
        echo "0.0.0"
    fi
}

# Set last migrated version
set_last_migrated_version() {
    local version="$1"
    mkdir -p "$(dirname "$MIGRATION_STATE")"
    echo "$version" > "$MIGRATION_STATE"
}

# Compare semantic versions
# Returns: 0 if equal, 1 if v1 > v2, 2 if v1 < v2
compare_versions() {
    local v1="${1#v}"
    local v2="${2#v}"
    
    if [[ "$v1" == "$v2" ]]; then
        return 0
    fi
    
    IFS='.' read -ra V1_PARTS <<< "$v1"
    IFS='.' read -ra V2_PARTS <<< "$v2"
    
    for i in {0..2}; do
        local p1="${V1_PARTS[$i]:-0}"
        local p2="${V2_PARTS[$i]:-0}"
        
        if [[ "$p1" -gt "$p2" ]]; then
            return 1
        elif [[ "$p1" -lt "$p2" ]]; then
            return 2
        fi
    done
    
    return 0
}

# Backup current configuration
cmd_backup() {
    log_info "Backing up current configuration..."
    
    local backup_name backup_path
    backup_name="config-$(date +%Y%m%d-%H%M%S)"
    backup_path="${BACKUP_DIR}/${backup_name}"
    
    mkdir -p "$backup_path"
    
    # Backup key config files
    local cp_exit=0
    cp "${INSTALL_DIR}/.env" "$backup_path/" 2>&1 || cp_exit=$?
    cp "${INSTALL_DIR}/.version" "$backup_path/" 2>&1 || cp_exit=$?
    cp "${INSTALL_DIR}/docker-compose.yml" "$backup_path/" 2>&1 || cp_exit=$?
    cp -r "${INSTALL_DIR}/config" "$backup_path/" 2>&1 || cp_exit=$?

    # Backup user data references
    cp -r "${DATA_DIR}" "$backup_path/data/" 2>&1 || cp_exit=$?
    
    log_success "Configuration backed up to: $backup_path"
    echo "$backup_path"
}

# Show diff between current and example configs
cmd_diff() {
    log_info "Checking for configuration changes..."
    
    local example_env="${INSTALL_DIR}/.env.example"
    local current_env="${INSTALL_DIR}/.env"
    
    if [[ ! -f "$example_env" ]]; then
        log_warn "No .env.example found — cannot show diff"
        return 1
    fi
    
    if [[ ! -f "$current_env" ]]; then
        log_warn "No .env found — user hasn't configured yet"
        return 1
    fi
    
    echo ""
    echo "=== Environment Variable Changes ==="
    echo ""
    
    # Show variables in example but not in current
    echo "New variables (add these to your .env):"
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        local grep_exit=0
        grep -q "^${key}=" "$current_env" 2>&1 || grep_exit=$?
        if [[ $grep_exit -ne 0 ]]; then
            echo "  + $key=$value"
        fi
    done < "$example_env"

    echo ""
    echo "Deprecated variables (remove from your .env):"
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        local grep_exit=0
        grep -q "^${key}=" "$example_env" 2>&1 || grep_exit=$?
        if [[ $grep_exit -ne 0 ]]; then
            echo "  - $key"
        fi
    done < "$current_env"
}

# Check if migration is needed
cmd_check() {
    local current_version
    local last_migrated
    
    current_version=$(get_current_version)
    last_migrated=$(get_last_migrated_version)
    
    log_info "Current version: $current_version"
    log_info "Last migrated: $last_migrated"
    
    # compare_versions returns a non-zero *ordering* status, so capture it with
    # `|| result=$?`. A bare call would trip `set -e` and abort before $? is
    # read. result 1 => current_version > last_migrated => newer migrations are
    # pending (this was previously checked as 2, i.e. a downgrade, so an ordinary
    # upgrade silently reported "no migration needed").
    local result=0
    compare_versions "$current_version" "$last_migrated" || result=$?

    if [[ $result -eq 1 ]]; then
        log_warn "Migration needed: $last_migrated → $current_version"
        
        # List pending migrations
        echo ""
        echo "Pending migrations:"
        for migration in "$MIGRATIONS_DIR"/migrate-v*.sh; do
            if [[ -f "$migration" ]]; then
                local migration_version
                migration_version=$(basename "$migration" | sed 's/migrate-v//;s/.sh//')
                
                local mig_cmp=0
                compare_versions "$migration_version" "$last_migrated" || mig_cmp=$?
                if [[ $mig_cmp -eq 1 ]]; then
                    echo "  - $migration_version: $(head -5 "$migration" | grep '^# Description:' | sed 's/# Description://')"
                fi
            fi
        done
        
        return 2
    else
        log_success "No migration needed (already at $current_version)"
        return 0
    fi
}

# Run pending migrations
cmd_migrate() {
    log_info "Starting configuration migration..."
    
    local current_version
    local last_migrated
    
    current_version=$(get_current_version)
    last_migrated=$(get_last_migrated_version)
    
    # Create backup first
    cmd_backup >/dev/null
    
    local cmp=0
    compare_versions "$current_version" "$last_migrated" || cmp=$?
    # Only current_version > last_migrated (cmp == 1) has pending migrations;
    # equal (0) or a downgrade (2) means there is nothing to apply. The former
    # `-ne 2` guard skipped every ordinary upgrade after taking a backup.
    if [[ $cmp -ne 1 ]]; then
        log_success "Already up to date ($current_version)"
        return 0
    fi
    
    log_info "Migrating: $last_migrated → $current_version"
    
    # Run migrations in order
    local failed=0
    local ls_exit=0
    local migrations
    migrations=$(ls -1 "$MIGRATIONS_DIR"/migrate-v*.sh 2>&1 | sort) || ls_exit=$?
    if [[ $ls_exit -ne 0 ]]; then
        log_success "No migration scripts found"
        return 0
    fi

    for migration in $migrations; do
        if [[ -f "$migration" ]]; then
            local migration_version
            migration_version=$(basename "$migration" | sed 's/migrate-v//;s/.sh//')
            
            # Check if this migration is needed
            local mig_cmp=0
            compare_versions "$migration_version" "$last_migrated" || mig_cmp=$?
            if [[ $mig_cmp -eq 1 ]]; then
                log_info "Running migration: $migration_version"
                
                if bash "$migration"; then
                    log_success "Migration $migration_version completed"
                    set_last_migrated_version "$migration_version"
                else
                    log_error "Migration $migration_version failed!"
                    failed=$((failed + 1))
                    break
                fi
            fi
        fi
    done
    
    if [[ $failed -eq 0 ]]; then
        set_last_migrated_version "$current_version"
        log_success "Migration complete! Updated to $current_version"
        return 0
    else
        log_error "Migration failed. Check logs and restore from backup."
        return 1
    fi
}

# Validate .env against schema
cmd_validate() {
    # MIGRATE_VALIDATOR lets the test suite point at a mock validator so it runs
    # hermetically instead of overwriting the tracked scripts/validate-env.sh.
    local validator="${MIGRATE_VALIDATOR:-${SCRIPT_DIR}/validate-env.sh}"
    local env_file="${INSTALL_DIR}/.env"
    local schema_file="${INSTALL_DIR}/.env.schema.json"

    if [[ ! -f "$validator" ]]; then
        log_error "Validator script missing: $validator"
        return 1
    fi
    if [[ ! -f "$schema_file" ]]; then
        log_error "Schema missing: $schema_file"
        return 1
    fi
    bash "$validator" "$env_file" "$schema_file"
}

# Show help
cmd_help() {
    cat << 'EOF'
ODS Config Migration Manager

Usage: ./migrate-config.sh [command]

Commands:
  check       Check if migration is needed
  migrate     Run pending migrations (with backup)
  diff        Show configuration differences
  backup      Backup current configuration
  validate    Validate .env against .env.schema.json
  help        Show this help message

Examples:
  ./migrate-config.sh check
  ./migrate-config.sh migrate
  ./migrate-config.sh diff
  ./migrate-config.sh validate

Migration scripts should be placed in the migrations/ directory
and named: migrate-vX.Y.Z.sh

EOF
}

# Main
case "${1:-help}" in
    check)
        cmd_check
        ;;
    migrate)
        cmd_migrate
        ;;
    diff)
        cmd_diff
        ;;
    backup)
        cmd_backup
        ;;
    validate)
        cmd_validate
        ;;
    help|--help|-h)
        cmd_help
        ;;
    *)
        log_error "Unknown command: $1"
        cmd_help
        exit 1
        ;;
esac
