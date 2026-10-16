#!/bin/bash
# ============================================================================
# ODS Installer — Extension .env carry-over
# ============================================================================
# Purpose: Keep the settings installed extensions own across a .env rewrite
#
# Expects: nothing (pure file operation; no globals)
# Provides: ods_carry_extension_env_keys()
#
# Modder notes:
#   Phase 06 rewrites .env from its template on every installer run. The
#   template does not know extension settings: a Library extension's setup hook
#   appends its own secrets (LibreChat's database password and credential
#   encryption key, Flowise's login, ...) and declares them in its manifest's
#   env_vars. Dropping them makes Compose refuse the whole stack on the
#   extension's required variables, and the original values cannot be
#   regenerated without losing the extension's data. Bundled extensions
#   declare owner settings the same way (Brave Search's API key, Tailscale's
#   hostname and tags, n8n's reverse-proxy settings), which the template never
#   writes either.
# ============================================================================

# Append to NEW_ENV every key an extension declares in its manifest env_vars
# that NEW_ENV lacks and PREVIOUS_ENV has, copying the exact line.
# Usage: ods_carry_extension_env_keys PREVIOUS_ENV NEW_ENV EXTENSIONS_DIR...
ods_carry_extension_env_keys() {
    local previous_env="$1" new_env="$2"
    shift 2
    local extensions_dir manifest key line header_written=false
    [[ -f "$previous_env" && -f "$new_env" ]] || return 0
    for extensions_dir in "$@"; do
        for manifest in "$extensions_dir"/*/manifest.yaml; do
            [[ -f "$manifest" ]] || continue
            while IFS= read -r key; do
                grep -q "^${key}=" "$new_env" && continue
                line="$(grep -m1 "^${key}=" "$previous_env")" || continue
                if [[ "$header_written" != true ]]; then
                    printf '\n#=== Installed extensions (kept from the previous .env) ===\n' >> "$new_env"
                    header_written=true
                fi
                printf '%s\n' "$line" >> "$new_env"
            done < <(awk '
                /^[[:space:]]*($|#)/ { next }
                {
                    match($0, /^[[:space:]]*/)
                    indent = RLENGTH
                    if (in_env && indent <= env_indent) in_env = 0
                }
                /^[[:space:]]*env_vars:[[:space:]]*($|#)/ {
                    in_env = 1
                    env_indent = indent
                    next
                }
                /^[[:space:]]*-[[:space:]]*key:[[:space:]]*/ {
                    if (!in_env) next
                    k = $0
                    sub(/^[[:space:]]*-[[:space:]]*key:[[:space:]]*/, "", k)
                    gsub(/["\047[:space:]]/, "", k)
                    if (k ~ /^[A-Z][A-Z0-9_]*$/) print k
                }' "$manifest")
        done
    done
}

# Append to NEW_ENV every public URL the owner set (KEY_PUBLIC_URL or
# KEY_PUBLIC_URLS, see docs/ODS-PROXY.md) that NEW_ENV lacks and PREVIOUS_ENV
# has. The template never writes these; dropping them on a rerun sends magic
# links and service links back to LAN addresses. The last assignment wins, as
# it does when .env is read.
# Usage: ods_carry_public_url_env_keys PREVIOUS_ENV NEW_ENV
ods_carry_public_url_env_keys() {
    local previous_env="$1" new_env="$2" line header_written=false
    [[ -f "$previous_env" && -f "$new_env" ]] || return 0
    while IFS= read -r line; do
        grep -q "^${line%%=*}=" "$new_env" && continue
        if [[ "$header_written" != true ]]; then
            printf '\n#=== Public URLs (kept from the previous .env) ===\n' >> "$new_env"
            header_written=true
        fi
        printf '%s\n' "$line" >> "$new_env"
    done < <(awk '
        /^[A-Z][A-Z0-9_]*_PUBLIC_URLS?=/ {
            key = substr($0, 1, index($0, "=") - 1)
            if (!(key in last)) order[++n] = key
            last[key] = $0
        }
        END { for (i = 1; i <= n; i++) print last[order[i]] }' "$previous_env")
}

# Settings ODS tells the owner to put in .env that no installer writes: the
# dashboard's n8n API key (routers/workflows.py), the Hugging Face token for
# gated downloads (routers/models.py), the Intel Arc image override
# (docker-compose.arc.yml), Open WebUI's speech settings (tts/README.md) and
# whether apps use measured model profiles (ODS_MODEL_PROFILES,
# docs/MODEL-MANAGEMENT.md).
# Installer-managed keys must not be listed: an old value would override the
# installer's new choice.
# shellcheck disable=SC2034  # read by installers/phases/06-directories.sh
ODS_OWNER_ENV_KEYS=(
    N8N_API_KEY
    HF_TOKEN
    LLAMA_ARC_IMAGE
    AUDIO_TTS_ENGINE
    AUDIO_TTS_MODEL
    AUDIO_TTS_VOICE
    AUDIO_TTS_OPENAI_API_BASE_URL
    AUDIO_TTS_OPENAI_API_KEY
    ODS_MODEL_PROFILES
)

# Append to NEW_ENV each named KEY that NEW_ENV lacks and PREVIOUS_ENV has,
# keeping its last assignment, as it is when .env is read.
# Usage: ods_carry_named_env_keys PREVIOUS_ENV NEW_ENV KEY...
ods_carry_named_env_keys() {
    local previous_env="$1" new_env="$2" key line header_written=false
    shift 2
    [[ -f "$previous_env" && -f "$new_env" ]] || return 0
    for key in "$@"; do
        grep -q "^${key}=" "$new_env" && continue
        line="$(awk -v key="$key" 'index($0, key "=") == 1 { last = $0 } END { if (last != "") print last }' "$previous_env")"
        [[ -n "$line" ]] || continue
        if [[ "$header_written" != true ]]; then
            printf '\n#=== Owner settings (kept from the previous .env) ===\n' >> "$new_env"
            header_written=true
        fi
        printf '%s\n' "$line" >> "$new_env"
    done
}
