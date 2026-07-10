#!/bin/bash
# ============================================================================
# ODS — External Model Sync
# ============================================================================
# Purpose: Detect GGUF models already present in LM Studio or Ollama that
#          match ODS's model library, and copy (hardlink when on the same
#          filesystem, copy otherwise) them into ODS's model directory so
#          the installer skips redundant downloads.
#
#          Files are NEVER symlinked: docker-compose.base.yml mounts only
#          ./data/models:/models, so a symlink pointing outside that tree
#          would be broken inside the container.
#
# Expects: INSTALL_DIR (defaults to parent of script dir)
#          GGUF_FILE   (target GGUF filename; may also be passed as $1)
#
# Outputs (stdout, one line):
#   synced:lmstudio:<source_path>   — symlinked from LM Studio
#   synced:ollama:<source_path>     — symlinked from Ollama blob store
#   already_present                 — GGUF already in ODS models dir
#   not_found                       — no external source found
#
# Exit codes: 0 always (callers check stdout for result)
#
# Modder notes:
#   Add new external providers by adding a _sync_from_<provider> function
#   and calling it in sync_model() after the existing ones.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
ODS_MODELS_DIR="$INSTALL_DIR/data/models"

# ── Logging helpers ───────────────────────────────────────────────────────

_info()  { echo "[sync-external-models] $*" >&2; }
_warn()  { echo "[sync-external-models] WARN: $*" >&2; }

# ── External provider path detection ─────────────────────────────────────

_lmstudio_model_dirs() {
    # Returns candidate LM Studio model directories, one per line.
    # Checks standard locations for Linux, macOS, and Windows (Git Bash/WSL2).
    local -a dirs=()

    # Linux / WSL2 home-based
    [[ -d "$HOME/.lmstudio/models" ]] && dirs+=("$HOME/.lmstudio/models")

    # macOS Application Support
    local mac_dir="$HOME/Library/Application Support/LM-Studio/models"
    [[ -d "$mac_dir" ]] && dirs+=("$mac_dir")

    # Windows: %LOCALAPPDATA%\LM-Studio\models (Git Bash exposes LOCALAPPDATA)
    if [[ -n "${LOCALAPPDATA:-}" ]]; then
        local win_dir
        # cygpath converts Windows paths to POSIX for Git Bash
        win_dir="$(cygpath -u "$LOCALAPPDATA" 2>/dev/null || echo "")"
        [[ -n "$win_dir" && -d "$win_dir/LM-Studio/models" ]] && dirs+=("$win_dir/LM-Studio/models")
        # LM Studio also ships with an AppData/Roaming variant on some versions
        local roaming
        roaming="$(cygpath -u "${APPDATA:-}" 2>/dev/null || echo "")"
        [[ -n "$roaming" && -d "$roaming/LM-Studio/models" ]] && dirs+=("$roaming/LM-Studio/models")
    fi

    printf '%s\n' "${dirs[@]}"
}

_ollama_models_dir() {
    # Returns the Ollama models directory, or empty if not found.
    # Respects the OLLAMA_MODELS env var that Ollama itself honours.
    if [[ -n "${OLLAMA_MODELS:-}" && -d "$OLLAMA_MODELS" ]]; then
        echo "$OLLAMA_MODELS"
        return
    fi
    [[ -d "$HOME/.ollama/models" ]] && echo "$HOME/.ollama/models"
}

# ── GGUF filename helpers ─────────────────────────────────────────────────

# Strip the quantization suffix from a GGUF stem.
# "Qwen3.5-2B-Q4_K_M" → "Qwen3.5-2B"
_gguf_base() {
    local stem="$1"
    # Quantization tokens look like Q4_K_M, Q8_0, F16, BF16, IQ3_M, etc.
    echo "$stem" | sed 's/-[A-Za-z][0-9]\{1\}[A-Za-z0-9_]*\(\.gguf\)\{0,1\}$//'
}

# ── LM Studio sync ────────────────────────────────────────────────────────

