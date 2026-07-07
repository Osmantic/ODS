#!/bin/bash
# M8 Missing Test: STT Full Test
# Tests actual Whisper speech-to-text inference

WHISPER_URL="http://localhost:9000"

# Portable millisecond timestamp (macOS BSD date lacks %N)
_now_ms() {
    python3 -c 'import time; print(int(time.time() * 1000))' 2>/dev/null || echo "$(date +%s)000"
}

echo "=== M8 Test: STT Full (Whisper Inference) ==="

# Create a small test audio file (silence + tone)
# Using /dev/urandom to generate a dummy WAV-like file for testing
# In production, this would use a real audio sample

# Check if we can generate a simple test
if ! command -v ffmpeg &> /dev/null; then
  echo "⚠️  SKIP: ffmpeg not available for audio generation"
  echo "ℹ️  Manual test: Upload audio to $WHISPER_URL/v1/audio/transcriptions"
  exit 0
fi

# Create test audio (1 second of silence)
TEST_AUDIO="/tmp/test_audio.wav"
ffmpeg -f lavfi -i anullsrc=r=16000:cl=mono -t 1 -acodec pcm_s16le "$TEST_AUDIO" -y 2>/dev/null

# Test STT endpoint
START=$(_now_ms)
RESPONSE=$(curl -s -X POST "$WHISPER_URL/v1/audio/transcriptions" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@$TEST_AUDIO" \
  -F "model=whisper-1" 2>/dev/null)
END=$(_now_ms)
LATENCY=$(( END - START ))

# Cleanup
rm -f "$TEST_AUDIO"

# Check response
if echo "$RESPONSE" | grep -qE "text|transcript"; then
  echo "✅ PASS: STT transcription received (${LATENCY}ms)"
  echo "   Transcript: $(echo "$RESPONSE" | grep -o '"text":"[^"]*"' | cut -d'"' -f4)"
  exit 0
else
  echo "❌ FAIL: No transcription in response (${LATENCY}ms)"
  echo "   Response: ${RESPONSE:0:100}"
  exit 1
fi
