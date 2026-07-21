#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(git -C "$ROOT_DIR" rev-parse --show-toplevel)"
VERIFIER="$ROOT_DIR/scripts/verify-hosted-bootstrap.sh"
EXPECTED_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

[[ -x "$VERIFIER" ]] || fail "Hosted bootstrap verifier is not executable."

mkdir -p "$TMP_DIR/bin"
cat > "$TMP_DIR/bin/curl" <<'FAKE_CURL'
#!/usr/bin/env bash
set -euo pipefail

headers_file=""
body_file=""
endpoint=""
proto=""
proto_redir=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dump-header|--output|--proto|--proto-redir|--header|--retry|--retry-delay|--connect-timeout|--max-time)
            option="$1"
            value="$2"
            shift 2
            case "$option" in
                --dump-header) headers_file="$value" ;;
                --output) body_file="$value" ;;
                --proto) proto="$value" ;;
                --proto-redir) proto_redir="$value" ;;
            esac
            ;;
        --fail|--silent|--show-error|--location|--tlsv1.2)
            shift
            ;;
        https://*)
            endpoint="$1"
            shift
            ;;
        *)
            echo "Unexpected curl argument: $1" >&2
            exit 64
            ;;
    esac
done

[[ -n "$headers_file" && -n "$body_file" && -n "$endpoint" ]]
[[ "$proto" == "=https" && "$proto_redir" == "=https" ]]
cp "${ODS_TEST_HEADERS:?}" "$headers_file"
cp "${ODS_TEST_BODY:?}" "$body_file"
printf '%s\n' "$endpoint" >> "${ODS_TEST_TRACE:?}"
FAKE_CURL
chmod +x "$TMP_DIR/bin/curl"

write_headers() {
    local channel="${1:-main}"
    local source_ref="${2:-main}"
    local content_type="${3:-Text/Plain; charset=utf-8}"
    local presentation="${4:-script}"

    cat > "$TMP_DIR/headers" <<EOF
HTTP/2 200
content-type: $content_type
x-ods-channel: $channel
x-ods-source-ref: $source_ref
x-ods-presentation: $presentation
x-ods-cache: HIT

EOF
}

cp "$ROOT_DIR/get-ods.sh" "$TMP_DIR/body"
write_headers

PATH="$TMP_DIR/bin:$PATH" \
    ODS_TEST_HEADERS="$TMP_DIR/headers" \
    ODS_TEST_BODY="$TMP_DIR/body" \
    ODS_TEST_TRACE="$TMP_DIR/trace" \
    bash "$VERIFIER" "$EXPECTED_SHA" \
        https://install-one.example/ods.sh \
        https://install-two.example/ods.sh \
        > "$TMP_DIR/success.log"

[[ "$(wc -l < "$TMP_DIR/trace" | tr -d ' ')" == "2" ]] \
    || fail "Verifier did not check every supplied endpoint."
grep -qF "Hosted bootstrap matches $EXPECTED_SHA from main on 2 endpoint(s)." \
    "$TMP_DIR/success.log" \
    || fail "Verifier success summary is missing."

: > "$TMP_DIR/default-trace"
PATH="$TMP_DIR/bin:$PATH" \
    ODS_TEST_HEADERS="$TMP_DIR/headers" \
    ODS_TEST_BODY="$TMP_DIR/body" \
    ODS_TEST_TRACE="$TMP_DIR/default-trace" \
    bash "$VERIFIER" "$EXPECTED_SHA" > "$TMP_DIR/default-success.log"
cat > "$TMP_DIR/expected-default-endpoints" <<'EOF'
https://get.osmantic.com/ods
https://get.osmantic.com/ods.sh
https://get.osmantic.com/ods/main
https://get.osmantic.com/ods/main.sh
https://install.osmantic.com/ods
https://install.osmantic.com/ods.sh
https://install.osmantic.com/ods/main
https://install.osmantic.com/ods/main.sh
https://osmantic.com/get/ods
https://osmantic.com/get/ods.sh
https://osmantic.com/get/ods/main
https://osmantic.com/get/ods/main.sh
EOF
cmp -s "$TMP_DIR/expected-default-endpoints" "$TMP_DIR/default-trace" \
    || fail "Verifier defaults do not cover all twelve active Worker aliases."

write_headers "main" "preview"
if PATH="$TMP_DIR/bin:$PATH" \
    ODS_TEST_HEADERS="$TMP_DIR/headers" \
    ODS_TEST_BODY="$TMP_DIR/body" \
    ODS_TEST_TRACE="$TMP_DIR/trace-wrong-ref" \
    bash "$VERIFIER" "$EXPECTED_SHA" > "$TMP_DIR/wrong-ref.log" 2>&1; then
    fail "Verifier accepted the wrong deployed source ref."
fi
grep -qF "expected main" "$TMP_DIR/wrong-ref.log" \
    || fail "Wrong-ref failure did not explain the expected source contract."

write_headers
printf '\n# changed after deployment\n' >> "$TMP_DIR/body"
if PATH="$TMP_DIR/bin:$PATH" \
    ODS_TEST_HEADERS="$TMP_DIR/headers" \
    ODS_TEST_BODY="$TMP_DIR/body" \
    ODS_TEST_TRACE="$TMP_DIR/trace-wrong-body" \
    bash "$VERIFIER" "$EXPECTED_SHA" > "$TMP_DIR/wrong-body.log" 2>&1; then
    fail "Verifier accepted body bytes that differ from the expected ref."
fi
grep -qF "body does not match" "$TMP_DIR/wrong-body.log" \
    || fail "Wrong-body failure did not identify the content mismatch."

