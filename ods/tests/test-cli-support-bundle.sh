#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-cli-support.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

mkdir -p "$FIXTURE/lib" "$FIXTURE/scripts"
cp "$ROOT_DIR/ods-cli" "$FIXTURE/ods-cli"
cp "$ROOT_DIR"/lib/*.sh "$FIXTURE/lib/"
: > "$FIXTURE/docker-compose.base.yml"

cat > "$FIXTURE/scripts/ods-support-bundle.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" > "${SUPPORT_ARGS_LOG:?}"
printf '{"archive":"fixture.tar.gz"}\n'
exit "${SUPPORT_EXIT_CODE:-0}"
SH
chmod +x "$FIXTURE/scripts/ods-support-bundle.sh"

args_log="$FIXTURE/args.log"
output="$(
    ODS_HOME="$FIXTURE" SUPPORT_ARGS_LOG="$args_log" \
        bash "$FIXTURE/ods-cli" support-bundle --json --no-logs
)"

[[ "$output" == '{"archive":"fixture.tar.gz"}' ]]
mapfile -t forwarded < "$args_log"
[[ "${forwarded[*]}" == "--json --no-logs" ]]

set +e
ODS_HOME="$FIXTURE" SUPPORT_ARGS_LOG="$args_log" SUPPORT_EXIT_CODE=7 \
    bash "$FIXTURE/ods-cli" support --json >/dev/null
status=$?
set -e
[[ $status -eq 7 ]]

rm "$FIXTURE/scripts/ods-support-bundle.sh"
set +e
missing_output="$(ODS_HOME="$FIXTURE" bash "$FIXTURE/ods-cli" support-bundle 2>&1)"
status=$?
set -e
[[ $status -eq 1 ]]
[[ "$missing_output" == *"Support bundle generator not found or not executable"* ]]

echo "CLI support-bundle tests passed"
