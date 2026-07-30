#!/bin/bash
# ============================================================================
# ODS backup path contract
# ============================================================================
# Single source of truth for the user-data directories `ods backup` captures
# and `ods restore` puts back. Sourced by ods-backup.sh and ods-restore.sh so
# the two sides cannot drift apart.
#
# A directory belongs here when a bundled service bind-mounts it under
# ./data/ and its contents are user state that cannot be regenerated:
# conversations, workflows, vectors, agent memory, usage history, device
# authorisations.
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
    "data/openclaw"         # agent workspace
    "data/hermes"           # agent state and configuration
    "data/persona"          # Hermes persona (SOUL.md)
    "data/hermes-proxy"     # proxy session state
    "data/ape"              # governance decisions and approvals
    "data/token-spy"        # usage and cost history
    "data/privacy-shield"   # scrubber state and keys
    "data/comfyui"          # user workflows and generated output
    "data/tailscale"        # device authorisation (re-auth needed if lost)
    "data/ods-proxy"        # proxy state
    # Retained for installs that predate the current service set. Nothing in
    # the tree mounts these today, and skipping a missing directory is free.
    "data/litellm"
    "data/livekit"
    "data/ollama"
)

# Excluded on purpose. Each entry is a cache that is re-downloaded or
# re-derived on demand; keep this list explicit so the coverage test can tell
# "deliberately skipped" from "forgotten".
# shellcheck disable=SC2034  # consumed by tests/test-backup-data-coverage.sh
ODS_BACKUP_EXCLUDED_DATA_PATHS=(
    "data/models"        # GGUF weights — re-downloadable, tens of GB
    "data/whisper"       # STT model cache (the cache backup type covers it)
    "data/embeddings"    # TEI model cache
)
