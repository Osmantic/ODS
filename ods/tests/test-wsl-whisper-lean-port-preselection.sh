#!/usr/bin/env bash
# A fresh lean WSL install must reserve Whisper's future Library port in both
# the installed dotenv and the Pixel installer environment.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
mkdir -p "$scratch/install"

cat > "$scratch/powershell.exe" <<'STUB'
#!/usr/bin/env bash
[[ "${ODS_TEST_PS_FAIL:-false}" != true ]] || exit 1
for port in ${ODS_TEST_PS_PORTS:-}; do printf '%s\r\n' "$port"; done
printf 'ODS_WINDOWS_PORT_SCAN_OK\r\n'
STUB
chmod +x "$scratch/powershell.exe"
export PATH="$scratch:$PATH"
export INSTALL_DIR="$scratch/install" SCRIPT_DIR="$root"
export ODS_WSL_HOST_OVERRIDE=true
source "$root/installers/lib/detection.sh"

warn() { :; }
log() { :; }
ai_ok() { :; }
_phase06_lemonade_uses_host_9000() { return 1; }
check_port_conflict() { ods_windows_host_port_in_use "$1"; }
_env_get() {
    local key="$1" fallback="$2" saved=""
    [[ ! -f "$INSTALL_DIR/.env" ]] || saved="$(sed -n "s/^${key}=//p" "$INSTALL_DIR/.env" | head -n 1)"
    printf '%s\n' "${saved:-$fallback}"
}
source <(sed -n '/^    _env_get_explicit_first() {/,/^    }/p' \
    "$root/installers/phases/06-directories.sh")

prepare_case() {
    unset WHISPER_PORT _ODS_WINDOWS_PORT_CACHE _ODS_WINDOWS_PORT_CACHE_READY
    unset ODS_TEST_PS_FAIL
    rm -f -- "$INSTALL_DIR/.env"
    ENABLE_WHISPER=false ENABLE_VOICE=false
    ODS_TEST_PS_PORTS="$1"
    export ODS_TEST_PS_PORTS
    declare -gA SERVICE_PORTS=([whisper]=9000)
}

apply_preselection() {
    source <(sed -n '/^# A native Windows application can own 9000/,/^# Port conflict detection with detailed process information/{
        /^# Port conflict detection with detailed process information/d
        p
    }' "$root/installers/phases/04-requirements.sh")
}

apply_phase06_port() {
    source <(sed -n '/^    WHISPER_PORT_VALUE=/,/^    # Preserve user-supplied cloud API keys/{
        /^    # Preserve user-supplied cloud API keys/d
        p
    }' "$root/installers/phases/06-directories.sh")
}

prepare_case '9000'
apply_preselection
[[ "${WHISPER_PORT:-}" == 9100 ]]
apply_phase06_port
[[ "$WHISPER_PORT_VALUE" == 9100 && "${SERVICE_PORTS[whisper]}" == 9100 ]]
[[ "$(bash -c 'printf %s "${WHISPER_PORT:-9000}"')" == 9100 ]]

prepare_case '9000 9100'
apply_preselection
[[ "${WHISPER_PORT:-}" == 9001 ]]

prepare_case '9000 9100 9001'
apply_preselection
[[ -z "${WHISPER_PORT:-}" ]]

prepare_case ''
apply_preselection
[[ -z "${WHISPER_PORT:-}" ]]
[[ "$(_env_get_explicit_first WHISPER_PORT 9000)" == 9000 ]]

prepare_case '9000'
WHISPER_PORT=9000
apply_preselection
[[ "$WHISPER_PORT" == 9000 ]]

prepare_case '9000'
printf 'WHISPER_PORT=9000\n' > "$INSTALL_DIR/.env"
apply_preselection
[[ -z "${WHISPER_PORT:-}" ]]
[[ "$(_env_get_explicit_first WHISPER_PORT 9000)" == 9000 ]]

prepare_case '9000 9100'
printf 'WHISPER_PORT=9100\n' > "$INSTALL_DIR/.env"
apply_preselection
[[ -z "${WHISPER_PORT:-}" ]]
apply_phase06_port
[[ "$WHISPER_PORT_VALUE" == 9100 && "${SERVICE_PORTS[whisper]}" == 9100 ]]

prepare_case '9000'
ODS_TEST_PS_FAIL=true
export ODS_TEST_PS_FAIL
apply_preselection
[[ -z "${WHISPER_PORT:-}" ]]

prepare_case '9000'
ENABLE_WHISPER=true
apply_preselection
[[ "$WHISPER_PORT" == 9100 ]]

printf 'PASS: WSL lean install preselects a future Whisper port and preserves owner choices\n'
