#!/usr/bin/env bash
# Pixel capability, license, source, and secret helpers for the ODS installer.
# Importing this file has no side effects.

_ods_pixel_secure_regular_file() {
    local path="$1"
    local required_uid="${2:-}"
    [[ -f "$path" && ! -L "$path" ]] || return 1

    local owner mode
    owner="$(stat -c '%u' -- "$path" 2>/dev/null)" || return 1
    mode="$(stat -c '%a' -- "$path" 2>/dev/null)" || return 1
    [[ "$owner" =~ ^[0-9]+$ && "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
    [[ -z "$required_uid" || "$owner" == "$required_uid" ]] || return 1
    (( (8#$mode & 0022) == 0 )) || return 1
}

_ods_pixel_parse_os_release() {
    local requested="${1:-/etc/os-release}"
    local path="$requested"
    local required_uid=""

    if [[ "$requested" == "/etc/os-release" ]]; then
        required_uid=0
        if [[ -L "$requested" ]]; then
            path="$(readlink -f -- "$requested" 2>/dev/null)" || return 1
            [[ "$path" == "/usr/lib/os-release" ]] || return 1
        fi
    elif [[ -L "$requested" ]]; then
        return 1
    fi
    _ods_pixel_secure_regular_file "$path" "$required_uid" || return 1

    _ODS_PIXEL_OS_ID=""
    _ODS_PIXEL_OS_VERSION_ID=""
    local saw_id=0 saw_version=0 line key raw value
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" != *$'\r'* ]] || return 1
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]] || return 1
        key="${BASH_REMATCH[1]}"
        raw="${BASH_REMATCH[2]}"

        case "$key" in
            ID|VERSION_ID)
                if [[ "$raw" =~ ^\"([A-Za-z0-9._-]+)\"$ ]]; then
                    value="${BASH_REMATCH[1]}"
                elif [[ "$raw" =~ ^\'([A-Za-z0-9._-]+)\'$ ]]; then
                    value="${BASH_REMATCH[1]}"
                elif [[ "$raw" =~ ^[A-Za-z0-9._-]+$ ]]; then
                    value="$raw"
                else
                    return 1
                fi
                if [[ "$key" == "ID" ]]; then
                    (( saw_id == 0 )) || return 1
                    saw_id=1
                    _ODS_PIXEL_OS_ID="$value"
                else
                    (( saw_version == 0 )) || return 1
                    saw_version=1
                    _ODS_PIXEL_OS_VERSION_ID="$value"
                fi
                ;;
            *)
                # Unknown os-release fields are inert data and are never sourced.
                ;;
        esac
    done < "$path"

    (( saw_id == 1 && saw_version == 1 ))
}

# Qualified Pixel hosts are intentionally narrower than ODS support: native
# Ubuntu 24.04, Debian 12, or Ubuntu 24.04 on WSL2, all with live PID1 systemd.
ods_pixel_host_qualified() {
    local os_release_path="${1:-/etc/os-release}"
    local proc1_comm_path="${2:-/proc/1/comm}"
    local proc_version_path="${3:-/proc/version}"

    [[ "$(uname -s 2>/dev/null)" == "Linux" ]] || return 1
    _ods_pixel_secure_regular_file "$proc1_comm_path" || return 1
    _ods_pixel_secure_regular_file "$proc_version_path" || return 1

    local pid1_comm
    IFS= read -r pid1_comm < "$proc1_comm_path" || return 1
    [[ "$pid1_comm" == "systemd" ]] || return 1

    if grep -Eqi 'microsoft|wsl' "$proc_version_path"; then
        grep -Eqi 'microsoft-standard.*wsl2|wsl2' "$proc_version_path" || return 1
    fi

    _ods_pixel_parse_os_release "$os_release_path" || return 1
    [[ "$_ODS_PIXEL_OS_ID" == "ubuntu" && "$_ODS_PIXEL_OS_VERSION_ID" == "24.04" ]] && return 0
    [[ "$_ODS_PIXEL_OS_ID" == "debian" && "$_ODS_PIXEL_OS_VERSION_ID" == "12" ]]
}

ods_pixel_license_accepted() {
    [[ "${PIXEL_LICENSE_ACCEPTED:-}" == "true" ]]
}

# Usage: ods_pixel_resolve_enablement true|false|auto [os-release] [pid1-comm] [proc-version]
ods_pixel_resolve_enablement() {
    local requested="${1:-}"
    local os_release_path="${2:-/etc/os-release}"
    local proc1_comm_path="${3:-/proc/1/comm}"
    local proc_version_path="${4:-/proc/version}"
    local qualified=false licensed=false

    ods_pixel_host_qualified "$os_release_path" "$proc1_comm_path" "$proc_version_path" && qualified=true
    ods_pixel_license_accepted && licensed=true

    case "$requested" in
        false)
            printf '%s\n' hermes
            ;;
        auto)
            if [[ "$qualified" == true && "$licensed" == true ]]; then
                printf '%s\n' pixel
            else
                printf '%s\n' hermes
            fi
            ;;
        true)
            if [[ "$qualified" == true && "$licensed" == true ]]; then
                printf '%s\n' pixel
            else
                printf '%s\n' 'error: pixel-prerequisites-not-met' >&2
                return 1
            fi
            ;;
        *)
            printf '%s\n' 'error: invalid-pixel-enablement' >&2
            return 1
            ;;
    esac
}

# Classify only model routes that the Pixel host installer can bind through the
# authenticated ODS LiteLLM gateway. Phase 02 validates external endpoints and
# Phase 06 renders their concrete model behind the same authenticated alias.
ods_pixel_model_route_class() {
    local mode="${1:-local}"
    local external_url="${2:-}"
    local lemonade_external="${3:-false}"
    [[ "$external_url" != *$'\n'* && "$external_url" != *$'\r'* ]] || return 1
    [[ "$lemonade_external" == true || "$lemonade_external" == false ]] || return 1
    if [[ -n "$external_url" ]]; then
        printf '%s\n' managed-gateway
        return 0
    fi
    if [[ "$lemonade_external" == true ]]; then
        printf '%s\n' managed-gateway
        return 0
    fi
    case "$mode" in
        local) printf '%s\n' local ;;
        cloud|hybrid|lemonade) printf '%s\n' managed-gateway ;;
        *) return 1 ;;
    esac
}

