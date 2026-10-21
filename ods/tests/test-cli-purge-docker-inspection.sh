#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/foreign/data/searxng"
printf 'foreign\n' > "$TMP/foreign/data/searxng/sentinel"
cat > "$TMP/bin/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CAPTURE"
[[ "$1" == ps ]] || { echo 'Unexpected Docker mutation' >&2; exit 99; }
case "$SCENARIO" in
 failure) echo 'Cannot connect to the Docker daemon' >&2; exit 1 ;;
 partial) echo ods-searxng; exit 1 ;;
 running) echo ods-searxng ;;
 unrelated) echo ods-searxng-other ;;
 stopped) : ;;
esac
EOF
chmod +x "$TMP/bin/docker"
for command in sudo systemctl service launchctl powershell.exe curl; do
    printf '#!/usr/bin/env bash\necho "Unexpected host command: $0" >&2\nexit 99\n' > "$TMP/bin/$command"
    chmod +x "$TMP/bin/$command"
done
export PATH="$TMP/bin:$PATH"
for scenario in failure partial running stopped unrelated cancel; do
    fixture="$TMP/$scenario"
    mkdir -p "$fixture/data/searxng"
    touch "$fixture/docker-compose.base.yml"
    printf 'keep\n' > "$fixture/data/searxng/sentinel"
    # Both selectors override a deliberately inherited unrelated installation.
    export INSTALL_DIR="$fixture" ODS_HOME="$fixture" ODS_INSTALL_DIR="$TMP/foreign"
    export CAPTURE="$fixture/docker.log" SCENARIO="$scenario"
    reply=searxng
    [[ "$scenario" != cancel ]] || { SCENARIO=stopped; reply=no; }
    if output=$(printf '%s\n' "$reply" | bash "$ROOT/ods-cli" purge search 2>&1); then rc=0; else rc=$?; fi
    case "$scenario" in
        failure|partial)
            [[ "$rc" != 0 && -f "$fixture/data/searxng/sentinel" ]] || { echo "FAIL: $scenario inspection allowed deletion"; exit 1; }
            [[ "$output" == *'Cannot verify whether searxng is running'* ]] || { echo "FAIL: missing recovery error: $output"; exit 1; } ;;
        running)
            [[ "$rc" != 0 && -f "$fixture/data/searxng/sentinel" && "$output" == *'container is still running'* ]] || { echo "FAIL: running container was not refused"; exit 1; } ;;
        stopped|unrelated)
            [[ "$rc" == 0 && ! -e "$fixture/data/searxng" ]] || { echo "FAIL: stopped service purge failed: $output"; exit 1; } ;;
        cancel)
            [[ "$rc" == 0 && -f "$fixture/data/searxng/sentinel" ]] || { echo 'FAIL: cancellation changed data'; exit 1; } ;;
    esac
    [[ $(cat "$TMP/foreign/data/searxng/sentinel") == foreign ]] || { echo 'FAIL: foreign installation changed'; exit 1; }
    [[ $(wc -l < "$CAPTURE") == 1 ]] || { echo 'FAIL: unexpected Docker calls'; exit 1; }
    echo "PASS: public purge $scenario preserves the Docker inspection contract"
done
