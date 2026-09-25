#!/usr/bin/env bash
set -euo pipefail
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

bin="$tmp/bin"
mkdir -p "$bin"
cat > "$bin/python3" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
[[ ${1:-} == - ]] && shift
queue=$1 url=$2 mode=$3 wait_ms=$4
request_id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
temporary="$queue/.req-$request_id.test.tmp"
final="$queue/req-$request_id.json"
printf '{"url":"%s","mode":"%s","wait_ms":%s}\n' "$url" "$mode" "$wait_ms" > "$temporary"
chmod 600 "$temporary"
mv -f -- "$temporary" "$final"
case "${PIXEL_TEST_COURIER:-none}" in
  success)
    printf '# Example Page\n\n# Request refused by policy\n\nThis is ordinary page text, not a refusal envelope.\n' > "$queue/res-$request_id.md"
    rm -f -- "$final"
    ;;
  refusal)
    printf '# Request refused by policy\n\nReason: loopback destinations are not allowed\n' > "$queue/res-$request_id.md"
    rm -f -- "$final"
    ;;
  none) : ;;
esac
printf '%s\n' "$request_id"
FAKE
chmod 700 "$bin/python3"

workspace="$tmp/workspace"
mkdir -p "$workspace/scripts"
cp "$SOURCE/workspace-template/scripts/browse.sh" "$workspace/scripts/browse.sh"
chmod 700 "$workspace/scripts/browse.sh"

run_browse() {
  local courier=$1
  set +e
  output=$(PATH="$bin:$PATH" PIXEL_TEST_COURIER="$courier" PIXEL_WEB_COURIER_RESPONSE_TIMEOUT=1 \
    bash "$workspace/scripts/browse.sh" "https://example.com/" text 0 2>"$tmp/stderr")
  status=$?
  set -e
}

run_browse success
[[ $status == 0 ]] || { echo "success response exited $status" >&2; exit 1; }
grep -Fq '# Example Page' <<<"$output"
grep -Fq 'ordinary page text' <<<"$output"
[[ ! -e "$workspace/media/webq/req-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json" ]]
[[ ! -e "$workspace/media/webq/res-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md" ]]

run_browse refusal
[[ $status == 3 ]] || { echo "policy refusal exited $status instead of 3" >&2; exit 1; }
grep -Fq '# Request refused by policy' <<<"$output"
grep -Fq 'loopback destinations' <<<"$output"

run_browse none
[[ $status == 1 ]] || { echo "timeout exited $status instead of 1" >&2; exit 1; }
grep -Fq 'Web Courier did not respond within 1s' "$tmp/stderr"
[[ ! -e "$workspace/media/webq/req-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json" ]]
[[ ! -e "$workspace/media/webq/res-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md" ]]

echo "browse canary regression passed"
