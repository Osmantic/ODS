#!/bin/bash
# M8 Missing Test: Embeddings Full Test
# Tests actual embedding vector generation

LLAMA_SERVER_URL="http://localhost:8080"

# Portable millisecond timestamp (macOS BSD date lacks %N)
_now_ms() {
    python3 -c 'import time; print(int(time.time() * 1000))' 2>/dev/null || echo "$(date +%s)000"
}

echo "=== M8 Test: Embeddings Full ==="

TEST_TEXT="The quick brown fox jumps over the lazy dog"

# Test embeddings endpoint
START=$(_now_ms)
RESPONSE=$(curl -s -X POST "$LLAMA_SERVER_URL/v1/embeddings" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"qwen2.5-32b-instruct\",
    \"input\": \"$TEST_TEXT\"
  }" 2>/dev/null)
END=$(_now_ms)
LATENCY=$(( END - START ))

# Check for embedding array
if echo "$RESPONSE" | grep -q '"embedding":\['; then
  # Extract vector dimension
  DIM=$(echo "$RESPONSE" | grep -o '"embedding":\[[^]]*\]' | head -1 | tr ',' '\n' | wc -l)
  echo "✅ PASS: Embeddings generated (${LATENCY}ms, ${DIM} dimensions)"
  exit 0
else
  echo "❌ FAIL: No embedding vector in response (${LATENCY}ms)"
  echo "   Response: ${RESPONSE:0:100}"
  exit 1
fi
