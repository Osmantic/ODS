#!/usr/bin/env bash
# Public script fixtures; only the unrelated host lock is relocated in the copy.
set -euo pipefail
project="${ODS_TEST_PROJECT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/bin" "$root/scripts" "$root/home/baselines" "$root/home/memory"
sed "s|^LOCKFILE=/tmp/memory-shepherd.lock$|LOCKFILE=$root/reset.lock|" \
    "$project/memory-shepherd/memory-shepherd.sh" |
    sed "s|/tmp/memory-shepherd-\${agent}-current.md|$root/legacy-current.md|" > "$root/scripts/memory-shepherd.sh"
cp "$project/memory-shepherd/install.sh" "$root/scripts/install.sh"
for _ in {1..40}; do printf 'Durable baseline instructions.\n'; done > "$root/home/baselines/agent.md"
printf '# Current\n---\nPrivate scratch notes.\n' > "$root/home/memory/MEMORY.md"
cat > "$root/local.conf" <<'CONF'
[general]
baseline_dir=~/baselines
archive_dir=~/archives
[fixture]
baseline=agent.md
memory_file=~/memory/MEMORY.md
CONF
if [[ "${ODS_TEST_REMOTE_ONLY:-0}" != 1 ]]; then
HOME="$root/home" MEMORY_SHEPHERD_CONF="$root/local.conf" \
    bash "$root/scripts/memory-shepherd.sh" fixture > "$root/local.log" 2>&1
cmp "$root/home/baselines/agent.md" "$root/home/memory/MEMORY.md"
grep -F 'Private scratch notes.' "$root/home/archives/fixture/"*.md >/dev/null
HOME="$root/home" MEMORY_SHEPHERD_CONF="$root/local.conf" \
    bash "$root/scripts/install.sh" --dry-run --prefix "$root/units" > "$root/install.log"
grep -F "Baselines:     $root/home/baselines" "$root/install.log" >/dev/null
grep -F "Archives:      $root/home/archives" "$root/install.log" >/dev/null
echo 'PASS configured home paths: installer and archived reset'
fi

cat > "$root/bin/scp" <<'SCP'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == -q ]]; shift
while [[ "${1:-}" == -o ]]; do printf '%s\n' "$2" >> "$FIXTURE/opts"; shift 2; done
if [[ "$1" == fixture@fixture.invalid:\~/MEMORY.md ]]; then
    printf 'read\n' >> "$FIXTURE/calls"
    printf '%s\n' "$2" > "$FIXTURE/fetch-path"
    case "$SCENARIO" in
        fetch-fail) printf 'partial private memory' > "$2"; exit 73 ;;
        timeout-zero) sleep 2; cp "$FIXTURE/original" "$2" ;;
        fetch-hang) sleep 30 ;;
        fetch-ignore-term) trap '' TERM; sleep 30 ;;
        *) cp "$FIXTURE/original" "$2" ;;
    esac
else
    printf 'write\n' >> "$FIXTURE/calls"
    case "$SCENARIO" in
        push-fail) exit 74 ;;
        push-hang) sleep 30 ;;
        cancel) kill -TERM "$PPID"; sleep 1; exit 75 ;;
        *) cp "$1" "$FIXTURE/remote" ;;
    esac
fi
SCP
chmod +x "$root/bin/scp"
for scenario in ${ODS_TEST_SCENARIOS:-success timeout-zero fetch-fail fetch-hang fetch-ignore-term archive-fail push-fail push-hang cancel}; do
    fixture="$root/$scenario"
    mkdir -p "$fixture/tmp" "$fixture/archive"
    printf '# Current\n---\nPrivate remote scratch.\n' > "$fixture/original"
    cp "$fixture/original" "$fixture/remote"
    : > "$fixture/calls"; : > "$fixture/opts"
    printf 'sentinel must survive\n' > "$fixture/sentinel"
    case "$(uname -s)" in
        MINGW*|MSYS*) cp "$fixture/sentinel" "$root/legacy-current.md" ;;
        *) ln -s "$fixture/sentinel" "$root/legacy-current.md" ;;
    esac
    if [[ "$scenario" == archive-fail ]]; then
        rmdir "$fixture/archive"; printf 'occupied' > "$fixture/archive"
    fi
    remote_timeout=1
    [[ "$scenario" == timeout-zero ]] && remote_timeout=0
    cat > "$fixture/config" <<CONF
[general]
baseline_dir=$root/home/baselines
archive_dir=$fixture/archive
remote_scp_timeout=$remote_timeout
[fixture]
baseline=agent.md
remote_host=fixture.invalid
remote_user=fixture
remote_memory=~/MEMORY.md
CONF
    code=0
    start=$SECONDS
    PATH="$root/bin:$PATH" HOME="$root/home" TMPDIR="$fixture/tmp" \
        MEMORY_SHEPHERD_CONF="$fixture/config" FIXTURE="$fixture" SCENARIO="$scenario" \
        timeout --kill-after=2s 12s bash "$root/scripts/memory-shepherd.sh" fixture > "$fixture/output" 2>&1 || code=$?
    [[ $((SECONDS - start)) -lt 15 ]]
    fetch_path=$(cat "$fixture/fetch-path")
    [[ "$fetch_path" == "$fixture/tmp/memory-shepherd-fixture."* ]] || {
        printf 'FAIL remote %s: predictable download path %s\n' "$scenario" "$fetch_path" >&2
        exit 1
    }
    [[ ! -e "$fetch_path" ]] || {
        printf 'FAIL remote %s: scratch survives transfer deadline\n' "$scenario" >&2
        exit 1
    }
    [[ "$(cat "$fixture/sentinel")" == 'sentinel must survive' ]]
    [[ "$(cat "$root/legacy-current.md")" == 'sentinel must survive' ]]
    rm "$root/legacy-current.md"
    grep -qx 'BatchMode=yes' "$fixture/opts"
    grep -qx 'ConnectTimeout=15' "$fixture/opts"
    if [[ "$scenario" == success || "$scenario" == timeout-zero ]]; then
        [[ "$code" == 0 ]]
        cmp "$root/home/baselines/agent.md" "$fixture/remote"
    else
        [[ "$code" != 0 ]]
        cmp "$fixture/original" "$fixture/remote"
        if grep -F 'Reset fixture MEMORY.md on' "$fixture/output"; then exit 1; fi
    fi
    if [[ "$scenario" == fetch-* || "$scenario" == archive-fail ]]; then
        [[ "$(cat "$fixture/calls")" == read ]]
    else
        grep -F 'Private remote scratch.' "$fixture/archive/fixture/"*.md >/dev/null
    fi
    echo "PASS remote $scenario: private scratch cleanup and write boundary"
done
