#!/bin/bash
# Benchmark script to measure ods status performance improvement
# Compares sequential vs parallel health check execution time

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Portable millisecond timestamp (macOS BSD date lacks %N)
_now_ms() {
    python3 -c 'import time; print(int(time.time() * 1000))' 2>/dev/null || echo "$(date +%s)000"
}

echo -e "${BLUE}━━━ ODS Status Performance Benchmark ━━━${NC}"
echo ""

# Check if ods-cli exists
if [[ ! -f "$PROJECT_DIR/ods-cli" ]]; then
    echo "Error: ods-cli not found at $PROJECT_DIR/ods-cli"
    exit 1
fi

# Mock health check endpoints for testing
setup_mock_services() {
    echo -e "${CYAN}Setting up mock health endpoints...${NC}"

    # Create a simple HTTP server that responds to health checks
    # This simulates the actual service health endpoints
    for port in 3000 3001 3002 4000 5678 6333 7860 8080 8090 8188 8880 8888 9000; do
        # Start a simple netcat listener that responds with 200 OK
        (while true; do echo -e "HTTP/1.1 200 OK\r\n\r\nOK" | nc -l -p $port -q 1 2>/dev/null; done) &
        echo $! >> /tmp/benchmark-mock-pids.txt
    done

    sleep 2
    echo -e "${GREEN}✓${NC} Mock services started"
}

cleanup_mock_services() {
    echo ""
    echo -e "${CYAN}Cleaning up mock services...${NC}"
    if [[ -f /tmp/benchmark-mock-pids.txt ]]; then
        while read pid; do
            kill $pid 2>/dev/null || true
        done < /tmp/benchmark-mock-pids.txt
        rm -f /tmp/benchmark-mock-pids.txt
    fi
    echo -e "${GREEN}✓${NC} Cleanup complete"
}

trap cleanup_mock_services EXIT

# Simulate sequential health checks (old implementation)
benchmark_sequential() {
    local start
    start=$(_now_ms)

    # Simulate 13 sequential curl calls with 1 second timeout each
    for i in {1..13}; do
        curl -sf --max-time 1 http://127.0.0.1:8080/health > /dev/null 2>&1 || true
    done

    local end duration
    end=$(_now_ms)
    duration=$(( end - start ))
    echo $duration
}

# Simulate parallel health checks (new implementation)
benchmark_parallel() {
    local start tmpdir
    start=$(_now_ms)
    tmpdir=$(mktemp -d)
    local -a pids=()

    # Launch 13 parallel curl calls
    for i in {1..13}; do
        (curl -sf --max-time 1 http://127.0.0.1:8080/health > /dev/null 2>&1 && echo "ok" > "$tmpdir/$i") &
        pids+=($!)
    done

    # Wait for all to complete
    for pid in "${pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    local end duration
    end=$(_now_ms)
    duration=$(( end - start ))
    rm -rf "$tmpdir"
    echo $duration
}

echo -e "${CYAN}Running benchmarks (3 iterations each)...${NC}"
echo ""

# Run sequential benchmark
echo -e "${YELLOW}Sequential health checks:${NC}"
seq_total=0
for i in {1..3}; do
    seq_time=$(benchmark_sequential)
    echo "  Run $i: ${seq_time}ms"
    seq_total=$((seq_total + seq_time))
done
seq_avg=$((seq_total / 3))

echo ""

# Run parallel benchmark
echo -e "${YELLOW}Parallel health checks:${NC}"
par_total=0
for i in {1..3}; do
    par_time=$(benchmark_parallel)
    echo "  Run $i: ${par_time}ms"
    par_total=$((par_total + par_time))
done
par_avg=$((par_total / 3))

echo ""
echo -e "${BLUE}━━━ Results ━━━${NC}"
echo ""
printf "  Sequential average: %6dms\n" $seq_avg
printf "  Parallel average:   %6dms\n" $par_avg
echo ""

if [[ $seq_avg -gt 0 ]]; then
    speedup=$(awk "BEGIN {printf \"%.1f\", $seq_avg / $par_avg}")
    improvement=$(awk "BEGIN {printf \"%.0f\", (($seq_avg - $par_avg) / $seq_avg) * 100}")
    echo -e "  ${GREEN}Speedup: ${speedup}x faster${NC}"
    echo -e "  ${GREEN}Improvement: ${improvement}% reduction in execution time${NC}"
fi

echo ""
