#!/bin/sh
# ODS Perplexica Entrypoint
#
# Perplexica's (Vane's) researcher offers its model a scrape_url action in
# every mode. The action opens whatever URLs the model names, with no address
# validation, from this container on the ODS network: in Vane 1.12.2 through
# a headless Chromium (on images that ship one), before that with fetch().
# Any caller of the unauthenticated /api/search, or text in a search result,
# can steer the model to an internal address. ODS disables the action at
# container startup: the pinned bundle's scrape_url reports itself disabled,
# so the researcher never offers it, and its execute opens no URL even if a
# model names it anyway. Search-result pages that Quality mode reads itself
# are not affected.
#
# The patch also keeps the older output cap (at most N characters per URL),
# which applied when scrape_url could run.

set -eu

log() {
    echo "[ods-perplexica] $*" >&2
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

# Upstream renamed Perplexica to Vane in v1.12.2 and moved the app from
# /home/perplexica to /home/vane. Prefer the image's working directory, then
# both known layouts, so operator image overrides of either line still patch.
find_server_bundle() {
    for app_root in "$PWD" /home/vane /home/perplexica; do
        if [ -d "$app_root/.next/server" ]; then
            printf '%s\n' "$app_root/.next/server"
            return 0
        fi
    done
    return 1
}

patch_scrape_url() {
    if ! search_root="$(find_server_bundle)"; then
        log "Perplexica server bundle not found under $PWD, /home/vane or /home/perplexica; skipping scrape_url patch"
        return 0
    fi

    files_list="${TMPDIR:-/tmp}/ods-perplexica-scrape-files.$$"
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
let text = fs.readFileSync(file, "utf8");
const source = text;
const id = "[A-Za-z_$][\\w$]*";

// 1. Disable scrape_url. Vane's action object (researcher/actions/scrapeURL.ts)
// minifies to {name:"scrape_url",schema:...,getToolDescription:...,
// getDescription:...,enabled:a=>!0,execute:async(a,b)=>{a.urls=a.urls.slice(0,3);...}.
// ActionRegistry offers only actions whose enabled() is true, but executes any
// registered action a model names (registry.ts executeAll), so both change:
// enabled:a=>!1, and execute starts with a.urls=[] and opens nothing.
// Minified names differ per build; a restarted container keeps its patched
// bundle, which the pattern no longer matches.
const action = new RegExp(`(name:"scrape_url",[^{}]{0,2000}?enabled:${id}=>)!0(,execute:async\\((${id}),${id}\\)=>\\{)(${id})\\.urls=\\4\\.urls\\.slice\\(0,3\\);`, "g");
text = text.replace(action, (match, head, execute, param, target) =>
  param === target ? `${head}!1${execute}${param}.urls=[];` : match);
const off = new RegExp(`name:"scrape_url",[^{}]{0,2000}?enabled:${id}=>!1,execute:async\\((${id}),${id}\\)=>\\{\\1\\.urls=\\[\\];`, "g");
const actions = text.split('name:"scrape_url"').length - 1;
if (actions === 0 || (text.match(off) || []).length !== actions) {
  process.exit(3);
}

// 2. Cap the text each scraped URL contributes. A restarted container keeps
// its patched bundle, so recognize any previously capped push site.
if (!/\.push\(\{content:[A-Za-z_$][\w$]*\.slice\(0,\d+\),metadata:\{url:/.test(text)) {
  // Perplexica <=1.12.1 pushes `title:j`; Vane 1.12.2 pushes `title:k.title`.
  const pattern = /([A-Za-z_$][\w$]*\.push\(\{content:)([A-Za-z_$][\w$]*)(,metadata:\{url:[A-Za-z_$][\w$]*,title:[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\}\}\))/g;
  let replacements = 0;
  text = text.replace(pattern, (match, prefix, contentVar, suffix) => {
    replacements += 1;
    return `${prefix}${contentVar}.slice(0,${max})${suffix}`;
  });
  if (replacements === 0) {
    process.exit(2);
  }
}

if (text !== source) {
  fs.writeFileSync(file, text);
}
NODE
        then
            patched=$((patched + 1))
            log "scrape_url disabled in ${file} (output cap ${SCRAPE_MAX_CHARS} chars per URL)"
        else
            status=$?
            if [ "$status" -eq 3 ]; then
                log "ERROR: found scrape_url in ${file}, but could not disable it"
            else
                log "ERROR: found scrape_url in ${file}, but could not patch its result push site"
            fi
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

sync_model_route() {
    attempts="${PERPLEXICA_MODEL_SYNC_ATTEMPTS:-30}"
    delay="${PERPLEXICA_MODEL_SYNC_DELAY_SECONDS:-2}"
    case "$attempts:$delay" in
        *[!0-9:]*|:*|*:)
            attempts=30
            delay=2
            ;;
    esac

    (
        attempt=1
        last_error=""
        while [ "$attempt" -le "$attempts" ]; do
            if output=$(node /app/ods-sync-model-config.js 2>&1); then
                if [ -n "$output" ]; then
                    log "Active model route synchronized: $output"
                fi
                exit 0
            fi
            last_error="$output"
            attempt=$((attempt + 1))
            [ "$attempt" -le "$attempts" ] && sleep "$delay"
        done
        log "WARNING: model-route synchronization did not complete: $last_error"
    ) &
}

sync_search_route() {
    [ -n "${PERPLEXICA_SEARXNG_API_URL:-}" ] || return 0
    attempts="${PERPLEXICA_SEARCH_SYNC_ATTEMPTS:-30}"
    delay="${PERPLEXICA_SEARCH_SYNC_DELAY_SECONDS:-2}"
    case "$attempts:$delay" in
        *[!0-9:]*|:*|*:)
            attempts=30
            delay=2
            ;;
    esac

    (
        attempt=1
        last_error=""
        while [ "$attempt" -le "$attempts" ]; do
            if output=$(node /app/ods-sync-search-config.js 2>&1); then
                log "Search route synchronized: $output"
                exit 0
            fi
            last_error="$output"
            attempt=$((attempt + 1))
            [ "$attempt" -le "$attempts" ] && sleep "$delay"
        done
        log "WARNING: search-route synchronization did not complete: $last_error"
    ) &
}

sync_model_route
sync_search_route

# When compose overrides `entrypoint:`, Docker drops the image's CMD
# (`node server.js`), so $@ arrives empty. Fall back to the image's default
# command so the upstream docker-entrypoint.sh has something to exec.
if [ "$#" -eq 0 ]; then
    set -- node server.js
fi

exec docker-entrypoint.sh "$@"
