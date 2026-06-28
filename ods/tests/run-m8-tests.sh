#!/bin/bash
# M8 Complete Test Suite
# Runs all 6 missing tests from gap analysis

echo "======================================"
echo "M8 Complete Test Suite"
echo "======================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASSED=0
FAILED=0
SKIPPED=0

run_test() {
  local TEST_NAME="$1"
  local TEST_SCRIPT="$2"
  
  echo "---"
  echo "Running: $TEST_NAME"
  if bash "$SCRIPT_DIR/$TEST_SCRIPT"; then
    PASSED=$((PASSED+1))
  else
    FAILED=$((FAILED+1))
  fi
  echo ""
}

# Run all 6 missing tests
run_test "Streaming Test" "test-streaming.sh"
run_test "STT Full Test" "test-stt-full.sh"
run_test "TTS Full Test" "test-tts-full.sh"
run_test "Embeddings Full Test" "test-embeddings-full.sh"
run_test "Multi-Turn Test" "test-multi-turn.sh"
run_test "Concurrency Test" "test-concurrency.sh"

echo "======================================"
echo "M8 Test Summary"
echo "======================================"
echo "Passed: $PASSED"
echo "Failed: $FAILED"
echo "Total:  $((PASSED+FAILED))"
echo ""

if [ $FAILED -eq 0 ]; then
  echo "✅ All M8 tests passed!"
  exit 0
else
  echo "⚠️  Some tests failed — review output above"
  exit 1
fi
