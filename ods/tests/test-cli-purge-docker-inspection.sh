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
 companion|legacy|frozen|missing-recipe)
     # Respect the requested projection so the old base-file-only guard is
     # exercised by these regressions rather than hidden by the Docker stub.
     case "$SCENARIO" in
         legacy) recipe="$INSTALL_DIR/docker-compose.yml" ;;
         frozen) recipe="$INSTALL_DIR/data/pixel-native/preparation/ingress-refresh-0123456789abcdef0123456789abcdef/lifecycle.compose.json" ;;
         missing-recipe) recipe='' ;;
         *) recipe="$INSTALL_DIR/docker-compose.base.yml" ;;
     esac
     printf 'ods-searxng-valkey\tsearxng-valkey\t%s' "$INSTALL_DIR"
     [[ "$*" != *com.docker.compose.project.config_files* ]] || printf '\t%s' "$recipe"
     printf '\n' ;;
 other-project) printf 'foreign-project-searxng-valkey-1\tsearxng-valkey\t/srv/other\n' ;;
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
for scenario in failure partial running companion legacy frozen missing-recipe other-project stopped unrelated cancel; do
    fixture="$TMP/$scenario"
    mkdir -p "$fixture/data/searxng"
    if [[ "$scenario" == legacy ]]; then
        touch "$fixture/docker-compose.yml"
    else
        touch "$fixture/docker-compose.base.yml"
    fi
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
        companion|legacy|frozen|missing-recipe)
            # A database or cache from the same fragment still writes the data.
            [[ "$rc" != 0 && -f "$fixture/data/searxng/sentinel" && "$output" == *'searxng-valkey service is still running'* ]] || { echo "FAIL: running companion was not refused: $output"; exit 1; } ;;
        stopped|unrelated|other-project)
            # Another installation's companion with the same service label
            # does not block this installation's purge.
            [[ "$rc" == 0 && ! -e "$fixture/data/searxng" ]] || { echo "FAIL: stopped service purge failed: $output"; exit 1; } ;;
        cancel)
            [[ "$rc" == 0 && -f "$fixture/data/searxng/sentinel" ]] || { echo 'FAIL: cancellation changed data'; exit 1; } ;;
    esac
    [[ $(cat "$TMP/foreign/data/searxng/sentinel") == foreign ]] || { echo 'FAIL: foreign installation changed'; exit 1; }
    [[ $(wc -l < "$CAPTURE") == 1 ]] || { echo 'FAIL: unexpected Docker calls'; exit 1; }
    echo "PASS: public purge $scenario preserves the Docker inspection contract"
done