_sync_from_lmstudio() {
    local target_gguf="$1"
    local dest="$ODS_MODELS_DIR/$target_gguf"

    [[ -f "$dest" ]] && { echo "already_present"; return 0; }

    local -a lmstudio_dirs=()
    while IFS= read -r _lmd; do
        [[ -n "$_lmd" ]] && lmstudio_dirs+=("$_lmd")
    done < <(_lmstudio_model_dirs 2>/dev/null || true)
    [[ ${#lmstudio_dirs[@]} -eq 0 ]] && { echo "not_found"; return 0; }

    local stem="${target_gguf%.gguf}"
    local base
    base="$(_gguf_base "$stem")"

    for models_dir in "${lmstudio_dirs[@]}"; do
        [[ -d "$models_dir" ]] || continue

        # Pass 1 — exact filename match (highest confidence)
        local found=""
        found="$(find "$models_dir" -maxdepth 5 -name "$target_gguf" -type f 2>/dev/null | head -1)"
        if [[ -n "$found" ]]; then
            mkdir -p "$ODS_MODELS_DIR"
            # Hardlink preferred — same inode, no symlink indirection, works inside Docker.
            # Falls back to copy when source and destination are on different filesystems.
            if ln "$found" "$dest" 2>/dev/null; then
                _info "Hardlinked from LM Studio (exact): $found"
            else
                cp "$found" "$dest"
                _info "Copied from LM Studio (exact): $found"
            fi
            echo "synced:lmstudio:$found"
            return 0
        fi

        # Pass 2 — base name match (same model, any quantization)
        # Accept any quantization the user already has to avoid a re-download.
        found="$(find "$models_dir" -maxdepth 5 -iname "${base}*.gguf" -type f 2>/dev/null | head -1)"
        if [[ -n "$found" ]]; then
            mkdir -p "$ODS_MODELS_DIR"
            # Hardlink under the ODS expected filename so the installer finds it.
            if ln "$found" "$dest" 2>/dev/null; then
                _info "Hardlinked from LM Studio (family match): $found → $target_gguf"
            else
                cp "$found" "$dest"
                _info "Copied from LM Studio (family match): $found → $target_gguf"
            fi
            echo "synced:lmstudio:$found"
            return 0
        fi
    done

    echo "not_found"
}

# ── Ollama sync ───────────────────────────────────────────────────────────

# Derive a likely Ollama model tag from an ODS GGUF filename.
# "Qwen3.5-2B-Q4_K_M.gguf"         → "qwen3.5:2b"
# "Phi-4-mini-instruct-Q4_K_M.gguf" → "phi4-mini"
# Returns empty string if the mapping cannot be determined.
_gguf_to_ollama_tag() {
    local gguf="$1"
    local stem="${gguf%.gguf}"
    local base
    base="$(_gguf_base "$stem")"

    # Strip common suffixes that Ollama omits
    base="${base%-Instruct}"
    base="${base%-instruct}"
    base="${base%-Chat}"
    base="${base%-chat}"

    # Convert to lowercase (tr used for Bash 3.2 portability; ${var,,} is Bash 4+)
    local lower
    lower="$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]')"

    # Last dash-separated token is the size (e.g., 2b, 9b, 30b-a3b, mini)
    local size="${lower##*-}"
    local family="${lower%-"$size"}"

    # Build Ollama tag: family:size (skip if we couldn't split properly)
    if [[ -n "$family" && -n "$size" && "$family" != "$lower" ]]; then
        echo "${family}:${size}"
    else
        echo ""
    fi
}

# Parse an Ollama manifest and return the blob digest for the model layer.
# Returns empty string on any error.
_ollama_model_blob_digest() {
    local manifest_path="$1"
    [[ -f "$manifest_path" ]] || return 0

    # Use python3 if available (most reliable JSON parsing)
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$manifest_path" <<'PY' 2>/dev/null || true
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    for layer in data.get("layers", []):
        mt = layer.get("mediaType", "")
        if "model" in mt and "image.model" in mt:
            print(layer["digest"].replace("sha256:", "sha256-"))
            break
except Exception:
    pass
PY
    fi
}

_sync_from_ollama() {
    local target_gguf="$1"
    local dest="$ODS_MODELS_DIR/$target_gguf"

    [[ -f "$dest" ]] && { echo "already_present"; return 0; }

    local ollama_host="${OLLAMA_HOST:-http://localhost:11434}"

    # Quick liveness check — don't wait long if Ollama isn't running
    if ! curl -sf --max-time 3 "$ollama_host/api/tags" >/dev/null 2>&1; then
        echo "not_found"
        return 0
    fi

    local tags_json
    tags_json="$(curl -sf --max-time 8 "$ollama_host/api/tags" 2>/dev/null || true)"
    [[ -z "$tags_json" ]] && { echo "not_found"; return 0; }

    # Derive the expected Ollama tag for this GGUF
    local expected_tag
    expected_tag="$(_gguf_to_ollama_tag "$target_gguf")"

    # Extract model name family (the part before the colon) for broad search
    local family="${expected_tag%%:*}"
    [[ -z "$family" ]] && { echo "not_found"; return 0; }

    # Check that Ollama actually has a model from this family
    if ! echo "$tags_json" | grep -qi "\"name\"[[:space:]]*:[[:space:]]*\"${family}"; then
        echo "not_found"
        return 0
    fi

    # Find the best matching model name in the Ollama list
    local matched_model=""
    matched_model="$(echo "$tags_json" | grep -oi '"name"[[:space:]]*:[[:space:]]*"[^"]*'"${family}"'[^"]*"' \
        | sed 's/.*"\([^"]*\)"$/\1/' | head -1 || true)"
    [[ -z "$matched_model" ]] && { echo "not_found"; return 0; }

    _info "Ollama has model: $matched_model (looking for $expected_tag)"

    # Try to locate and link the underlying GGUF blob
    local ollama_dir
    ollama_dir="$(_ollama_models_dir || true)"
    if [[ -z "$ollama_dir" || ! -d "$ollama_dir" ]]; then
        # Ollama is running and has the model, but blob store not accessible
        _info "Ollama model found but blob directory not accessible — skipping blob link"
        echo "not_found"
        return 0
    fi

    local blobs_dir="$ollama_dir/blobs"
    local manifests_base="$ollama_dir/manifests"
    [[ -d "$blobs_dir" ]] || { echo "not_found"; return 0; }

    # Locate the manifest for matched_model
    # Ollama stores manifests at: manifests/<registry>/<namespace>/<name>/<tag>
    # For library models: manifests/registry.ollama.ai/library/<name>/<tag>
    local model_name="${matched_model%%:*}"
    local model_tag="${matched_model#*:}"
    [[ "$model_tag" == "$matched_model" ]] && model_tag="latest"

    local manifest_file=""
    # Try the standard Ollama registry path first
    local candidate="$manifests_base/registry.ollama.ai/library/$model_name/$model_tag"
    [[ -f "$candidate" ]] && manifest_file="$candidate"

    # Fall back to a recursive search if not at the expected path
    if [[ -z "$manifest_file" && -d "$manifests_base" ]]; then
        manifest_file="$(find "$manifests_base" -type f -path "*/$model_name/$model_tag" 2>/dev/null | head -1 || true)"
    fi

    if [[ -z "$manifest_file" ]]; then
        _info "Ollama manifest not found for $matched_model"
        echo "not_found"
        return 0
    fi

    local blob_digest
    blob_digest="$(_ollama_model_blob_digest "$manifest_file")"
    if [[ -z "$blob_digest" ]]; then
        _info "Could not extract blob digest from Ollama manifest: $manifest_file"
        echo "not_found"
        return 0
    fi

    local blob_path="$blobs_dir/$blob_digest"
    if [[ ! -f "$blob_path" ]]; then
        _info "Ollama blob not found at: $blob_path"
        echo "not_found"
        return 0
    fi

    mkdir -p "$ODS_MODELS_DIR"
    # Hardlink preferred — avoids symlink breakage inside Docker's /models mount.
    if ln "$blob_path" "$dest" 2>/dev/null; then
        _info "Hardlinked from Ollama blob ($matched_model): $blob_path → $target_gguf"
    else
        cp "$blob_path" "$dest"
        _info "Copied from Ollama blob ($matched_model): $blob_path → $target_gguf"
    fi

    echo "synced:ollama:$blob_path"
}

# ── Public entry point ────────────────────────────────────────────────────

# sync_model <gguf_file>
# Attempts to sync a single GGUF from LM Studio, then Ollama.
# Prints one result line to stdout (see file header for codes).
sync_model() {
    local gguf_file="${1:-${GGUF_FILE:-}}"
    if [[ -z "$gguf_file" ]]; then
        _warn "No GGUF filename specified"
        echo "not_found"
        return 0
    fi

    # Already present — nothing to do
    if [[ -f "$ODS_MODELS_DIR/$gguf_file" ]]; then
        echo "already_present"
        return 0
    fi

    local result

    result="$(_sync_from_lmstudio "$gguf_file")"
    case "$result" in
        synced:*|already_present) echo "$result"; return 0 ;;
    esac

    result="$(_sync_from_ollama "$gguf_file")"
    case "$result" in
        synced:*|already_present) echo "$result"; return 0 ;;
    esac

    echo "not_found"
}

# ── CLI usage ─────────────────────────────────────────────────────────────
# When sourced: sync_model() is available to the caller.
# When executed directly: sync_model "$1" (or $GGUF_FILE).

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    target="${1:-${GGUF_FILE:-}}"
    if [[ -z "$target" ]]; then
        echo "Usage: $0 <gguf_filename>  (or set GGUF_FILE env var)" >&2
        echo "Example: $0 Qwen3.5-2B-Q4_K_M.gguf" >&2
        exit 1
    fi
    result="$(sync_model "$target")"
    echo "$result"
    case "$result" in
        synced:lmstudio:*) exit 0 ;;
        synced:ollama:*)   exit 0 ;;
        already_present)   exit 0 ;;
        not_found)         exit 1 ;;
        *)                 exit 1 ;;
    esac
fi
