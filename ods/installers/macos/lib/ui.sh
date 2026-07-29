#!/bin/bash
# ============================================================================
# ODS macOS Installer -- UI Helpers
# ============================================================================
# Part of: installers/macos/lib/
# Purpose: Colored output, phase headers, progress, banners
#
# Matches the CRT narrator voice from installers/lib/ui.sh
# ============================================================================

DIVIDER="──────────────────────────────────────────────────────────────────────────────"

# Elapsed time since install start
install_elapsed() {
    local now_epoch="${INSTALL_NOW_EPOCH:-$(date +%s)}"
    local secs=$(( now_epoch - INSTALL_START_EPOCH ))
    local m=$(( secs / 60 ))
    local s=$(( secs % 60 ))
    printf '%dm %02ds' "$m" "$s"
}

# ── Logging ──

log() { echo -e "${GRN}[INFO]${NC} $1" | tee -a "$ODS_LOG_FILE"; }

# ── AI narrator voice ──

ai()       { echo -e "  ${GRN}>${NC} $1" | tee -a "$ODS_LOG_FILE"; }
ai_ok()    { echo -e "  ${BGRN}[OK]${NC} $1" | tee -a "$ODS_LOG_FILE"; }
ai_warn()  { echo -e "  ${AMB}[!!]${NC} $1" | tee -a "$ODS_LOG_FILE"; }
ai_err()   { echo -e "  ${RED}[XX]${NC} $1" | tee -a "$ODS_LOG_FILE"; }
info_box() { echo -e "  ${DGRN}$1${NC} ${WHT}$2${NC}" | tee -a "$ODS_LOG_FILE"; }

# Section header
chapter() {
    local title="$1"
    echo ""
    echo -e "  ${DGRN}$(printf '=%.0s' {1..60})${NC}"
    echo -e "  ${WHT}${title}${NC}"
    echo -e "  ${DGRN}$(printf '=%.0s' {1..60})${NC}"
}

# Phase screen
show_phase() {
    local phase=$1 total=$2 name=$3 estimate=$4
    local elapsed
    elapsed=$(install_elapsed)
    echo ""
    echo -e "  ${DGRN}ODSGATE SEQUENCE [${elapsed}]${NC}  ${WHT}PHASE ${phase}/${total}${NC} ${BGRN}-- ${name}${NC}"
    if [[ -n "$estimate" ]]; then
        echo -e "  ${DGRN}Estimated: ${estimate}${NC}"
    fi
    echo -e "  ${DGRN}$(printf -- '-%.0s' {1..60})${NC}"
}

# Boot banner
show_ods_banner() {
    echo ""
    echo -e "${BGRN}   OOOOO  DDDD   SSSSS${NC}"
    echo -e "${BGRN}  OO   OO DD DD SS${NC}"
    echo -e "${BGRN}  OO   OO DD DD  SSS${NC}"
    echo -e "${BGRN}  OO   OO DD DD    SS${NC}"
    echo -e "${BGRN}   OOOOO  DDDD  SSSS${NC}"
    echo ""
    echo -e "  ${WHT}ODSGATE macOS Installer v${ODS_VERSION}${NC}"
    echo -e "  ${DGRN}One command to a full local AI stack.${NC}"
    echo -e "  ${DGRN}Apple Silicon + Metal acceleration${NC}"
    echo ""
}

download_hf_artifact_with_python() {
    local url="$1"
    local destination="$2"
    local helper=""
    local python_cmd="${PYTHON_CMD:-}"
    local log_file="${ODS_LOG_FILE:-/tmp/ods-install.log}"

    case "$url" in
        https://huggingface.co/*|https://www.huggingface.co/*|https://hf.co/*) ;;
        *) return 2 ;;
    esac

    for candidate in \
        "${INSTALL_DIR:-}/scripts/download-hf-artifact.py" \
        "${SOURCE_ROOT:-}/scripts/download-hf-artifact.py"; do
        if [[ -f "$candidate" ]]; then
            helper="$candidate"
            break
        fi
    done
    [[ -n "$helper" ]] || return 2

    if [[ -z "$python_cmd" ]]; then
        python_cmd="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
    fi
    [[ -n "$python_cmd" ]] || return 2

    if ! "$python_cmd" -c "import huggingface_hub, hf_xet" >/dev/null 2>&1; then
        "$python_cmd" -m pip install --user -q "huggingface_hub[hf_xet]>=0.27" \
            >> "$log_file" 2>&1 || true
    fi

    ai "Retrying with Hugging Face client..."
    "$python_cmd" "$helper" "$url" "$destination" >> "$log_file" 2>&1
}

