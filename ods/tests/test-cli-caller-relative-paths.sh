#!/usr/bin/env bash
# Paths given to `ods` resolve against the directory it was run from. These
# commands cd into the install directory before they use the path, so a
# relative path used to land there instead: `ods doctor --report r.json` wrote
# the report into the install directory, `ods backup -o backups` wrote the
# backup there (not to the other disk the user named), and
# `ods remote-provider ... --api-key-file key.txt` failed with "file not found".

set -euo pipefail

# ods-cli needs Bash 4+; macOS ships 3.2.
if (( BASH_VERSINFO[0] < 4 )); then
    for modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$modern_bash" ]] && exec "$modern_bash" "$0" "$@"
    done
    echo "[SKIP] ods-cli requires Bash 4+"
    exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"

fail() { printf '[FAIL] %s\n' "$*" >&2; [[ ! -f "${TMP:-}/out.log" ]] || cat "$TMP/out.log" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }
command -v jq >/dev/null 2>&1 || fail "jq is required"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
INSTALL="$TMP/install"
CALLER="$TMP/caller"
mkdir -p "$INSTALL/scripts" "$TMP/home" "$TMP/bin" "$CALLER"
printf 'services: {}\n' > "$INSTALL/docker-compose.base.yml"
printf 'DASHBOARD_API_KEY=test-dashboard-key\n' > "$INSTALL/.env"

# The doctor script writes its JSON report to the path it is given.
cat > "$INSTALL/scripts/ods-doctor.sh" <<'SH'
#!/usr/bin/env bash
printf '{"summary":{}}\n' > "$1"
SH
# The backup script records the arguments the CLI passed it.
cat > "$INSTALL/ods-backup.sh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$(dirname "$0")/backup-args"
SH
# curl stands in for Dashboard API and keeps the request payload.
cat > "$TMP/bin/curl" <<SH
#!/usr/bin/env bash
out=""
while [[ \$# -gt 0 ]]; do
    case "\$1" in
        --output) out="\$2"; shift 2 ;;
        --data-binary) cp "\${2#@}" "$TMP/payload.json"; shift 2 ;;
        *) shift ;;
    esac
done
printf '{}' > "\$out"
printf '200'
SH
chmod +x "$INSTALL/scripts/ods-doctor.sh" "$INSTALL/ods-backup.sh" "$TMP/bin/curl"

# ods ARGS...: run the real CLI from the caller's directory, as a user would.
ods() {
    (cd "$CALLER" && HOME="$TMP/home" INSTALL_DIR="$INSTALL" PATH="$TMP/bin:$PATH" \
        "$BASH" "$ODS_CLI" "$@") > "$TMP/out.log" 2>&1 < /dev/null \
        || fail "ods $* exited non-zero"
}

ods doctor --json --report report.json
[[ -f "$CALLER/report.json" ]] || fail "doctor --report report.json was not written to the caller's directory"
[[ ! -e "$INSTALL/report.json" ]] || fail "doctor --report report.json was written into the install directory"
pass "doctor --report resolves a relative path from the caller's directory"

ods doctor --json "$TMP/absolute-report.json"
[[ -f "$TMP/absolute-report.json" ]] || fail "doctor with an absolute report path"
pass "doctor still accepts an absolute report path"

ods backup --description "-o" -o backups -t full
expected="$(printf '%s\n' --description -o -o "$CALLER/backups" -t full)"
[[ "$(cat "$INSTALL/backup-args")" == "$expected" ]] \
    || fail "backup arguments: $(cat "$INSTALL/backup-args")"
pass "backup --output resolves a relative directory from the caller's directory; other values pass through"

ods backup --output "$TMP/abs-backups"
[[ "$(cat "$INSTALL/backup-args")" == "$(printf '%s\n' --output "$TMP/abs-backups")" ]] \
    || fail "backup with an absolute --output: $(cat "$INSTALL/backup-args")"
pass "backup still accepts an absolute --output"

ods backup
[[ -z "$(cat "$INSTALL/backup-args")" ]] || fail "plain backup passed arguments: $(cat "$INSTALL/backup-args")"
pass "plain backup still passes no arguments"

printf 'sk-from-caller-dir\n' > "$CALLER/key.txt"
ods remote-provider plan configure --base-url https://api.example.test/v1 --model m --api-key-file key.txt
[[ "$(jq -r '.secrets.apiKey // empty' "$TMP/payload.json")" == "sk-from-caller-dir" ]] \
    || fail "remote-provider --api-key-file key.txt did not read the caller's file: $(cat "$TMP/payload.json")"
pass "remote-provider --api-key-file resolves a relative path from the caller's directory"

printf 'ssh-private-key\n' > "$CALLER/id_test"
printf 'host.example.test ssh-ed25519 AAAA\n' > "$CALLER/known_hosts"
ods remote-provider plan configure --transport ssh --ssh-host host.example.test --ssh-user u \
    --ssh-inference-host 127.0.0.1 --ssh-inference-port 8080 \
    --base-url http://127.0.0.1:8080/v1 --model m --api-key-file key.txt --ssh-private-key-file id_test --ssh-known-hosts-file known_hosts
[[ "$(jq -r '.secrets.sshPrivateKey // empty' "$TMP/payload.json")" == "ssh-private-key" ]] \
    || fail "--ssh-private-key-file id_test did not read the caller's file"
[[ "$(jq -r '.secrets.sshKnownHosts // empty' "$TMP/payload.json")" == "host.example.test ssh-ed25519 AAAA" ]] \
    || fail "--ssh-known-hosts-file known_hosts did not read the caller's file"
pass "remote-provider SSH key files resolve relative paths from the caller's directory"
