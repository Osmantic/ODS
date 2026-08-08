#!/usr/bin/env bash
# Contract: the resolver's user-extension compose scan must reject everything
# dashboard-api/routers/extensions.py:_scan_compose_content rejects.
#
# _scan_user_compose_content documents itself as mirroring that function, but
# the two drifted. The resolver is the layer that builds the compose stack the
# stack actually starts with, so anything it lets through reaches a running
# container even when the API would have refused the same extension.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESOLVER="$SCRIPT_DIR/../scripts/resolve-compose-stack.sh"

pass() { echo "  ✓ $1"; }
fail() { echo "  ✗ $1"; exit 1; }

FIXTURE="$(mktemp -d)"
trap 'rm -rf "$FIXTURE"' EXIT
for f in base nvidia amd apple; do : > "$FIXTURE/docker-compose.$f.yml"; done
mkdir -p "$FIXTURE/data/user-extensions"

# Write a user extension whose compose.yaml carries $1 inside the service body.
write_ext() {
  local name="$1" body="$2"
  local dir="$FIXTURE/data/user-extensions/$name"
  mkdir -p "$dir"
  cat > "$dir/manifest.yaml" <<MANIFEST
schema_version: ods.services.v1
id: ${name}
service:
  port: 9999
MANIFEST
  cat > "$dir/compose.yaml" <<COMPOSE
services:
  ${name}:
    image: alpine:3
${body}
COMPOSE
}

# Run the resolver and assert the extension was refused: its compose file must
# not appear in the emitted -f list.
assert_rejected() {
  local name="$1" label="$2"
  local out
  out="$("$RESOLVER" --script-dir "$FIXTURE" --gpu-backend nvidia 2>&1 || true)"
  if grep -q "user-extensions/${name}/compose.yaml" <<<"$out"; then
    fail "$label — resolver merged the extension instead of rejecting it"
  fi
  pass "$label"
}

assert_accepted() {
  local name="$1" label="$2"
  local out
  out="$("$RESOLVER" --script-dir "$FIXTURE" --gpu-backend nvidia 2>&1 || true)"
  grep -q "user-extensions/${name}/compose.yaml" <<<"$out" \
    || fail "$label — a benign extension was rejected"
  pass "$label"
}

echo "Test 1: relative bind-mount escaping the project directory (short form)"
write_ext relesc '    volumes:
      - "../../../../etc:/host-etc:ro"'
assert_rejected relesc "short-form '../' escape refused"
rm -rf "$FIXTURE/data/user-extensions/relesc"

echo "Test 2: relative bind-mount escaping via long-form volume"
write_ext relesclong '    volumes:
      - type: bind
        source: "../../../../etc"
        target: /host-etc'
assert_rejected relesclong "long-form '../' escape refused"
rm -rf "$FIXTURE/data/user-extensions/relesclong"

echo "Test 3: dangerous capability written with the CAP_ prefix"
write_ext capprefix '    cap_add:
      - CAP_SYS_ADMIN'
assert_rejected capprefix "CAP_-prefixed capability refused"
rm -rf "$FIXTURE/data/user-extensions/capprefix"

echo "Test 4: reserved io.docker.* label"
write_ext iodocker '    labels:
      io.docker.something: "1"'
assert_rejected iodocker "reserved io.docker.* label refused"
rm -rf "$FIXTURE/data/user-extensions/iodocker"

echo "Test 5: benign relative mount inside the project still allowed"
write_ext benign '    volumes:
      - "./data/benign:/data"'
assert_accepted benign "in-project relative mount still merges"
rm -rf "$FIXTURE/data/user-extensions/benign"

echo ""
echo "✓ All resolver compose-guard parity tests passed"