# Download with curl, resume support, and retry logic
download_with_progress() {
    local url="$1"
    local destination="$2"
    local label="${3:-Downloading}"
    local max_retries="${4:-${ODS_MODEL_DOWNLOAD_RETRIES:-8}}"

    case "$max_retries" in
        ''|*[!0-9]*|0) max_retries=8 ;;
    esac

    local part_file="${destination}.part"
    local connect_timeout="${ODS_DOWNLOAD_CONNECT_TIMEOUT:-30}"
    local low_speed_time="${ODS_DOWNLOAD_LOW_SPEED_TIME:-120}"
    local low_speed_limit="${ODS_DOWNLOAD_LOW_SPEED_LIMIT:-262144}"
    local http_version="${ODS_DOWNLOAD_HTTP_VERSION:-${ODS_BOOTSTRAP_DOWNLOAD_HTTP_VERSION:-http1.1}}"
    local curl_http_flags=()
    local attempt=1

    case "$http_version" in
        ""|auto|AUTO|Auto)
            ;;
        1|1.1|http1|HTTP1|http1.1|HTTP1.1)
            curl_http_flags=(--http1.1)
            ;;
        2|http2|HTTP2)
            curl_http_flags=(--http2)
            ;;
        *)
            ai_warn "Unknown ODS_DOWNLOAD_HTTP_VERSION=${http_version}; using http1.1."
            curl_http_flags=(--http1.1)
            ;;
    esac

    while [[ $attempt -le $max_retries ]]; do
        if [[ $attempt -gt 1 ]]; then
            local wait_time=$((2 ** (attempt - 1)))
            [[ "$wait_time" -gt 60 ]] && wait_time=60
            ai "Retry attempt $attempt of $max_retries (waiting ${wait_time}s)..."
            sleep $wait_time
        else
            ai "${label}..."
        fi

        if curl -C - -L --progress-bar \
            --connect-timeout "$connect_timeout" \
            --speed-time "$low_speed_time" --speed-limit "$low_speed_limit" \
            "${curl_http_flags[@]}" \
            -o "$part_file" "$url"; then
            mv "$part_file" "$destination"
            ai_ok "${label} complete"
            return 0
        else
            local rc=$?
            if download_hf_artifact_with_python "$url" "$part_file"; then
                mv "$part_file" "$destination"
                ai_ok "${label} complete"
                return 0
            fi
            if [[ $attempt -eq $max_retries ]]; then
                ai_err "${label} failed after $max_retries attempts (curl exit code: ${rc})"
                ai "Re-run the installer to resume the download."
                return 1
            else
                ai_warn "${label} failed (attempt $attempt/$max_retries, curl exit code: ${rc})"
            fi
        fi

        attempt=$((attempt + 1))
    done
}

# Verify file integrity using SHA256 checksum
verify_sha256() {
    local file_path="$1"
    local expected_hash="$2"
    local label="${3:-File}"

    if [[ ! -f "$file_path" ]]; then
        ai_err "${label} not found: $file_path"
        return 1
    fi

    if [[ -z "$expected_hash" ]]; then
        ai_warn "No SHA256 hash provided for ${label}, skipping verification"
        return 0
    fi

    ai "Verifying ${label} integrity (SHA256)..."

    local actual_hash
    if command -v shasum &>/dev/null; then
        # macOS native shasum
        actual_hash=$(shasum -a 256 "$file_path" 2>/dev/null | awk '{print $1}')
    elif command -v sha256sum &>/dev/null; then
        # GNU coreutils (if installed via Homebrew)
        actual_hash=$(sha256sum "$file_path" 2>/dev/null | awk '{print $1}')
    else
        ai_warn "Neither shasum nor sha256sum available, skipping verification"
        return 0
    fi

    if [[ -z "$actual_hash" ]]; then
        ai_warn "Could not compute checksum for ${label}"
        return 2
    fi

    if [[ "$actual_hash" == "$expected_hash" ]]; then
        ai_ok "${label} verified OK"
        return 0
    else
        ai_err "${label} is corrupt (SHA256 mismatch)"
        ai "  Expected: $expected_hash"
        ai "  Got:      $actual_hash"
        return 1
    fi
}

# Success card
show_success_card() {
    local webui_port="${1:-3000}"
    local dashboard_port="${2:-3001}"

    # Detect local IP for network access
    local local_ip
    local_ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "your-ip")

    echo ""
    echo -e "  ${BGRN}$(printf '=%.0s' {1..60})${NC}"
    echo ""
    echo -e "       ${WHT}THE GATEWAY IS OPEN${NC}"
    echo ""
    echo -e "       ${DGRN}Chat UI:${NC}    ${WHT}http://localhost:${webui_port}${NC}"
    echo -e "       ${DGRN}Dashboard:${NC}  ${WHT}http://localhost:${dashboard_port}${NC}"
    local _bind
    _bind=$(grep "^BIND_ADDRESS=" "$ODS_INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' || echo "127.0.0.1")
    [[ -z "$_bind" ]] && _bind="127.0.0.1"
    if [[ "$_bind" == "0.0.0.0" ]]; then
        echo -e "       ${DGRN}Network:${NC}    ${WHT}http://${local_ip}:${webui_port}${NC}"
    else
        echo -e "       ${DGRN}LAN access:${NC} ${DIM}Set BIND_ADDRESS=0.0.0.0 in .env${NC}"
    fi
    echo ""
    echo -e "       ${DGRN}Manage:${NC}     ${GRN}./ods-macos.sh status${NC}"
    echo -e "       ${DGRN}Logs:${NC}       ${GRN}./ods-macos.sh logs llama-server${NC}"
    echo -e "       ${DGRN}Stop:${NC}       ${GRN}./ods-macos.sh stop${NC}"
    echo ""
    echo -e "       ${DGRN}Install completed in $(install_elapsed)${NC}"
    echo ""
    echo -e "  ${BGRN}$(printf '=%.0s' {1..60})${NC}"
    echo ""
}
