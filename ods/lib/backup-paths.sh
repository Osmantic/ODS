#!/bin/bash
# ============================================================================
# ODS backup path contract
# ============================================================================
# Single source of truth for the user-data directories `ods backup` captures
# and `ods restore` puts back. Sourced by ods-backup.sh and ods-restore.sh so
# the two sides cannot drift apart.
#
# An entry belongs here when a bundled service persists it under ./data/ and
# its contents are user state that cannot be regenerated: conversations,
# workflows, vectors, agent memory, usage history, device authorisations.
# That includes what dashboard-api writes directly into its ./data:/data
# mount. Entries may be directories or single files.
#
# Regenerable caches are deliberately excluded (see
# ODS_BACKUP_EXCLUDED_DATA_PATHS below) — they are large, re-downloadable,
# and would dominate every archive.
#
# tests/test-backup-data-coverage.sh fails the build when a bundled service
# mounts a ./data/ directory that appears in neither list.
# ============================================================================

# shellcheck disable=SC2034  # consumed by the scripts that source this file
ODS_USER_DATA_PATHS=(
    "data/open-webui"       # chats, prompts, RAG documents
    "data/n8n"              # workflows and credentials
    "data/qdrant"           # vector collections
    "data/hermes"           # agent state and configuration
    "data/persona"          # Hermes persona (SOUL.md)
    "data/hermes-proxy"     # proxy session state
    "data/ape"              # governance decisions and approvals
    "data/token-spy"        # usage and cost history
    "data/privacy-shield"   # scrubber state and keys
    "data/comfyui"          # user workflows and generated output
    "data/tailscale"        # device authorisation (re-auth needed if lost)
    "data/ods-proxy"        # proxy state
    # Written directly by dashboard-api through its ./data:/data mount.
    "data/user-extensions"           # Library-installed extensions
    "data/config"                    # setup wizard / persona configuration
    "data/pixel-images"              # generated image library
    "data/pixel-agent-teams"         # Pixel agent team history (0700)
    "data/dashboard-password.json"   # dashboard login (0600); unconfigured without it
    "data/model-imports.json"        # user-imported model library entries
    "data/model-stores.json"         # registered external model stores
    # Retained for installs that predate the current service set. Nothing in
    # the tree mounts these today, and skipping a missing directory is free.
    "data/litellm"
    "data/livekit"
    "data/ollama"
    "data/openclaw"         # removed legacy OpenClaw extension's agent data
)

# Excluded on purpose. Each entry is a cache that is re-downloaded or
# re-derived on demand; keep this list explicit so the coverage test can tell
# "deliberately skipped" from "forgotten".
# shellcheck disable=SC2034  # consumed by tests/test-backup-data-coverage.sh
ODS_BACKUP_EXCLUDED_DATA_PATHS=(
    "data/hermes-auth"   # startup policy receipt; regenerate from restored seed/config
    "data/models"        # GGUF weights — re-downloadable, tens of GB
    "data/whisper"       # STT model cache
    "data/embeddings"    # TEI model cache
    "data/llama-server"  # CUDA kernel cache, root-owned; rebuilt on the next start
    "data/vllm"          # vLLM compile/autotune cache, rebuilt on start
    # dashboard-api runtime files: progress, status and receipts that the
    # running stack rewrites, or copies of state captured elsewhere.
    "data/extensions-library"            # catalog copy; re-copied by the installer
    "data/extension-progress"            # in-flight install/remove progress
    "data/bootstrap-status.json"         # first-boot model download progress
    "data/bootstrap-upgrade.args"        # one-shot bootstrap upgrade request
    "data/model-download-status.json"    # in-flight download progress
    "data/model-activation-receipt.json" # last activation receipt
    "data/model-state.json"              # describes data/models, which is skipped too
    "data/token_counter.json"            # live token counter
    "data/model_performance.json"        # live throughput samples
    "data/auth"                          # magic-link tokens, expire within 24h
    "data/dashboard-api-key.txt"         # fallback key, regenerated when unset in .env
    "data/config-backups"                # .env snapshots; .env itself is backed up
    "data/remote-provider"               # routing state and secrets rendered by the installer
    "data/pixel-chat-results"            # SSE delivery receipts, pruned after 7 days
    "data/.dashboard-private-stores"     # migration custody; live images exported separately
)

# Every user-data path to capture from (or restore into) the install rooted
# at $1: the fixed list above plus the data/<id> directory of each
# Library-installed extension found under $1/data/user-extensions. Those ids
# are only known at runtime, so they are derived here rather than listed.
# Ids already decided above (captured or excluded) are not repeated.
ods_backup_data_paths() {
    local root="$1" ext id candidate
    printf '%s\n' "${ODS_USER_DATA_PATHS[@]}"
    for ext in "$root"/data/user-extensions/*/; do
        [[ -d "$ext" ]] || continue
        id="$(basename "$ext")"
        [[ "$id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || continue
        candidate="data/$id"
        [[ " ${ODS_USER_DATA_PATHS[*]} ${ODS_BACKUP_EXCLUDED_DATA_PATHS[*]} " == *" $candidate "* ]] && continue
        [[ -d "$root/$candidate" && ! -L "$root/$candidate" ]] || continue
        printf '%s\n' "$candidate"
    done
}

# The cache tier: captured only by `ods backup -t full` and put back by
# `ods restore`. These are the excluded directories above — large and
# re-downloadable, so the default user-data backup skips them — but a full
# backup exists precisely so an install can be moved or rebuilt without
# downloading tens of GB again. Paths match the compose bind mounts
# (./data/models:/models, ./data/whisper:<hub cache>, ./data/embeddings:/data).
# shellcheck disable=SC2034  # consumed by ods-backup.sh and ods-restore.sh
ODS_BACKUP_CACHE_PATHS=(
    "data/models"        # GGUF weights served by llama-server
    "data/whisper"       # faster-whisper Hugging Face hub cache
    "data/embeddings"    # TEI model cache
)
