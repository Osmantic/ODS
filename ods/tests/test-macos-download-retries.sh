#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

export ODS_LOG_FILE="$TMP_DIR/install.log"
export INSTALL_START_EPOCH=0
export GRN="" BGRN="" AMB="" RED="" NC="" DGRN="" WHT=""

mkdir -p "$TMP_DIR/bin"
cat > "$TMP_DIR/bin/curl" <<'EOF'
#!/usr/bin/env bash
count_file="${ODS_TEST_CURL_COUNT_FILE:?}"
count=0
[[ -f "$count_file" ]] && count="$(cat "$count_file")"
count=$((count + 1))
printf '%s\n' "$count" > "$count_file"
exit "${ODS_TEST_CURL_EXIT:-56}"
EOF
cat > "$TMP_DIR/bin/sleep" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMP_DIR/bin/curl" "$TMP_DIR/bin/sleep"
export PATH="$TMP_DIR/bin:$PATH"

# shellcheck source=/dev/null
source "$ROOT_DIR/installers/macos/lib/ui.sh"

run_failure_case() {
    local label="$1"
    local expected="$2"
    local retries="${3:-__unset__}"

    local count_file="$TMP_DIR/${label}.count"
    local target="$TMP_DIR/${label}.gguf"
    : > "$ODS_LOG_FILE"
    rm -f "$count_file" "$target" "$target.part"

    export ODS_TEST_CURL_COUNT_FILE="$count_file"
    if [[ "$retries" == "__unset__" ]]; then
        unset ODS_MODEL_DOWNLOAD_RETRIES
    else
        export ODS_MODEL_DOWNLOAD_RETRIES="$retries"
    fi

    download_with_progress "https://example.invalid/model.gguf" "$target" "Downloading model" && {
            echo "expected download_with_progress to fail for $label" >&2
            return 1
        }
    unset ODS_TEST_CURL_COUNT_FILE ODS_MODEL_DOWNLOAD_RETRIES

    local actual
    actual="$(cat "$count_file")"
    if [[ "$actual" != "$expected" ]]; then
        echo "$label: expected $expected curl attempts, got $actual" >&2
        return 1
    fi
}

run_failure_case default 8
run_failure_case env_override 2 2
run_failure_case invalid_env 8 bogus

echo "macOS download retry contract passed"
