#!/bin/bash
# ============================================================================
# ODS Installer — Path Utilities
# ============================================================================
# Part of: installers/lib/
# Purpose: Cross-platform path resolution and validation
#
# Expects: (nothing — can be sourced independently)
# Provides: resolve_install_dir(), validate_install_path(), normalize_path()
#
# Modder notes:
#   Add platform-specific path handling here.
# ============================================================================

# Normalize a path (resolve symlinks, remove trailing slashes, make absolute)
normalize_path() {
    local path="$1"
    
    # Handle empty path
    if [[ -z "$path" ]]; then
        echo ""
        return 1
    fi
    
    # Expand tilde to HOME
    path="${path/#\~/$HOME}"
    
    # Make absolute if relative
    if [[ "$path" != /* ]]; then
        path="$(pwd)/$path"
    fi
    
    # Resolve symlinks and normalize (remove .., ., //)
    if command -v realpath &>/dev/null; then
        # GNU realpath (Linux)
        realpath -m "$path" 2>/dev/null || echo "$path"
    elif command -v grealpath &>/dev/null; then
        # GNU realpath via Homebrew (macOS)
        grealpath -m "$path" 2>/dev/null || echo "$path"
    else
        # Fallback: basic normalization without external dependencies
        # This handles most cases but doesn't resolve all edge cases
        echo "$path"
    fi
}

# Resolve installation directory with precedence:
# 1. INSTALL_DIR env var (if set)
# 2. ODS_HOME env var (if set) - legacy macOS
# 3. ODS_SCRIPT_HINT env var, only when it points at a populated install
#    (detected by presence of a .env sentinel at that root). Callers set this
#    when they know the script lives inside the install dir (e.g. ods-macos.sh
#    exports SCRIPT_DIR before sourcing constants.sh). The sentinel guard
#    prevents false positives from /usr/local/bin PATH symlinks or scratch
#    copies that lack an installer-generated .env.
# 4. ODS_INSTALL_DIR env var (if set) - legacy macOS
# 5. Default: $HOME/ods
resolve_install_dir() {
    local resolved=""

    # Check precedence order
    if [[ -n "${INSTALL_DIR:-}" ]]; then
        resolved="$INSTALL_DIR"
    elif [[ -n "${ODS_HOME:-}" ]]; then
        resolved="$ODS_HOME"
    elif [[ -n "${ODS_SCRIPT_HINT:-}" ]] && [[ -f "${ODS_SCRIPT_HINT}/.env" ]]; then
        resolved="$ODS_SCRIPT_HINT"
    elif [[ -n "${ODS_INSTALL_DIR:-}" ]]; then
        resolved="$ODS_INSTALL_DIR"
    else
        resolved="$HOME/ods"
    fi
    
    # Normalize the path
    normalize_path "$resolved"
}

# Validate installation path (check writability, space, etc.)
validate_install_path() {
    local path="$1"
    local required_gb="${2:-20}"
    
    # Check if path is empty
    if [[ -z "$path" ]]; then
        echo "ERROR: Installation path is empty" >&2
        return 1
    fi
    
    # Check if parent directory exists and is writable
    local parent_dir
    parent_dir="$(dirname "$path")"
    
    if [[ ! -d "$parent_dir" ]]; then
        echo "ERROR: Parent directory does not exist: $parent_dir" >&2
        return 1
    fi
    
    if [[ ! -w "$parent_dir" ]]; then
        echo "ERROR: Parent directory is not writable: $parent_dir" >&2
        return 1
    fi
    
    # Check available disk space (POSIX-portable df -Pk)
    local avail_gb
    if command -v df &>/dev/null; then
        # Use df -Pk (POSIX) which returns KB, then convert to GB
        local avail_kb
        avail_kb=$(df -Pk "$parent_dir" 2>/dev/null | tail -1 | awk '{print $4}' || echo "0")
        avail_gb=$((avail_kb / 1048576))  # KB to GB (1024*1024)
        if [[ "$avail_gb" -lt "$required_gb" ]]; then
            echo "WARNING: Low disk space. Available: ${avail_gb}GB, Required: ${required_gb}GB" >&2
            return 2  # Warning, not fatal
        fi
    fi
    
    return 0
}

# Get platform-specific default install directory
get_default_install_dir() {
    case "$(uname -s)" in
        Darwin|Linux|*)
            # All platforms: use home directory (backward compatible)
            echo "$HOME/ods"
            ;;
    esac
}