cp "$ROOT_DIR/get-ods.sh" "$TMP_DIR/body"
write_headers "preview"
if PATH="$TMP_DIR/bin:$PATH" \
    ODS_TEST_HEADERS="$TMP_DIR/headers" \
    ODS_TEST_BODY="$TMP_DIR/body" \
    ODS_TEST_TRACE="$TMP_DIR/trace-wrong-channel" \
    bash "$VERIFIER" "$EXPECTED_SHA" > "$TMP_DIR/wrong-channel.log" 2>&1; then
    fail "Verifier accepted the wrong hosted channel."
fi
grep -qF "expected main" "$TMP_DIR/wrong-channel.log" \
    || fail "Wrong-channel failure did not identify the channel contract."

write_headers
sed -i.bak 's#Text/Plain; charset=utf-8#text/html; charset=utf-8#' "$TMP_DIR/headers"
rm -f "$TMP_DIR/headers.bak"
if PATH="$TMP_DIR/bin:$PATH" \
    ODS_TEST_HEADERS="$TMP_DIR/headers" \
    ODS_TEST_BODY="$TMP_DIR/body" \
    ODS_TEST_TRACE="$TMP_DIR/trace-wrong-type" \
    bash "$VERIFIER" "$EXPECTED_SHA" > "$TMP_DIR/wrong-type.log" 2>&1; then
    fail "Verifier accepted a non-script content type."
fi
grep -qF "expected text/plain" "$TMP_DIR/wrong-type.log" \
    || fail "Wrong-content-type failure did not identify the presentation mismatch."

write_headers
sed -i.bak '/^x-ods-presentation:/d' "$TMP_DIR/headers"
rm -f "$TMP_DIR/headers.bak"
if PATH="$TMP_DIR/bin:$PATH" \
    ODS_TEST_HEADERS="$TMP_DIR/headers" \
    ODS_TEST_BODY="$TMP_DIR/body" \
    ODS_TEST_TRACE="$TMP_DIR/trace-missing-presentation" \
    bash "$VERIFIER" "$EXPECTED_SHA" > "$TMP_DIR/missing-presentation.log" 2>&1; then
    fail "Verifier accepted a response without X-ODS-Presentation."
fi
grep -qF "expected script" "$TMP_DIR/missing-presentation.log" \
    || fail "Missing-presentation failure did not identify the response contract."

cat > "$TMP_DIR/headers" <<EOF
HTTP/2 302
location: https://install-two.example/ods.sh
x-ods-channel: main
x-ods-source-ref: main
x-ods-presentation: script

HTTP/2 200
content-type: text/plain; charset=utf-8

EOF
if PATH="$TMP_DIR/bin:$PATH" \
    ODS_TEST_HEADERS="$TMP_DIR/headers" \
    ODS_TEST_BODY="$TMP_DIR/body" \
    ODS_TEST_TRACE="$TMP_DIR/trace-stale-redirect" \
    bash "$VERIFIER" "$EXPECTED_SHA" > "$TMP_DIR/stale-redirect.log" 2>&1; then
    fail "Verifier accepted metadata inherited from a redirect response."
fi
grep -qF "did not return X-ODS-Source-Ref" "$TMP_DIR/stale-redirect.log" \
    || fail "Redirect-header failure did not identify missing final-response metadata."

write_headers
if PATH="$TMP_DIR/bin:$PATH" \
    ODS_TEST_HEADERS="$TMP_DIR/headers" \
    ODS_TEST_BODY="$TMP_DIR/body" \
    ODS_TEST_TRACE="$TMP_DIR/trace-http" \
    bash "$VERIFIER" "$EXPECTED_SHA" \
        http://install.example/ods.sh > "$TMP_DIR/http.log" 2>&1; then
    fail "Verifier accepted a non-HTTPS endpoint."
fi
grep -qF "must use HTTPS" "$TMP_DIR/http.log" \
    || fail "Non-HTTPS failure did not identify the transport requirement."

MALFORMED_REPO="$TMP_DIR/malformed-repo"
mkdir -p "$MALFORMED_REPO/ods/scripts"
cp "$VERIFIER" "$MALFORMED_REPO/ods/scripts/verify-hosted-bootstrap.sh"
cat > "$MALFORMED_REPO/ods/get-ods.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[
EOF
git -C "$MALFORMED_REPO" init -q
git -C "$MALFORMED_REPO" add ods/get-ods.sh
git -C "$MALFORMED_REPO" \
    -c user.name="ODS Test" \
    -c user.email="ods-test@example.invalid" \
    commit -q -m "test malformed bootstrap"
MALFORMED_SHA="$(git -C "$MALFORMED_REPO" rev-parse HEAD)"
cp "$MALFORMED_REPO/ods/get-ods.sh" "$TMP_DIR/body"
write_headers
if PATH="$TMP_DIR/bin:$PATH" \
    ODS_TEST_HEADERS="$TMP_DIR/headers" \
    ODS_TEST_BODY="$TMP_DIR/body" \
    ODS_TEST_TRACE="$TMP_DIR/trace-malformed-body" \
    bash "$MALFORMED_REPO/ods/scripts/verify-hosted-bootstrap.sh" \
        "$MALFORMED_SHA" > "$TMP_DIR/malformed-body.log" 2>&1; then
    fail "Verifier accepted a syntactically invalid bootstrap."
fi
grep -qF "does not pass bash -n" "$TMP_DIR/malformed-body.log" \
    || fail "Malformed-body failure did not identify the syntax error."

echo "[PASS] Hosted bootstrap verifier enforces final-response metadata, HTTPS, syntax, body, and endpoint coverage."
