#!/usr/bin/env bash
# Exercise the public import command with real tar payloads, and failures at
# extraction/publication boundaries. No installed tree or service is used.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CLI=${ODS_CLI_UNDER_TEST:-$ROOT/ods-cli}
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/foreign/presets/shared" "$TMP/archive/shared"
printf 'foreign\n' > "$TMP/foreign/presets/shared/env"
printf 'name=shared\n' > "$TMP/archive/shared/meta.txt"
printf 'enabled:example\n' > "$TMP/archive/shared/extensions.list"
printf 'replacement\n' > "$TMP/archive/shared/env"
tar czf "$TMP/valid.tar.gz" -C "$TMP/archive" shared
rm "$TMP/archive/shared/extensions.list"
tar czf "$TMP/missing-extensions.tar.gz" -C "$TMP/archive" shared
rm "$TMP/archive/shared/meta.txt"
tar czf "$TMP/missing-meta.tar.gz" -C "$TMP/archive" shared
PRESET_REAL_TAR=$(command -v tar)
PRESET_REAL_PYTHON=$(command -v python3)
PRESET_REAL_RM=$(command -v rm)
export PRESET_REAL_TAR PRESET_REAL_PYTHON PRESET_REAL_RM
export PRESET_RUNTIME_MARKER="$TMP/runtime-unavailable"
export PRESET_FOREIGN_PRESET="$TMP/foreign/presets/shared"
export PRESET_COLLISION_HOOK="$TMP/collision-hook"
mkdir "$PRESET_COLLISION_HOOK"
cat > "$PRESET_COLLISION_HOOK/sitecustomize.py" <<'PY'
# Simulate a concurrent writer after the existence check. The actual rename
# syscall remains unpatched and must refuse the now nonempty destination.
import os

original_lexists = os.path.lexists


def collision_after_check(path):
    result = original_lexists(path)
    if path == os.environ.get("PRESET_COLLISION_TARGET") and not result:
        os.mkdir(path)
        with open(os.path.join(path, "env"), "w") as stream:
            stream.write("foreign")
    return result


os.path.lexists = collision_after_check
PY
cat > "$TMP/bin/tar" <<'EOF'
#!/usr/bin/env bash
if [[ "${PRESET_FAILURE:-}" == runtime && "$1" == tzf ]]; then touch "$PRESET_RUNTIME_MARKER"; fi
if [[ "$1" == xzf && "${3:-}" == -C ]]; then
    stage=$(dirname "$4")
    mode=$(stat -c '%a' "$stage" 2>/dev/null || stat -f '%Lp' "$stage")
    [[ "$mode" == 700 ]] || exit 98
fi
if [[ "${PRESET_FAILURE:-}" == extract && "$1" == xzf ]]; then
    "$PRESET_REAL_TAR" "$@"
    exit 23
fi
exec "$PRESET_REAL_TAR" "$@"
EOF
cat > "$TMP/bin/python3" <<'EOF'
#!/usr/bin/env bash
if [[ "${PRESET_FAILURE:-}" == runtime && -e "$PRESET_RUNTIME_MARKER" ]]; then exit 127; fi
# Only the actual directory rename boundary has '-' plus source/destination.
# Registry and other Python invocations pass through unchanged.
if [[ "${1:-}" != - || $# != 3 ]]; then exec "$PRESET_REAL_PYTHON" "$@"; fi
case "${PRESET_FAILURE:-}" in
    park) [[ "$3" != */previous ]] || exit 24 ;;
    publish|rollback-failure) [[ "$2" != */payload/shared ]] || exit 24 ;;
    term) if [[ "$2" == */payload/shared ]]; then kill -TERM "$PPID"; exit 24; fi ;;
    term-after-park)
        if [[ "$3" == */previous ]]; then
            "$PRESET_REAL_PYTHON" "$@" || exit "$?"
            kill -TERM "$PPID"
            exit 24
        fi ;;
    foreign-destination|foreign-empty|foreign-link)
        if [[ "$2" == */payload/shared ]]; then
            if [[ "$PRESET_FAILURE" == foreign-link ]]; then
                ln -s "$PRESET_FOREIGN_PRESET" "$3"
            else
                mkdir "$3"
                [[ "$PRESET_FAILURE" == foreign-empty ]] || printf foreign > "$3/env"
            fi
            # Run the real rename against the competing directory; a synthetic
            # failure here would miss mv's successful directory-nesting bug.
            exec "$PRESET_REAL_PYTHON" "$@"
        fi ;;
    foreign-race)
        if [[ "$2" == */payload/shared ]]; then
            export PYTHONPATH="$PRESET_COLLISION_HOOK${PYTHONPATH:+:$PYTHONPATH}"
            export PRESET_COLLISION_TARGET="$3"
            exec "$PRESET_REAL_PYTHON" "$@"
        fi ;;
    term-after-publish)
        if [[ "$2" == */payload/shared ]]; then
            "$PRESET_REAL_PYTHON" "$@" || exit "$?"
            kill -TERM "$PPID"
            exit 24
        fi ;;
esac
if [[ "${PRESET_FAILURE:-}" == rollback-failure && "$2" == */previous ]]; then exit 25; fi
exec "$PRESET_REAL_PYTHON" "$@"
EOF
cat > "$TMP/bin/rm" <<'EOF'
#!/usr/bin/env bash
if [[ "${PRESET_FAILURE:-}" == cleanup && "${3:-}" == */.import-* ]]; then exit 26; fi
if [[ "${PRESET_FAILURE:-}" == term-during-cleanup && "${3:-}" == */.import-* ]]; then
    kill -TERM "$PPID"
    exit 26
