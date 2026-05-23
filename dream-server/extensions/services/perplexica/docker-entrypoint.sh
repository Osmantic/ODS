#!/bin/sh
# DreamServer Perplexica Entrypoint
#
# Perplexica's bundled action orchestrator can let local models call
# scrape_url even when the prompt says not to. The upstream tool then sends
# full-page markdown into the final synthesis prompt, which can exceed smaller
# fleet context windows and produce empty answers. Patch the pinned bundle at
# container startup so scrape_url keeps at most N characters per URL.

set -eu

log() {
    echo "[dream-perplexica] $*" >&2
}

SCRAPE_MAX_CHARS="${PERPLEXICA_SCRAPE_URL_MAX_CHARS:-30000}"
case "$SCRAPE_MAX_CHARS" in
    ''|*[!0-9]*)
        log "Invalid PERPLEXICA_SCRAPE_URL_MAX_CHARS='$SCRAPE_MAX_CHARS'; using 30000"
        SCRAPE_MAX_CHARS=30000
        ;;
esac

if [ "$SCRAPE_MAX_CHARS" -lt 1000 ]; then
    log "PERPLEXICA_SCRAPE_URL_MAX_CHARS is too small; using 30000"
    SCRAPE_MAX_CHARS=30000
fi

patch_scrape_url() {
    search_root="/home/perplexica/.next/server"

    if [ ! -d "$search_root" ]; then
        log "Perplexica server bundle not found at $search_root; skipping scrape_url patch"
        return 0
    fi

    files_list="${TMPDIR:-/tmp}/dream-perplexica-scrape-files.$$"
    grep -Rsl 'name:"scrape_url"' "$search_root" > "$files_list" 2>/dev/null || true
    if [ ! -s "$files_list" ]; then
        rm -f "$files_list"
        log "scrape_url tool not found in bundled server JS; no patch needed"
        return 0
    fi

    patched=0
    inspected=0
    while IFS= read -r file; do
        inspected=$((inspected + 1))
        if node - "$file" "$SCRAPE_MAX_CHARS" <<'NODE'
const fs = require("fs");

const [file, maxRaw] = process.argv.slice(2);
const max = Number.parseInt(maxRaw, 10);
const source = fs.readFileSync(file, "utf8");

if (source.includes(`content:k.slice(0,${max})`)) {
  process.exit(0);
}

const pattern = /([A-Za-z_$][\w$]*\.push\(\{content:)([A-Za-z_$][\w$]*)(,metadata:\{url:[A-Za-z_$][\w$]*,title:[A-Za-z_$][\w$]*\}\}\))/g;
let replacements = 0;
const patched = source.replace(pattern, (match, prefix, contentVar, suffix) => {
  replacements += 1;
  return `${prefix}${contentVar}.slice(0,${max})${suffix}`;
});

if (replacements === 0) {
  process.exit(2);
}

fs.writeFileSync(file, patched);
NODE
        then
            patched=$((patched + 1))
            log "scrape_url output cap active in ${file} (${SCRAPE_MAX_CHARS} chars per URL)"
        else
            log "ERROR: found scrape_url in ${file}, but could not patch its result push site"
            rm -f "$files_list"
            return 1
        fi
    done < "$files_list"
    rm -f "$files_list"

    if [ "$patched" -eq 0 ] && [ "$inspected" -gt 0 ]; then
        log "ERROR: inspected scrape_url bundle files but did not patch any"
        return 1
    fi
}

patch_scrape_url

# When compose overrides `entrypoint:`, Docker drops the image's CMD
# (`node server.js`), so $@ arrives empty. Fall back to the image's default
# command so the upstream docker-entrypoint.sh has something to exec.
if [ "$#" -eq 0 ]; then
    set -- node server.js
fi

exec docker-entrypoint.sh "$@"
