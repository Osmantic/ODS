#!/usr/bin/env bash
# No installer-wide yes flag authorizes model license/source review.
ods_review_model_download() {
    local root="$1" filename="$2" url="$3" sha256="$4" receipt="${5:-}"
    local python_cmd="${ODS_PYTHON_CMD:-}" helper="$root/scripts/review-model-download.py"
    [[ -f "$helper" ]] || { printf 'Model download blocked: review helper is missing.\n' >&2; return 1; }
    if ! declare -F ods_detect_python_cmd >/dev/null 2>&1 && [[ -f "$root/lib/python-cmd.sh" ]]; then
        source "$root/lib/python-cmd.sh"
    fi
    if [[ -z "$python_cmd" ]] && declare -F ods_detect_python_cmd >/dev/null 2>&1; then
        python_cmd="$(ods_detect_python_cmd)" || return 1
    fi
    [[ -n "$python_cmd" ]] || python_cmd="$(command -v python3 || command -v python)" || return 1
    local args=("$helper" --catalog "$root/config/model-library.json" --file "$filename" --url "$url" --sha256 "$sha256")
    [[ -z "${ODS_MODEL_TERMS_ACK_FILE:-}" ]] || args+=(--ack-file "$ODS_MODEL_TERMS_ACK_FILE")
    [[ -z "$receipt" ]] || args+=(--write-ack-file "$receipt")
    [[ "${NON_INTERACTIVE:-false}" != "true" ]] || args+=(--non-interactive)
    "$python_cmd" "${args[@]}"
}

ods_check_model_download_receipt() {
    local root="$1" filename="$2" url="$3" sha256="$4"
    # A detached retry can only reuse an explicit receipt for this artifact;
    # it cannot prompt or fall back to environment/global installer approval.
    ODS_MODEL_TERMS_ACK_FILE="$root/data/model-download-review.json" NON_INTERACTIVE=true \
        ods_review_model_download "$root" "$filename" "$url" "$sha256"
}
