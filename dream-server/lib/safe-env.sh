#!/usr/bin/env bash
# ============================================================================
# Dream Server — Safe environment loading (no eval)
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
        # Strip optional surrounding quotes and one leading space
        value="${value# }"
        value="${value%\"}"
        value="${value#\"}"
        value="${value%\'}"
        value="${value#\'}"
        export "$key=$value"
    done < "$path"
}

load_env_from_output() {
    local line key value
    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=\"(.*)\"$ ]]; then
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]}"
            # Unescape: \\ -> \, \" -> "
            value="${value//\\\\/\\}"
            value="${value//\\\"/\"}"
            export "$key=$value"
        fi
    done
}

# load_selector_env: parse the KEY=VALUE output of scripts/select-model.py
# (the model selector) from stdin WITHOUT eval (#1271).
#
# The selector output reaches a privileged installer. Its values derive from a
# device/hardware string and a JSON catalog, so it must never be `eval`d:
# a crafted catalog or spoofed device string could otherwise inject
# `KEY=1; touch /tmp/pwned` and run arbitrary code during install.
#
# Security model:
#   - The KEY regex ^[A-Za-z_][A-Za-z0-9_]*$ is THE security gate. A line whose
#     key is not a bare shell identifier is rejected outright, which neutralises
#     `x=1; touch /tmp/pwned` (key would be "x" but the value carries metachars,
#     rejected below) and `EVIL$(...)=v` (key fails the regex).
#   - select-model.py emits 15 well-known keys plus, optionally, catalog
#     runtime_profile.env keys. Both are accepted as long as the KEY matches the
#     identifier regex — we do NOT hard-drop dynamic keys (that would regress
#     future runtime_profile.env catalogs); the regex + value sanitisation are
#     the defence.
#   - Values are shlex-dequoted by hand (no shell): a single-quoted value has
#     its quotes stripped and the only shlex escape '\'' turned back into '.
#     A bare (unquoted) value is accepted only if it has no shell metacharacter;
#     anything containing $ ` ; & | < > ( ) { } [ ] * ? ! ~ \\, whitespace or a
#     newline is rejected. Quoted-with-metachars values are kept literally
#     (they are data, never executed).
#   - Assignment uses `printf -v` + `export` — never eval/source/declare on the
#     untrusted string.
#
# Returns 0 if at least one line was parsed without a hard error, 1 if the
# input was structurally unusable. Individual bad lines are skipped, not fatal,
# so a partially-corrupt selector output still applies its valid keys.
load_selector_env() {
    local line key raw value
    local parsed=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip blank and comment lines.
        [[ -z "${line//[[:space:]]/}" ]] && continue
        [[ "$line" =~ ^[[:space:]]*# ]] && continue

        # Must be KEY=VALUE; split on the FIRST '=' only.
        [[ "$line" == *=* ]] || continue
        key="${line%%=*}"
        raw="${line#*=}"

        # THE security gate: key must be a bare shell identifier.
        if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            continue
        fi

        if [[ "$raw" == \'*\' && ${#raw} -ge 2 ]]; then
            # Single-quoted (shlex.quote form). Strip the wrapping quotes and
            # decode the only escape shlex emits: '\'' -> '
            value="${raw:1:${#raw}-2}"
            value="${value//\'\\\'\'/\'}"
        elif [[ "$raw" =~ ^[A-Za-z0-9_./:+,@%-]*$ ]]; then
            # Bare token: only the shlex "safe" set, no metacharacters.
            value="$raw"
        else
            # Double-quoted, or anything with shell metacharacters outside a
            # single-quoted wrapper. Reject: never let it near a shell.
            if [[ "$raw" == \"*\" && ${#raw} -ge 2 ]]; then
                # Double-quoted literal: keep the inner text verbatim. It is
                # assigned via printf -v, so $()/`` inside are never executed.
                value="${raw:1:${#raw}-2}"
                value="${value//\\\\/\\}"
                value="${value//\\\"/\"}"
            else
                continue
            fi
        fi

        # Optional value-shape hardening for the numeric / structured keys.
        case "$key" in
            MAX_CONTEXT|LLM_MODEL_SIZE_MB)
                [[ "$value" =~ ^[0-9]+$ ]] || continue ;;
            GGUF_SHA256)
                [[ -z "$value" || "$value" =~ ^[a-f0-9]{64}$ ]] || continue ;;
            GGUF_URL)
                [[ -z "$value" || "$value" =~ ^https?:// ]] || continue ;;
        esac

        printf -v "$key" '%s' "$value"
        export "${key?}"
        parsed=1
    done

    [[ "$parsed" -eq 1 ]] && return 0 || return 1
}
