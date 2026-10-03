#!/usr/bin/env bash
set -euo pipefail
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

fake=$tmp/bin
mkdir -p "$fake"
cat > "$fake/sudo" <<'SUDO'
#!/usr/bin/env bash
if [[ ${1:-} == -u ]]; then shift 2; fi
exec "$@"
SUDO
cat > "$fake/setfacl" <<'SETFACL'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$PIXEL_ACL_TEST_LOG"
SETFACL
cat > "$fake/test" <<'TEST'
#!/usr/bin/env bash
# Simulate a supported host whose external test -r/-w ignores POSIX ACLs.
case "${1:-}" in
  -r|-w) exit 1 ;;
  *) exec /usr/bin/test "$@" ;;
esac
TEST
chmod 700 "$fake/sudo" "$fake/setfacl" "$fake/test"
export PATH="$fake:$PATH"
export PIXEL_ACL_TEST_LOG=$tmp/setfacl.log
: > "$PIXEL_ACL_TEST_LOG"

# shellcheck source=scripts/lib/common.sh disable=SC1091
source "$SOURCE/scripts/lib/common.sh"
# shellcheck source=scripts/lib/broker-reader-acls.sh disable=SC1091
source "$SOURCE/scripts/lib/broker-reader-acls.sh"

ops=$tmp/ops
mkdir -p "$ops/results" "$ops/events" "$ops/private" "$ops/plans" "$ops/approvals" "$ops/authority"
printf '{}\n' > "$ops/inventory.json"
export PIXEL_OPS_BROKER_ENABLED=1 PIXEL_OPS_READER_USER=pixel-reader
export PIXEL_OPS_BROKER_STATE_DIR=$ops PIXEL_OPS_RESULT_DIR=$ops/results
export PIXEL_OPS_EVENT_DIR=$ops/events PIXEL_OPS_INVENTORY_PATH=$ops/inventory.json
pixel_apply_ops_reader_acls
grep -Fx -- "-m u:pixel-reader:--x,d:u:pixel-reader:r-- $ops" "$PIXEL_ACL_TEST_LOG" >/dev/null
grep -Fx -- "-m u:pixel-reader:r-x,d:u:pixel-reader:r-- $ops/results" "$PIXEL_ACL_TEST_LOG" >/dev/null
grep -Fx -- "-m u:pixel-reader:r-x,d:u:pixel-reader:r-- $ops/events" "$PIXEL_ACL_TEST_LOG" >/dev/null
grep -Fx -- "-m u:pixel-reader:r-- $ops/inventory.json" "$PIXEL_ACL_TEST_LOG" >/dev/null
if grep -Eq '/(private|plans|approvals|authority)( |$)' "$PIXEL_ACL_TEST_LOG"; then
  echo "Restore ACL repair widened an Operations authority path" >&2; exit 1
fi

frontier=$tmp/frontier
mkdir -p "$frontier/results" "$frontier/events" "$frontier/metrics/qualifications" "$frontier/private" "$frontier/plans" "$frontier/approvals" "$frontier/authority"
printf '{}\n' > "$frontier/metrics/usage.json"
export PIXEL_FRONTIER_BROKER_ENABLED=1 PIXEL_FRONTIER_READER_USER=frontier-reader
export PIXEL_FRONTIER_BROKER_STATE_DIR=$frontier PIXEL_FRONTIER_RESULT_DIR=$frontier/results
export PIXEL_FRONTIER_EVENT_DIR=$frontier/events
pixel_apply_frontier_reader_acls
grep -Fx -- "-m u:frontier-reader:--x,d:u:frontier-reader:r-- $frontier" "$PIXEL_ACL_TEST_LOG" >/dev/null
grep -Fx -- "-m u:frontier-reader:r-x,d:u:frontier-reader:r-- $frontier/results" "$PIXEL_ACL_TEST_LOG" >/dev/null
grep -Fx -- "-m u:frontier-reader:r-x,d:u:frontier-reader:r-- $frontier/events" "$PIXEL_ACL_TEST_LOG" >/dev/null
grep -Fx -- "-m u:frontier-reader:r-x,d:u:frontier-reader:r-- $frontier/metrics" "$PIXEL_ACL_TEST_LOG" >/dev/null
grep -Fx -- "-m u:frontier-reader:r-x,d:u:frontier-reader:r-- $frontier/metrics/qualifications" "$PIXEL_ACL_TEST_LOG" >/dev/null
grep -Fx -- "-m u:frontier-reader:r-- $frontier/metrics/usage.json" "$PIXEL_ACL_TEST_LOG" >/dev/null
if grep -Eq "$frontier/(private|plans|approvals|authority)( |$)" "$PIXEL_ACL_TEST_LOG"; then
  echo "Restore ACL repair widened a Frontier authority path" >&2; exit 1
fi

# A linked public projection must fail closed before setfacl can follow it.
linked=$tmp/linked-results
ln -s "$ops/private" "$linked"
PIXEL_OPS_RESULT_DIR=$linked
if ( pixel_apply_ops_reader_acls ) >/dev/null 2>&1; then
  echo "Restore ACL repair followed a linked Operations projection" >&2; exit 1
fi

# A linked public file must also fail closed before setfacl can follow it.
PIXEL_OPS_RESULT_DIR=$ops/results
printf 'secret\n' > "$ops/private/secret.json"
rm -- "$ops/inventory.json"
ln -s "$ops/private/secret.json" "$ops/inventory.json"
if ( pixel_apply_ops_reader_acls ) >/dev/null 2>&1; then
  echo "Restore ACL repair followed a linked Operations inventory" >&2; exit 1
fi

printf '%s\n' "restore broker reader ACL tests passed"
