#!/usr/bin/env bash
# Exercise complete reset invocations without touching host locks or SSH hosts.
set -euo pipefail

if [[ "${1:-}" != --private-tmp ]]; then
    namespace=(--mount --fork)
    if [[ "$EUID" != 0 ]]; then namespace+=(--user --map-root-user); fi
    # Expand the positional parameter in the private namespace's shell.
    # shellcheck disable=SC2016
    exec unshare "${namespace[@]}" bash -c \
        'set -euo pipefail; mount --make-rprivate /; mount -t tmpfs tmpfs /tmp; exec bash "$1" --private-tmp' \
        bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
fi

project="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
mkdir "$test_root/bin"
cat > "$test_root/bin/scp" <<'SCP'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == -q ]]
shift
if [[ "$1" == fixture@fixture.invalid:/fixture/MEMORY.md ]]; then
    printf 'read\n' >> "$ODS_TEST_CALLS"
    case "$ODS_TEST_READ" in
        fail) printf 'fixture: read unavailable\n' >&2; exit 73 ;;
        partial) printf 'incomplete copy' > "$2"; exit 74 ;;
        missing) printf 'fixture: no such file\n' >&2; exit 75 ;;
        success) cp "$ODS_TEST_MEMORY" "$2" ;;
        *) exit 91 ;;
    esac
elif [[ "$2" == fixture@fixture.invalid:/fixture/MEMORY.md ]]; then
    printf 'write\n' >> "$ODS_TEST_CALLS"
    cp "$1" "$ODS_TEST_MEMORY"
else
    exit 92
fi
SCP
chmod +x "$test_root/bin/scp"

failures=0
for scenario in fail partial missing success full-backup local-missing; do
    fixture="$test_root/$scenario"
    mkdir -p "$fixture/baselines" "$fixture/archives"
    for _ in {1..40}; do printf 'Durable baseline instruction.\n'; done > "$fixture/baselines/agent.md"
    printf '# Existing memory\n---\nDo not lose these notes.\n' > "$fixture/MEMORY.md"
    if [[ "$scenario" == full-backup ]]; then printf 'Complete memory without separator\n' > "$fixture/MEMORY.md"; fi
    cp "$fixture/MEMORY.md" "$fixture/original.md"
    if [[ "$scenario" == missing || "$scenario" == local-missing ]]; then rm "$fixture/MEMORY.md"; fi
    : > "$fixture/calls"
    {
        printf '[general]\nbaseline_dir=%s/baselines\narchive_dir=%s/archives\n[fixture]\nbaseline=agent.md\n' "$fixture" "$fixture"
        if [[ "$scenario" == local-missing ]]; then
            printf 'memory_file=%s/MEMORY.md\n' "$fixture"
        else
            printf 'remote_host=fixture.invalid\nremote_user=fixture\nremote_memory=/fixture/MEMORY.md\n'
        fi
    } > "$fixture/config"
    read_mode="$scenario"
    [[ "$scenario" == full-backup || "$scenario" == local-missing ]] && read_mode=success
    code=0
    PATH="$test_root/bin:$PATH" MEMORY_SHEPHERD_CONF="$fixture/config" \
        ODS_TEST_MEMORY="$fixture/MEMORY.md" ODS_TEST_CALLS="$fixture/calls" ODS_TEST_READ="$read_mode" \
        bash "$project/memory-shepherd/memory-shepherd.sh" fixture > "$fixture/output" 2>&1 || code=$?
    if [[ "$scenario" == fail || "$scenario" == partial || "$scenario" == missing ]]; then
        if [[ "$code" == 0 ]] || [[ "$(cat "$fixture/calls")" != read ]]; then
            printf 'FAIL %s: unsuccessful fetch must not upload or report success\n' "$scenario" >&2
            cat "$fixture/output" >&2
            failures=$((failures + 1))
            continue
        fi
        if [[ "$scenario" == missing ]]; then
            [[ ! -e "$fixture/MEMORY.md" ]]
        else
            cmp "$fixture/MEMORY.md" "$fixture/original.md"
        fi
        [[ -z "$(find "$fixture/archives" -type f -print)" ]]
    else
        [[ "$code" == 0 ]]
        cmp "$fixture/MEMORY.md" "$fixture/baselines/agent.md"
        if [[ "$scenario" != local-missing ]]; then
            [[ "$(cat "$fixture/calls")" == $'read\nwrite' ]]
            archive="$(find "$fixture/archives" -type f -name '*.md' -print)"
            [[ -n "$archive" ]]
            if [[ "$scenario" == full-backup ]]; then cmp "$archive" "$fixture/original.md"
            else grep -Fq 'Do not lose these notes.' "$archive"; fi
        else
            [[ ! -s "$fixture/calls" ]]
        fi
    fi
    printf 'PASS %s\n' "$scenario"
done
[[ "$failures" == 0 ]]