_ods_pixel_secure_owner_directory() {
    local path="$1"
    [[ "$path" == /* && -d "$path" && ! -L "$path" ]] || return 1
    local owner mode
    owner="$(stat -c '%u' -- "$path" 2>/dev/null)" || return 1
    mode="$(stat -c '%a' -- "$path" 2>/dev/null)" || return 1
    [[ "$owner" == "$(id -u)" && "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
    (( (8#$mode & 0022) == 0 ))
}

# Accept only the canonical Pixel repository or an owner-controlled local Git
# checkout below PIXEL_SOURCE_DIR. Always require an immutable full commit SHA.
ods_pixel_validate_source() {
    local source="${PIXEL_SOURCE_URL:-}"
    local ref="${PIXEL_SOURCE_REF:-}"
    local owner_root="${PIXEL_SOURCE_DIR:-}"

    [[ "$ref" =~ ^[0-9a-f]{40}$ ]] || {
        printf '%s\n' 'error: invalid-pixel-source-ref' >&2
        return 1
    }
    [[ "$source" != *$'\n'* && "$source" != *$'\r'* ]] || return 1

    if [[ "$source" == "https://github.com/Osmantic/Pixel.git" ]]; then
        return 0
    fi

    [[ "$source" == /* && "$owner_root" == /* ]] || {
        printf '%s\n' 'error: invalid-pixel-source' >&2
        return 1
    }
    _ods_pixel_secure_owner_directory "$owner_root" || return 1
    _ods_pixel_secure_owner_directory "$source" || return 1

    local resolved_root resolved_source
    resolved_root="$(cd "$owner_root" && pwd -P)" || return 1
    resolved_source="$(cd "$source" && pwd -P)" || return 1
    case "$resolved_source" in
        "$resolved_root"/*) ;;
        *) return 1 ;;
    esac

    [[ -d "$resolved_source/.git" && ! -L "$resolved_source/.git" ]] || return 1
}

# Validate and retain the resolved immutable Pixel source for later installer
# phases. `NAME=value function` assignments are temporary in Bash, so Phase 06
# must deliberately export this contract before Phase 11 installs Pixel.
ods_pixel_activate_source_contract() {
    local source="${1:-}" ref="${2:-}" owner_root="${3:-}"
    PIXEL_SOURCE_URL="$source"
    PIXEL_SOURCE_REF="$ref"
    PIXEL_SOURCE_DIR="$owner_root"
    if ! ods_pixel_validate_source; then
        unset PIXEL_SOURCE_URL PIXEL_SOURCE_REF PIXEL_SOURCE_DIR
        return 1
    fi
    export PIXEL_SOURCE_URL PIXEL_SOURCE_REF PIXEL_SOURCE_DIR
}

ods_pixel_generate_key() {
    local key=""
    if command -v openssl >/dev/null 2>&1; then
        key="$(openssl rand -hex 32 2>/dev/null)"
    fi
    if [[ ! "$key" =~ ^[0-9a-f]{64}$ ]]; then
        key="$(od -An -tx1 -N32 /dev/urandom 2>/dev/null | tr -d ' \n')"
    fi
    [[ "$key" =~ ^[0-9a-f]{64}$ ]] || return 1
    printf '%s\n' "$key"
}

# Reconcile the installed Pixel Compose fragment after the source tree has
# been copied into an existing install. rsync deliberately preserves runtime
# data and does not use --delete, so a previously enabled compose.yaml would
# otherwise survive after Phase 3 renamed the source copy to
# compose.yaml.disabled. That stale fragment can keep Pixel selected or make
# Compose interpolation fail after the Pixel secrets are correctly removed.
ods_pixel_reconcile_installed_compose() {
    local source_root="${1:-}"
    local install_root="${2:-}"
    local enabled="${3:-}"
    [[ "$source_root" == /* && "$install_root" == /* ]] || return 1

    local source_service="$source_root/extensions/services/pixel-edge"
    local installed_service="$install_root/extensions/services/pixel-edge"
    [[ -d "$source_service" && -d "$installed_service" ]] || return 1

    case "$enabled" in
        true)
            [[ -f "$source_service/compose.yaml" && ! -L "$source_service/compose.yaml" ]] || return 1
            rm -f -- "$installed_service/compose.yaml.disabled" || return 1
            [[ -f "$installed_service/compose.yaml" && ! -L "$installed_service/compose.yaml" ]]
            ;;
        false)
            [[ ! -e "$source_service/compose.yaml" && ! -L "$source_service/compose.yaml" ]] || return 1
            [[ -f "$source_service/compose.yaml.disabled" && ! -L "$source_service/compose.yaml.disabled" ]] || return 1
            rm -f -- "$installed_service/compose.yaml" || return 1
            [[ ! -e "$installed_service/compose.yaml" && ! -L "$installed_service/compose.yaml" ]]
            ;;
        *)
            return 1
            ;;
    esac
}
