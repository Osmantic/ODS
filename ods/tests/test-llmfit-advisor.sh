#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURES_DIR="$ROOT_DIR/tests/fixtures/llmfit"

# Source the advisor with SCRIPT_DIR set so model map path resolves
SCRIPT_DIR="$ROOT_DIR/scripts"
source "$ROOT_DIR/scripts/llmfit-advisor.sh"

_assert_eq() {
    local got="$1" expected="$2" label="$3"
    if [ "$got" != "$expected" ]; then
        echo "[FAIL] ${label}: expected '${expected}', got '${got}'"
        exit 1
    fi
    echo "[PASS] ${label}"
}

# Test each hardware fixture maps to expected ODS GGUF + tier

# NVIDIA 24GB → T3
_parse_llmfit_output "$(cat "$FIXTURES_DIR/nvidia-24gb.json")"
_assert_eq "$LLMFIT_ODS_GGUF" "Qwen3-30B-A3B-Q4_K_M.gguf" "nvidia-24gb maps to T3 GGUF"
_assert_eq "$LLMFIT_ODS_TIER" "T3"                      "nvidia-24gb maps to T3 tier"

# NVIDIA 8GB → T2
_parse_llmfit_output "$(cat "$FIXTURES_DIR/nvidia-8gb.json")"
_assert_eq "$LLMFIT_ODS_GGUF" "Qwen3.5-9B-Q4_K_M.gguf"  "nvidia-8gb maps to T2 GGUF"
_assert_eq "$LLMFIT_ODS_TIER" "T2"                      "nvidia-8gb maps to T2 tier"

# AMD Strix Halo → T3
_parse_llmfit_output "$(cat "$FIXTURES_DIR/amd-strix-halo.json")"
_assert_eq "$LLMFIT_ODS_TIER" "T3" "strix-halo maps to T3 tier"

# Apple M3 16GB → T2
_parse_llmfit_output "$(cat "$FIXTURES_DIR/apple-m3-16gb.json")"
_assert_eq "$LLMFIT_ODS_TIER" "T2" "apple-m3-16gb maps to T2 tier"

# CPU only → T1
_parse_llmfit_output "$(cat "$FIXTURES_DIR/cpu-only.json")"
_assert_eq "$LLMFIT_ODS_GGUF" "Qwen3.5-2B-Q4_K_M.gguf" "cpu-only maps to T1 GGUF"
_assert_eq "$LLMFIT_ODS_TIER" "T1"                       "cpu-only maps to T1 tier"

# Malformed JSON → returns 1, no crash
_parse_llmfit_output '{"broken":' && {
    echo "[FAIL] malformed JSON should return 1"
    exit 1
} || echo "[PASS] malformed JSON returns 1 cleanly"

# Empty recommendations → returns 1, no crash
_parse_llmfit_output '{"recommendations":[]}' && {
    echo "[FAIL] empty recommendations should return 1"
    exit 1
} || echo "[PASS] empty recommendations returns 1 cleanly"

# Unmapped model → returns 1, falls back
_parse_llmfit_output '{"recommendations":[{"model_id":"unknown/model","quantization":"Q4_K_M","estimated_tokens_per_sec":10}]}' && {
    echo "[FAIL] unmapped model should return 1"
    exit 1
} || echo "[PASS] unmapped model returns 1 cleanly"

# Regression: every ods_gguf_file in llmfit map must exist in model-library.json
_test_catalog_alignment() {
    local model_library="$ROOT_DIR/config/model-library.json"
    local model_map="$ROOT_DIR/config/llmfit-model-map.json"

    python3 - "$model_map" "$model_library" << 'EOF'
import sys, json

map_path, library_path = sys.argv[1], sys.argv[2]

with open(map_path) as f:
    mappings = json.load(f)['mappings']
with open(library_path) as f:
    library = json.load(f)

# Extract all gguf filenames from model-library.json
# Adjust key names to match actual schema
library_ggufs = set()
for entry in library.get('models', library if isinstance(library, list) else []):
    gguf = entry.get('gguf_file') or entry.get('filename') or entry.get('gguf')
    if gguf:
        library_ggufs.add(gguf)

failed = False
for m in mappings:
    gguf = m['ods_gguf_file']
    if gguf not in library_ggufs:
        print(f"FAIL: {gguf} not found in model-library.json")
        failed = True

if failed:
    sys.exit(1)
print("PASS: all ods_gguf_file entries present in model-library.json")
EOF
}

_test_catalog_alignment

echo ""
echo "[PASS] All llmfit-advisor tests passed ($(ls "$FIXTURES_DIR"/*.json | wc -l) fixtures)"
