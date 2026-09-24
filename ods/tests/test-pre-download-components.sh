#!/usr/bin/env bash
# Public CLI coverage for selective offline-cache plans.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/pre-download.sh"

OUT="$(bash "$SCRIPT" --component stt --component tts --plan)"
[[ "$OUT" == *"STT: Systran/faster-whisper-large-v3"* ]]
[[ "$OUT" == *"TTS: hexgrad/Kokoro-82M"* ]]
[[ "$OUT" != *"LLM ("* ]]
[[ "$OUT" == *"no network requests were made"* ]]

OUT="$(bash "$SCRIPT" --tier edge --component llm --plan)"
[[ "$OUT" == *"LLM (edge): Qwen/Qwen2.5-7B-Instruct"* ]]
[[ "$OUT" != *"STT:"* ]]

if bash "$SCRIPT" --component llm --plan >/dev/null 2>&1; then
    echo "[FAIL] LLM component accepted without a tier"
    exit 1
fi
if bash "$SCRIPT" --component video --plan >/dev/null 2>&1; then
    echo "[FAIL] unknown component accepted"
    exit 1
fi

echo "[PASS] pre-download plans target only the requested offline components"
