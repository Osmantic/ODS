#!/bin/bash
# M8 Missing Test: Multi-Turn Conversation Test
# Tests context preservation across multiple exchanges

LLAMA_SERVER_URL="http://localhost:8080"
MODEL="qwen2.5-32b-instruct"

echo "=== M8 Test: Multi-Turn Conversation ==="

# Turn 1: Set context
echo "  Turn 1: Setting context..."
RESPONSE1=$(curl -s -X POST "$LLAMA_SERVER_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$MODEL\",
    \"messages\": [{\"role\": \"user\", \"content\": \"My name is Alice and I live in Boston. Remember this.\"}],
    \"max_tokens\": 50
  }" 2>/dev/null)

# Extract assistant response for history
ASSISTANT1=$(echo "$RESPONSE1" | grep -o '"content":"[^"]*"' | head -1 | cut -d'"' -f4)
echo "    Assistant: ${ASSISTANT1:0:50}..."

# Turn 2: Test recall
echo "  Turn 2: Testing recall..."
RESPONSE2=$(curl -s -X POST "$LLAMA_SERVER_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$MODEL\",
    \"messages\": [
      {\"role\": \"user\", \"content\": \"My name is Alice and I live in Boston. Remember this.\"},
      {\"role\": \"assistant\", \"content\": \"$ASSISTANT1\"},
      {\"role\": \"user\", \"content\": \"What's my name and where do I live?\"}
    ],
    \"max_tokens\": 30
  }" 2>/dev/null)

ANSWER2=$(echo "$RESPONSE2" | grep -o '"content":"[^"]*"' | head -1 | cut -d'"' -f4)
echo "    Assistant: $ANSWER2"

# Validate context preservation
PASS=0
if echo "$ANSWER2" | grep -qi "alice"; then
  echo "  ✅ Name recalled correctly"
  PASS=$((PASS+1))
else
  echo "  ❌ Name NOT recalled"
fi

if echo "$ANSWER2" | grep -qi "boston"; then
  echo "  ✅ Location recalled correctly"
  PASS=$((PASS+1))
else
  echo "  ❌ Location NOT recalled"
fi

if [ $PASS -eq 2 ]; then
  echo ""
  echo "✅ PASS: Multi-turn context preserved (2/2 facts recalled)"
  exit 0
else
  echo ""
  echo "❌ FAIL: Context lost ($PASS/2 facts recalled)"
  exit 1
fi
