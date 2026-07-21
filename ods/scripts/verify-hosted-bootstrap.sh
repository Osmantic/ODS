#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(git -C "$ROOT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
DEFAULT_ENDPOINTS=(
    "https://get.osmantic.com/ods"
    "https://get.osmantic.com/ods.sh"
    "https://get.osmantic.com/ods/main"
    "https://get.osmantic.com/ods/main.sh"
    "https://install.osmantic.com/ods"
    "https://install.osmantic.com/ods.sh"
    "https://install.osmantic.com/ods/main"
    "https://install.osmantic.com/ods/main.sh"
    "https://osmantic.com/get/ods"
    "https://osmantic.com/get/ods.sh"
    "https://osmantic.com/get/ods/main"
    "https://osmantic.com/get/ods/main.sh"
)
EXPECTED_CHANNEL="${ODS_HOSTED_BOOTSTRAP_CHANNEL:-main}"
EXPECTED_SOURCE_REF="${ODS_HOSTED_BOOTSTRAP_SOURCE_REF:-main}"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

header_value() {
    local headers_file="$1"
    local header_name="$2"

    awk -v target="$(printf '%s' "$header_name" | tr '[:upper:]' '[:lower:]')" '
        $1 ~ /^HTTP\/[0-9.]+$/ && $2 ~ /^[0-9][0-9][0-9]$/ {
            value = ""
            next
        }
        index(tolower($0), target ":") == 1 {
            line = $0
            sub(/\r$/, "", line)
            sub(/^[^:]*:[[:space:]]*/, "", line)
            sub(/[[:space:]]+$/, "", line)
            value = line
        }
        END { print value }
    ' "$headers_file"
}

[[ -n "$REPO_ROOT" ]] || fail "Run this verifier from an ODS Git checkout."
command -v curl >/dev/null 2>&1 || fail "curl is required."
command -v cmp >/dev/null 2>&1 || fail "cmp is required."

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 EXPECTED_GIT_REF [ENDPOINT ...]" >&2
    echo "Example: $0 \"\$(git rev-parse HEAD)\"" >&2
    exit 2
fi

EXPECTED_REF="$1"
shift
EXPECTED_SHA="$(git -C "$REPO_ROOT" rev-parse --verify "${EXPECTED_REF}^{commit}" 2>/dev/null)" \
    || fail "Cannot resolve expected Git ref: $EXPECTED_REF"

if [[ $# -eq 0 ]]; then
    set -- "${DEFAULT_ENDPOINTS[@]}"
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

EXPECTED_SCRIPT="$TMP_DIR/expected-get-ods.sh"
git -C "$REPO_ROOT" show "$EXPECTED_SHA:ods/get-ods.sh" > "$EXPECTED_SCRIPT" \
    || fail "$EXPECTED_SHA does not contain ods/get-ods.sh"

endpoint_index=0
for endpoint in "$@"; do
    endpoint_index=$((endpoint_index + 1))
    headers_file="$TMP_DIR/headers-$endpoint_index"
    body_file="$TMP_DIR/body-$endpoint_index"

    case "$endpoint" in
        https://*) ;;
        *) fail "Hosted bootstrap endpoint must use HTTPS: $endpoint" ;;
    esac

    curl \
        --fail \
        --silent \
        --show-error \
        --location \
        --proto '=https' \
        --proto-redir '=https' \
        --tlsv1.2 \
        --header 'Accept: text/plain' \
        --retry 3 \
        --retry-delay 1 \
        --connect-timeout 15 \
        --max-time 60 \
        --dump-header "$headers_file" \
        --output "$body_file" \
        "$endpoint" \
        || fail "Could not download hosted bootstrap: $endpoint"

    source_ref="$(header_value "$headers_file" "x-ods-source-ref")"
    channel="$(header_value "$headers_file" "x-ods-channel")"
    cache_state="$(header_value "$headers_file" "x-ods-cache")"
    content_type="$(header_value "$headers_file" "content-type")"
    presentation="$(header_value "$headers_file" "x-ods-presentation")"

    [[ -n "$source_ref" ]] \
        || fail "$endpoint did not return X-ODS-Source-Ref."
    [[ "$source_ref" == "$EXPECTED_SOURCE_REF" ]] \
        || fail "$endpoint reports source ref '$source_ref'; expected $EXPECTED_SOURCE_REF."

    if [[ -n "$EXPECTED_CHANNEL" ]]; then
        [[ "$channel" == "$EXPECTED_CHANNEL" ]] \
            || fail "$endpoint reports channel '${channel:-missing}'; expected $EXPECTED_CHANNEL."
    fi

    [[ "$presentation" == "script" ]] \
        || fail "$endpoint reports presentation '${presentation:-missing}'; expected script."

    media_type="$(printf '%s' "${content_type%%;*}" | tr '[:upper:]' '[:lower:]')"
    case "$media_type" in
        text/plain) ;;
        *) fail "$endpoint returned content type '${content_type:-missing}'; expected text/plain." ;;
    esac

    cmp -s "$EXPECTED_SCRIPT" "$body_file" \
        || fail "$endpoint body does not match $EXPECTED_SHA:ods/get-ods.sh."
    bash -n "$body_file" \
        || fail "$endpoint returned a bootstrap that does not pass bash -n."

    echo "[PASS] $endpoint -> $channel/$source_ref matches $EXPECTED_SHA (cache: ${cache_state:-unknown})"
done

echo "[PASS] Hosted bootstrap matches $EXPECTED_SHA from $EXPECTED_SOURCE_REF on $endpoint_index endpoint(s)."
