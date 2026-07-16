#!/bin/bash
# ============================================================================
# ODS Installer — External Service Detection
# ============================================================================
# Part of: installers/lib/
# Purpose: Helpers to detect running local Ollama/LM Studio services and match
#          available models against the installer's required target model.
# ============================================================================

# Normalize model name for comparison (e.g. qwen3:8b -> qwen3-8b, Qwen3-8B-Q4_K_M.gguf -> qwen3-8b)
normalize_model_name() {
    echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/\.gguf$//; s/[-_]q[0-9].*//; s/:/-/; s/ //g'
}

# Check if model names belong to the same family/specification
model_family_matches() {
    local m1="$1"
    local m2="$2"
    [[ "$(normalize_model_name "$m1")" == "$(normalize_model_name "$m2")" ]]
}

# Search a newline-separated list of models for a match with target_model
find_matching_external_model() {
    local target_model="$1"
    local model_list="$2"
    local line
    while IFS= read -r line; do
        if [[ -n "$line" ]] && model_family_matches "$target_model" "$line"; then
            echo "$line"
            return 0
        fi
    done <<< "$model_list"
    return 1
}

# Detect running Ollama instance and return its local model tags
detect_ollama() {
    local tags
    tags=$(curl -sf --max-time 2 http://127.0.0.1:11434/api/tags 2>/dev/null) || return 1
    echo "$tags" | grep -o '"name":[[:space:]]*"[^"]*"' | cut -d'"' -f4
}

# Detect running LM Studio instance and return its loaded model IDs
detect_lmstudio() {
    local models
    models=$(curl -sf --max-time 2 http://127.0.0.1:1234/v1/models 2>/dev/null) || return 1
    echo "$models" | grep -o '"id":[[:space:]]*"[^"]*"' | cut -d'"' -f4
}
