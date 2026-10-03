#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
mode=${1:-10}
[[ "$mode" == forever || "$mode" =~ ^[1-9][0-9]{0,3}$ ]] || { echo "Usage: ./pixel pressure ITERATIONS|forever" >&2; exit 2; }
report=${PIXEL_PRESSURE_REPORT:-/tmp/pixel-operations-pressure.jsonl}
[[ "$report" == /* && "$report" != "$ROOT"/* ]] || { echo "Pressure report must be an absolute path outside the repository" >&2; exit 2; }
mkdir -p "$(dirname "$report")"
touch "$report"

cd "$ROOT"
bash tests/static.sh
iteration=0
while [[ "$mode" == forever || $iteration -lt $mode ]]; do
  iteration=$((iteration + 1))
  seed=$(( $(date +%s) ^ $$ ^ (iteration * 7919) ))
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  if python3 security-evals/source-pressure/fuzz.py --seed "$seed" --iterations 1000 \
    && python3 security-evals/operations-pressure/fuzz.py --seed "$seed" --cases 1000 \
    && python3 security-evals/operations-pressure/race.py \
    && python3 -m unittest tests.test_ops_broker tests.test_ops_runner -q; then
    status=passed
  else
    status=failed
  fi
  printf '{"schemaVersion":1,"startedAt":"%s","iteration":%s,"seed":%s,"status":"%s"}\n' \
    "$started" "$iteration" "$seed" "$status" >> "$report"
  printf 'Pixel pressure iteration %s seed=%s %s\n' "$iteration" "$seed" "$status"
  [[ "$status" == passed ]] || exit 1
done
printf 'Pixel pressure loop passed; report=%s\n' "$report"
