#!/usr/bin/env bash
# A Linux installer rerun keeps an owner-edited config/searxng/settings.yml
# (the SearXNG README tells owners to edit it), as the macOS installer does;
# --force still regenerates it. Runs phase 06's own SearXNG section.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT/installers/phases/06-directories.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

section="$(awk '
    /_phase06_step "generate-searxng-config"/ { on = 1 }
    closing { if ($0 ~ /^    fi$/) print; exit }
    on { print }
    on && /unset _searxng_lang/ { closing = 1 }
' "$PHASE")"
[[ "$section" == *'cat > "$INSTALL_DIR/config/searxng/settings.yml"'* ]] \
    || fail "could not find phase 06's SearXNG section"

run_section() {
    INSTALL_DIR="$tmp/install" SEARXNG_SECRET="fresh-secret" FORCE="$1" LC_ALL=en_US.UTF-8 bash -c '
        set -euo pipefail
        # shellcheck source=../installers/lib/searxng-locale.sh
        source "$2"
        _phase06_step() { :; }
        _phase06_repair_host_path() { return 1; }
        ai_ok() { echo "ok: $*"; }
        eval "$1"
    ' _ "$section" "$ROOT/installers/lib/searxng-locale.sh"
}

settings="$tmp/install/config/searxng/settings.yml"

run_section false > "$tmp/first.log"
grep -Fq 'secret_key: "fresh-secret"' "$settings" || fail "first run did not generate settings.yml"
pass "a first run generates settings.yml"

printf '%s\n' 'use_default_settings: true' 'server:' '  secret_key: "owner-secret"' \
    'engines:' '  - name: google' '    disabled: true # OWNER' > "$settings"
cp "$settings" "$tmp/owner.yml"
run_section false > "$tmp/rerun.log"
cmp -s "$settings" "$tmp/owner.yml" || fail "a rerun replaced the owner's settings.yml: $(cat "$settings")"
grep -Fq "Preserved existing SearXNG config" "$tmp/rerun.log" || fail "rerun did not say it kept the file"
pass "a rerun keeps the owner's settings.yml"

run_section true > "$tmp/force.log"
grep -Fq 'secret_key: "fresh-secret"' "$settings" || fail "--force did not regenerate settings.yml"
pass "--force regenerates settings.yml"
