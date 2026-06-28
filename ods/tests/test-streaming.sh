#!/bin/bash
# M8 Missing Test: Streaming Test
# Tests LLM streaming responses

LLAMA_SERVER_URL="http://localhost:8080"
MODEL="qwen2.5-32b-instruct"

# Portable millisecond timestamp (macOS BSD date lacks %N)
_now_ms() {
    python3 -c 'import time; print(int(time.time() * 1000))' 2>/dev/null || echo "$(date +%s)000"
}

echo "=== M8 Test: Streaming ==="

# Test streaming endpoint
START=$(_now_ms)
RESPONSE=$(curl -s -N -X POST "$LLAMA_SERVER_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$MODEL\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Count to 5\"}],
    \"stream\": true,
    \"max_tokens\": 20
  }" 2>/dev/null | head -c 500)
END=$(_now_ms)
LATENCY=$(( END - START ))

# Check for streaming data prefix (should contain "data:")
if echo "$RESPONSE" | grep -q "data:"; then
  echo "✅ PASS: Streaming response received (${LATENCY}ms)"
  exit 0
else
  echo "❌ FAIL: No streaming data detected (${LATENCY}ms)"
  echo "Response preview: ${RESPONSE:0:100}"
  exit 1
fi