fi
exec "$PRESET_REAL_RM" "$@"
EOF
for command in docker sudo systemctl service launchctl curl powershell.exe; do
    printf '#!/bin/sh\necho "Unexpected host command: $0" >&2\nexit 99\n' > "$TMP/bin/$command"
done
chmod +x "$TMP/bin/"*
export PATH="$TMP/bin:$PATH"

for scenario in missing-extensions missing-meta runtime extract park publish term term-after-park term-after-publish foreign-destination foreign-empty foreign-link foreign-race rollback-failure cleanup term-during-cleanup cancel success fresh; do
    install="$TMP/$scenario"
    mkdir -p "$install/lib" "$install/extensions/services" "$install/presets/unrelated"
    cp "$ROOT"/lib/*.sh "$install/lib/"
    touch "$install/docker-compose.base.yml"
    printf 'active settings\n' > "$install/.env"
    printf 'unrelated\n' > "$install/presets/unrelated/env"
    if [[ "$scenario" != fresh ]]; then
        mkdir "$install/presets/shared"
        printf 'old metadata\n' > "$install/presets/shared/meta.txt"
        printf 'old selection\n' > "$install/presets/shared/extensions.list"
        printf 'original\n' > "$install/presets/shared/env"
    fi
    archive="$TMP/valid.tar.gz" reply=y PRESET_FAILURE=""
    case "$scenario" in
        missing-*) archive="$TMP/$scenario.tar.gz" ;;
        runtime|extract|park|publish|term|term-after-park|term-after-publish|foreign-*|rollback-failure|cleanup|term-during-cleanup) PRESET_FAILURE="$scenario" ;;
        cancel) reply=n ;;
    esac
    export INSTALL_DIR="$install" ODS_HOME="$install" ODS_INSTALL_DIR="$TMP/foreign" PRESET_FAILURE
    rc=0
    output=$(printf '%s\n' "$reply" | bash "$CLI" preset import "$archive" 2>&1) || rc=$?
    case "$scenario" in
        missing-*|runtime|extract|park|publish|term|term-after-park)
            [[ "$rc" != 0 && $(cat "$install/presets/shared/env") == original ]] || {
                echo "FAIL: $scenario lost the previous preset: $output"; exit 1;
            }
            [[ $(cat "$install/presets/shared/meta.txt") == 'old metadata' &&
               $(cat "$install/presets/shared/extensions.list") == 'old selection' ]] || exit 1
            [[ "$output" != *'imported successfully'* ]] || exit 1 ;;
        rollback-failure|term-after-publish|foreign-*)
            recovery=$(find "$install/presets" -maxdepth 2 -path '*/.import-*/previous' -print)
            [[ "$rc" != 0 && -n "$recovery" && $(cat "$recovery/env") == original &&
               "$output" == *"Recovery copy retained at: $recovery"* ]] || {
                echo "FAIL: rollback failure discarded recovery or its location: $output"; exit 1;
            }
            if [[ "$scenario" == term-after-publish ]]; then
                [[ $(cat "$install/presets/shared/env") == replacement && ! -e "$install/presets/shared/previous" ]] || exit 1
            elif [[ "$scenario" == foreign-destination || "$scenario" == foreign-race ]]; then
                [[ $(cat "$install/presets/shared/env") == foreign && ! -e "$install/presets/shared/previous" ]] || exit 1
            elif [[ "$scenario" == foreign-empty ]]; then
                [[ -d "$install/presets/shared" && -z $(find "$install/presets/shared" -mindepth 1 -print) ]] || exit 1
            elif [[ "$scenario" == foreign-link ]]; then
                [[ -L "$install/presets/shared" && $(readlink "$install/presets/shared") == "$PRESET_FOREIGN_PRESET" ]] || exit 1
            fi ;;
        cancel)
            [[ "$rc" == 0 && $(cat "$install/presets/shared/env") == original && "$output" == *Cancelled* ]] || exit 1 ;;
        term-during-cleanup)
            [[ "$rc" != 0 && $(cat "$install/presets/shared/env") == replacement ]] || exit 1 ;;
        success|fresh|cleanup)
            [[ "$rc" == 0 && $(cat "$install/presets/shared/env") == replacement && "$output" == *'imported successfully'* ]] || {
                echo "FAIL: $scenario import failed: $output"; exit 1;
            }
            [[ $(cat "$install/presets/shared/extensions.list") == enabled:example ]] || exit 1 ;;
    esac
    [[ "$scenario" != runtime || "$output" == *'requires a working Python 3 runtime'* ]] || exit 1
    [[ $(cat "$install/.env") == 'active settings' && $(cat "$install/presets/unrelated/env") == unrelated &&
       $(cat "$TMP/foreign/presets/shared/env") == foreign ]] || exit 1
    if [[ "$scenario" == cleanup || "$scenario" == term-during-cleanup ]]; then
        retained=$(find "$install/presets" -maxdepth 1 -name '.import-*' -print)
        [[ -n "$retained" && "$output" == *"Could not remove temporary preset import files. Retained at: $retained"* ]] || exit 1
    fi
    [[ "$scenario" == rollback-failure || "$scenario" == term-after-publish || "$scenario" == foreign-* || "$scenario" == cleanup || "$scenario" == term-during-cleanup ||
       -z $(find "$install/presets" -maxdepth 1 -name '.import-*' -print) ]] || {
        echo "FAIL: $scenario left staging behind"; exit 1;
    }
    echo "PASS: public preset import $scenario preserves recovery and unrelated state"
done
