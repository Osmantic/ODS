#!/bin/bash
# M8 Missing Test: TTS Full Test
# Tests actual Kokoro text-to-speech inference

KOKORO_URL="http://localhost:8880"

# Portable millisecond timestamp (macOS BSD date lacks %N)
_now_ms() {
    python3 -c 'import time; print(int(time.time() * 1000))' 2>/dev/null || echo "$(date +%s)000"
}

echo "=== M8 Test: TTS Full (Kokoro Inference) ==="

# Test TTS endpoint with simple text
START=$(_now_ms)
RESPONSE=$(curl -s -X POST "$KOKORO_URL/v1/audio/speech" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kokoro",
    "input": "Hello, this is a test.",
    "voice": "af_bella",
    "response_format": "mp3"
  }' 2>/dev/null)
END=$(_now_ms)
LATENCY=$(( END - START ))

# Check if we got audio data (MP3 starts with ID3 or empty binary)
if echo "$RESPONSE" | head -c 10 | xxd | grep -qE "ID3|fffb|5249"; then
  SIZE=$(echo "$RESPONSE" | wc -c)
  echo "✅ PASS: TTS audio generated (${LATENCY}ms, ${SIZE} bytes)"
  exit 0
else
  # Check for JSON error response
  if echo "$RESPONSE" | grep -q "error"; then
    echo "❌ FAIL: TTS error response (${LATENCY}ms)"
    echo "   Response: ${RESPONSE:0:100}"
  else
    echo "⚠️  PARTIAL: Response received but may not be valid audio (${LATENCY}ms)"
    echo "   Preview: ${RESPONSE:0:50}"
  fi
  exit 1
fi
