#!/usr/bin/env bash
# ============================================================================
# ODS — Safe environment loading (no eval)
# ============================================================================
# Scripts that need to load .env should use load_env_file from this script.
# Do not use eval or "export $(grep ... .env | xargs)" — they allow injection.
#
# - load_env_file <path>  — parse a .env file and export vars (safe keys, no eval)
# - load_env_from_output  — parse KEY="value" lines from stdin (for script output)
# ============================================================================

# Load a .env file safely: comments and empty lines skipped; key names must be
# valid identifiers; values may be unquoted or quoted; no eval or word-splitting.
load_env_file() {
    local path="$1"
    [[ -f "$path" ]] || return 0
    local line key value
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Strip a trailing CR so CRLF .env files (Windows editors, the Windows
        # installer) don't leave carriage returns on every value — which would
        # otherwise corrupt ports/paths (e.g. 8080\r) and leave the closing
        # quote unstripped on quoted values. Matches load_env_from_output.
        line="${line%$'\r'}"
        # Skip comments and blank lines
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" =~ ^[[:space:]]*$ ]] && continue
        # Lines without '=' are not valid KEY=VALUE pairs
        [[ "$line" == *=* ]] || continue
        # Split on first '=' only, preserve '=' in values (e.g. base64 padding)
        key="${line%%=*}"
        value="${line#*=}"
        # Trim whitespace from key
        key="${key#"${key%%[![:space:]]*}"}"
        key="${key%"${key##*[![:space:]]}"}"
        [[ -z "$key" ]] && continue
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        # Bash exposes UID as a readonly shell variable. A .env line such as
        # UID=1000 is valid for Docker Compose, but exporting it here aborts
        # lifecycle commands under set -e before they can reach compose.
        [[ "$key" == "UID" ]] && continue
        # Strip one leading space, then a single matching pair of surrounding
        # quotes. Only strip when both ends carry the SAME quote character —
        # stripping each quote type independently corrupts values whose content
        # legitimately begins or ends with the other quote (e.g. a double-quoted
        # "'literal'" would otherwise lose its inner single quotes, and KEY="'"
        # would collapse to empty).
        value="${value# }"
        if [[ "$value" == '"'*'"' ]]; then
            value="${value#\"}"
            value="${value%\"}"
        elif [[ "$value" == "'"*"'" ]]; then
            value="${value#\'}"
            value="${value%\'}"
        fi
        export "$key=$value"
    done < "$path"
}

_safe_env_unescape_double_quoted() {
    local value="$1"
    # Decode the small shell-compatible escape set emitted by repository
    # helper scripts. This is parsing, not eval: no command substitution,
    # expansion, globbing, or word splitting is performed.
    value="${value//\\\"/\"}"
    value="${value//\\\$/\$}"
    value="${value//\\\`/\`}"
    value="${value//\\\\/\\}"
    printf '%s' "$value"
}

_safe_env_key_allowed() {
    local key="$1"
    shift || true
    local allowed
    for allowed in "$@"; do
        [[ "$key" == "$allowed" ]] && return 0
    done
    return 1
}

load_env_from_output() {
    local line key value
    while IFS= read -r line; do
        line="${line%$'\r'}"
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=\"(.*)\"$ ]]; then
            key="${BASH_REMATCH[1]}"
            value="$(_safe_env_unescape_double_quoted "${BASH_REMATCH[2]}")"
            export "$key=$value"
        fi
    done
}

load_env_from_output_allowlist() {
    local line key value
    [[ "$#" -gt 0 ]] || return 0
    while IFS= read -r line; do
        line="${line%$'\r'}"
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=\"(.*)\"$ ]]; then
            key="${BASH_REMATCH[1]}"
            _safe_env_key_allowed "$key" "$@" || continue
            value="$(_safe_env_unescape_double_quoted "${BASH_REMATCH[2]}")"
            export "$key=$value"
        fi
    done
}

load_model_selector_env_from_output() {
    load_env_from_output_allowlist \
        LLM_MODEL \
        GGUF_FILE \
        GGUF_URL \
        GGUF_SHA256 \
        MAX_CONTEXT \
        LLM_MODEL_SIZE_MB \
        MODEL_RECOMMENDATION_SOURCE \
        MODEL_RECOMMENDATION_POLICY \
        MODEL_RECOMMENDATION_CONFIDENCE \
        MODEL_RECOMMENDATION_REASON \
        MODEL_RECOMMENDED_ALTERNATIVES \
        MODEL_RUNTIME_PROFILE \
        MODEL_RUNTIME_PROFILE_LABEL \
        MODEL_RUNTIME_PROFILE_SOURCE \
        LLAMA_SERVER_IMAGE \
        LLAMA_CPP_RELEASE_TAG_OVERRIDE \
        LLAMA_CPP_SERVER_BINARY \
        LLAMA_ARG_FLASH_ATTN \
        LLAMA_ARG_CACHE_TYPE_K \
        LLAMA_ARG_CACHE_TYPE_V \
        LLAMA_ARG_N_CPU_MOE \
        LLAMA_ARG_NO_CACHE_PROMPT \
        LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS \
        LLAMA_ARG_SPEC_TYPE \
        LLAMA_ARG_SPEC_DRAFT_N_MAX \
        LLAMA_ARG_SPLIT_MODE \
        LLAMA_ARG_TENSOR_SPLIT
}
