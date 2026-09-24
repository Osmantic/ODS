#!/bin/bash
# Configure a client-only launcher for the ODS Talk portal on Android Termux
# and iOS a-Shell. Mobile devices connect to an existing ODS server; they do
# not attempt to run the Docker stack locally.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$SCRIPT_DIR/installers/common.sh"

server_url=""
open_portal=true
dry_run=false

show_help() {
    cat <<'EOF'
ODS mobile remote-portal bootstrap

USAGE:
    install-mobile.sh [--server URL] [--no-open] [--dry-run]

OPTIONS:
    --server URL   Existing ODS server origin or /talk URL (http/https)
    --no-open      Install the launcher without opening the portal
    --dry-run      Print the resolved plan without writing files
    --help, -h     Show this help

The portal URL is stored in ~/.config/ods/mobile-portal-url. The launcher is
installed to ~/.local/bin/ods-mobile on Termux or ~/bin/ods-mobile on a-Shell.
Override those roots with ODS_MOBILE_BIN_DIR and ODS_MOBILE_CONFIG_DIR.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --server)
            if [[ $# -lt 2 || "$2" == -* ]]; then
                echo "[ERROR] --server requires an http:// or https:// URL." >&2
                exit 2
            fi
            server_url="$2"
            shift 2
            ;;
        --no-open)
            open_portal=false
            shift
            ;;
        --dry-run)
            dry_run=true
            shift
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            echo "[ERROR] Unknown mobile installer option: $1" >&2
            show_help >&2
            exit 2
            ;;
    esac
done

platform="$(detect_platform)"
case "$platform" in
    android-termux|ios-ashell) ;;
    *)
        echo "[ERROR] install-mobile.sh only handles Android Termux or iOS a-Shell." >&2
        exit 1
        ;;
esac

if [[ -z "$server_url" ]]; then
    if [[ ! -t 0 ]]; then
        echo "[ERROR] --server is required when input is non-interactive." >&2
        exit 2
    fi
    printf 'Existing ODS server URL: '
    IFS= read -r server_url
fi

if [[ ! "$server_url" =~ ^https?://[^[:space:]?#]+/?$ ]]; then
    echo "[ERROR] Server URL must use http/https and cannot contain query parameters or fragments." >&2
    exit 2
fi

server_authority="${server_url#*://}"
server_authority="${server_authority%%/*}"
if [[ -z "$server_authority" || "$server_authority" == *"@"* ]]; then
    echo "[ERROR] Server URL must contain a host and cannot embed credentials." >&2
    exit 2
fi

server_url="${server_url%/}"
if [[ "$server_url" == */talk ]]; then
    portal_url="$server_url"
else
    portal_url="$server_url/talk"
fi

config_dir="${ODS_MOBILE_CONFIG_DIR:-$HOME/.config/ods}"
default_bin_dir="$HOME/.local/bin"
[[ "$platform" == "ios-ashell" ]] && default_bin_dir="$HOME/bin"
bin_dir="${ODS_MOBILE_BIN_DIR:-$default_bin_dir}"
config_file="$config_dir/mobile-portal-url"
launcher="$bin_dir/ods-mobile"

echo "[INFO] ODS mobile platform: $platform"
echo "[INFO] Remote portal: $portal_url"
echo "[INFO] Launcher: $launcher"

if [[ "$dry_run" == "true" ]]; then
    echo "[INFO] Dry run complete; no files were written."
    exit 0
fi

mkdir -p "$config_dir" "$bin_dir"
chmod 700 "$config_dir"

work_dir="$config_dir/.mobile-install.$$"
if ! (umask 077 && mkdir "$work_dir"); then
    echo "[ERROR] Cannot create mobile installer transaction directory: $work_dir" >&2
    exit 1
fi
config_tmp="$work_dir/mobile-portal-url"
launcher_tmp="$work_dir/ods-mobile"
cleanup() {
    rm -f "$config_tmp" "$launcher_tmp"
    rmdir "$work_dir"
}
trap cleanup EXIT

printf '%s\n' "$portal_url" > "$config_tmp"
chmod 600 "$config_tmp"

{
    echo '#!/bin/bash'
    echo 'set -euo pipefail'
    printf 'CONFIG_FILE=%q\n' "$config_file"
    cat <<'EOF'
if [[ ! -r "$CONFIG_FILE" ]]; then
    echo "[ERROR] ODS mobile portal configuration is missing: $CONFIG_FILE" >&2
    exit 1
fi
IFS= read -r portal_url < "$CONFIG_FILE"
if command -v termux-open-url >/dev/null 2>&1; then
    exec termux-open-url "$portal_url"
fi
if command -v open >/dev/null 2>&1; then
    exec open "$portal_url"
fi
echo "$portal_url"
echo "[INFO] No URL opener was found; open the URL above in your browser." >&2
EOF
} > "$launcher_tmp"
chmod 755 "$launcher_tmp"

mv -f "$config_tmp" "$config_file"
mv -f "$launcher_tmp" "$launcher"
rmdir "$work_dir"
trap - EXIT

echo "[INFO] Mobile launcher installed. Run: $launcher"
if [[ "$open_portal" == "true" ]]; then
    "$launcher"
fi
