#!/usr/bin/env bash
# ods-cli commands cd into INSTALL_DIR before using paths the user typed.
# Relative --report / --output / --*-file paths must resolve against the
# directory ods was run from, not the install directory.
set -euo pipefail

ODS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

INSTALL="$TMP/install"
USER_DIR="$TMP/user"
mkdir -p "$INSTALL/scripts" "$USER_DIR"
: > "$INSTALL/docker-compose.base.yml"
: > "$INSTALL/.env"

cat > "$INSTALL/scripts/ods-doctor.sh" <<'EOF'
#!/usr/bin/env bash
printf '{}\n' > "$1"
EOF
cat > "$INSTALL/ods-backup.sh" <<'EOF'
#!/usr/bin/env bash
while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--output) printf 'OUTPUT=%s\n' "$2"; shift 2 ;;
        *) shift ;;
    esac
done
EOF
chmod +x "$INSTALL/scripts/ods-doctor.sh" "$INSTALL/ods-backup.sh"

ods() { (cd "$USER_DIR" && INSTALL_DIR="$INSTALL" bash "$ODS_ROOT/ods-cli" "$@"); }
fail() { echo "[FAIL] $*" >&2; exit 1; }

ods doctor --json --report ./report.json >/dev/null
[[ -f "$USER_DIR/report.json" ]] || fail "doctor --report ./report.json was not written to the caller's directory"
[[ ! -e "$INSTALL/report.json" ]] || fail "doctor --report wrote into the install directory"

ods doctor --json positional.json >/dev/null
[[ -f "$USER_DIR/positional.json" ]] || fail "doctor positional report path not resolved against the caller's directory"

ods doctor --json --report "$TMP/absolute.json" >/dev/null
[[ -f "$TMP/absolute.json" ]] || fail "doctor --report absolute path changed"

out="$(ods backup -o ./backups)"
[[ "$out" == "OUTPUT=$USER_DIR/./backups" ]] || fail "backup -o ./backups resolved to: $out"
out="$(ods backup --output "$TMP/abs-backups")"
[[ "$out" == "OUTPUT=$TMP/abs-backups" ]] || fail "backup --output absolute path changed: $out"

grep -q 'api_key_file="$(_caller_path ' "$ODS_ROOT/ods-cli" || fail "remote-provider --api-key-file is not caller-resolved"
grep -q 'ssh_private_key_file="$(_caller_path ' "$ODS_ROOT/ods-cli" || fail "remote-provider --ssh-private-key-file is not caller-resolved"
grep -q 'ssh_known_hosts_file="$(_caller_path ' "$ODS_ROOT/ods-cli" || fail "remote-provider --ssh-known-hosts-file is not caller-resolved"

echo "[PASS] ods-cli resolves relative path arguments against the caller's directory"
