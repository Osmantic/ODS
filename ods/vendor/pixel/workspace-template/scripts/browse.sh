#!/usr/bin/env bash
# Render a public web page through Pixel's host-side Web Courier.
set -euo pipefail
url=${1:?Usage: scripts/browse.sh URL [text|links|screenshot|raw] [wait_ms]}
mode=${2:-text}
wait_ms=${3:-0}
case "$mode" in text|links|screenshot|raw) ;; *) echo "Invalid mode: $mode" >&2; exit 2 ;; esac
[[ "$wait_ms" =~ ^[0-9]+$ && "$wait_ms" -le 15000 ]] || { echo "wait_ms must be 0..15000" >&2; exit 2; }
[[ ${#url} -le 4096 && "$url" != *$'\n'* && "$url" != *$'\r'* ]] || { echo "URL is too long or contains a newline" >&2; exit 2; }

workspace=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
queue="$workspace/media/webq"
mkdir -p "$queue"
request_id=$(python3 - "$queue" "$url" "$mode" "$wait_ms" <<'PY'
import json, os, secrets, sys
queue, url, mode, wait_ms = sys.argv[1:]
request_id = secrets.token_hex(16)
temporary = os.path.join(queue, f".req-{request_id}.{secrets.token_hex(8)}.tmp")
final = os.path.join(queue, f"req-{request_id}.json")
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
fd = os.open(temporary, flags, 0o600)
try:
    os.write(fd, (json.dumps({"url": url, "mode": mode, "wait_ms": int(wait_ms)}) + "\n").encode())
    os.fsync(fd)
finally:
    os.close(fd)
os.replace(temporary, final)
print(request_id)
PY
)
response="$queue/res-$request_id.md"
timeout=${PIXEL_WEB_COURIER_RESPONSE_TIMEOUT:-75}
[[ "$timeout" =~ ^[0-9]+$ && "$timeout" -ge 1 && "$timeout" -le 180 ]] || timeout=75
for ((second=0; second<timeout; second++)); do
  [[ -f "$response" && ! -L "$response" ]] && break
  sleep 1
done
if [[ ! -f "$response" || -L "$response" ]]; then
  rm -f -- "$queue/req-$request_id.json"
  echo "Web Courier did not respond within ${timeout}s; ask the operator to check the configured Web Courier user service." >&2
  exit 1
fi
# Preserve the exact courier response body on standard output, but treat an explicit
# policy refusal as a non-zero outcome so callers can distinguish "the page loaded" from
# "the Courier refused this request by policy" without losing the refusal reason text.
refused=0
first_line=''
IFS= read -r first_line < "$response" || true
if [[ "$first_line" == '# Request refused by policy' ]]; then refused=1; fi
cat -- "$response"
rm -f -- "$response"
[[ $refused == 0 ]] || exit 3
