#!/bin/bash
# ============================================================================
# ODS Installer — SearXNG search language
# ============================================================================
# Part of: installers/lib/
# Purpose: Pick SearXNG's search.default_lang from the install locale.
#
# Pixel, Hermes and Perplexica call SearXNG's JSON API without a language
# parameter or a browser Accept-Language header, so SearXNG's upstream "auto"
# default resolves their searches to "all". Writing the owner's locale makes
# engines that honour a language (Google, Brave, DuckDuckGo) return results for
# the owner's language and region.
#
# SearXNG refuses to start when default_lang is not one of its locale tags, so
# only tags from the list below are ever written; anything else becomes "en".
# The list is sxng_locales from the pinned image named below; recheck it when
# extensions/services/searxng/compose.yaml moves the pin. The Windows copy is
# $script:SearxngLocaleTags in installers/windows/lib/env-generator.ps1.
# ============================================================================

# searxng/searxng:2026.9.25-12f8b6515@sha256:5286edb35782454ab8a102c5eff6b54bff745853191b46aeead95f225aa6dfb6 searx/sxng_locales.py
ODS_SEARXNG_LOCALE_TAGS="af ar ar-SA bg bg-BG ca ca-ES cs cs-CZ cy da da-DK de de-AT de-BE de-CH de-DE el el-GR en en-AU en-CA en-GB en-HK en-IE en-IN en-NZ en-PH en-PK en-SG en-US en-ZA es es-AR es-CL es-CO es-ES es-MX es-PE es-VE et et-EE fa fi fi-FI fil fil-PH fr fr-BE fr-CA fr-CH fr-FR gl hi hi-IN hr hr-HR hu hu-HU id id-ID it it-CH it-IT ja ja-JP ko ko-KR lt lt-LT lv lv-LV mi mi-NZ nb nb-NO nl nl-BE nl-NL nn nn-NO pl pl-PL pt pt-BR pt-PT ro ro-RO ru ru-RU sk sk-SK sl sl-SI sq sv sv-FI sv-SE th th-TH tr tr-TR uk uk-UA vi vi-VN zh zh-CN zh-HK zh-TW"

# ods_searxng_default_lang [LOCALE]
#   Map a POSIX/BCP 47 locale (en_US.UTF-8, de-DE, zh-Hant-TW, sr_RS@latin)
#   to a SearXNG locale tag: language-REGION when SearXNG knows the pair, else
#   the language, else "en". Without an argument it reads LC_ALL, LC_MESSAGES
#   and LANG in that order. C, POSIX and empty locales give "en".
ods_searxng_default_lang() {
    local raw lang="" region="" part tag
    if (( $# )); then
        raw="$1"
    else
        raw="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}"
    fi
    raw="${raw%%.*}"
    raw="${raw%%@*}"
    raw="${raw//_/-}"
    local -a parts=()
    IFS=- read -r -a parts <<< "$raw" || true
    # ${parts[@]+...}: macOS bash 3.2 treats an empty array as unset under set -u.
    for part in ${parts[@]+"${parts[@]}"}; do
        if [[ -z "$lang" ]]; then
            lang="$(printf '%s' "$part" | tr '[:upper:]' '[:lower:]')"
        elif [[ -z "$region" && "$part" =~ ^[A-Za-z]{2}$ ]]; then
            region="$(printf '%s' "$part" | tr '[:lower:]' '[:upper:]')"
        fi
    done
    if [[ "$lang" =~ ^[a-z]{2,3}$ ]]; then
        for tag in ${region:+"$lang-$region"} "$lang"; do
            case " $ODS_SEARXNG_LOCALE_TAGS " in
                *" $tag "*) printf '%s\n' "$tag"; return 0 ;;
            esac
        done
    fi
    printf 'en\n'
}

# ods_searxng_hostnames_yaml TAG
#   Print the settings.yml "hostnames:" block for a SearXNG locale tag.
#   Seznam stays enabled as the general-web fallback for when major engines
#   refuse the household IP, but it is a Czech-market index: for a US query it
#   fills most of the page with .cz shops. Unless the owner's language is
#   Czech, drop .cz results so the fallback only adds pages in other markets.
ods_searxng_hostnames_yaml() {
    [[ "${1%%-*}" == cs ]] && return 0
    printf '%s\n' \
        'hostnames:' \
        '  # Seznam (below) is a Czech-market fallback; keep its .cz shops out of' \
        '  # results unless the install locale is Czech.' \
        '  remove:' \
        "    - '\\.cz\$'"
}
