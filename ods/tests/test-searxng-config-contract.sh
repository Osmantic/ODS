#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
  echo "$*" >&2
  exit 1
}

template="$ROOT/config/searxng/settings.yml"
linux_phase="$ROOT/installers/phases/06-directories.sh"
macos_generator="$ROOT/installers/macos/lib/env-generator.sh"
windows_generator="$ROOT/installers/windows/lib/env-generator.ps1"
locale_lib="$ROOT/installers/lib/searxng-locale.sh"

files=("$template" "$linux_phase" "$macos_generator" "$windows_generator")

for file in "${files[@]}"; do
  grep -A3 -F -- "- name: bing" "$file" | grep -Fq "disabled: true" || {
    echo "Unqualified Bing engine must be disabled in factory SearXNG configuration: $file" >&2
    exit 1
  }
  grep -A1 -F -- "- name: brave" "$file" | grep -Fq "disabled: false" || {
    echo "Brave must remain available in factory SearXNG configuration: $file" >&2
    exit 1
  }
  grep -A2 -F -- "- name: seznam" "$file" | grep -Fq "disabled: false" || {
    echo "Seznam general-web fallback must remain available in factory SearXNG configuration: $file" >&2
    exit 1
  }
  grep -Fq 'default_lang: "' "$file" \
    || fail "SearXNG search language must be set, not left to \"auto\" (\"all\" for API clients): $file"
done

grep -Fqx '  default_lang: "en"' "$template" \
  || fail "Factory SearXNG template must fall back to English"
grep -Fqx "    - '\\.cz\$'" "$template" \
  || fail "Factory SearXNG template must keep Seznam's .cz results out for its English fallback"

# ── Locale mapping ───────────────────────────────────────────────────────────
# shellcheck source=../installers/lib/searxng-locale.sh
source "$locale_lib"

expect_lang() {
  local got
  got="$(ods_searxng_default_lang "$1")"
  [[ "$got" == "$2" ]] || fail "ods_searxng_default_lang '$1' gave '$got', expected '$2'"
}

expect_lang "en_US.UTF-8" "en-US"
expect_lang "en_GB.utf8" "en-GB"
expect_lang "de_DE.UTF-8@euro" "de-DE"
expect_lang "de_LU.UTF-8" "de"
expect_lang "pt_BR" "pt-BR"
expect_lang "fr-CA" "fr-CA"
expect_lang "zh-Hant-TW" "zh-TW"
expect_lang "zh_CN.GB18030" "zh-CN"
expect_lang "cs_CZ.UTF-8" "cs-CZ"
expect_lang "nb_NO.UTF-8" "nb-NO"
expect_lang "en_150" "en"
expect_lang "sr_RS@latin" "en"
expect_lang "fil_PH.UTF-8" "en"
expect_lang "C.UTF-8" "en"
expect_lang "C" "en"
expect_lang "POSIX" "en"
expect_lang "" "en"
expect_lang "*" "en"
expect_lang "en US" "en"

# bash warns on stderr when a test locale is not installed on this machine;
# the helper itself never writes to stderr, so those warnings are dropped.
[[ "$(LC_ALL=fr_FR.UTF-8 LC_MESSAGES=it_IT.UTF-8 LANG=de_DE.UTF-8 ods_searxng_default_lang 2>/dev/null)" == "fr-FR" ]] \
  || fail "LC_ALL must win over LC_MESSAGES and LANG"
[[ "$(LC_ALL='' LC_MESSAGES=it_IT.UTF-8 LANG=de_DE.UTF-8 ods_searxng_default_lang 2>/dev/null)" == "it-IT" ]] \
  || fail "LC_MESSAGES must win over LANG"
[[ "$(LC_ALL='' LC_MESSAGES='' LANG=de_DE.UTF-8 ods_searxng_default_lang 2>/dev/null)" == "de-DE" ]] \
  || fail "LANG must be used when LC_ALL and LC_MESSAGES are empty"
[[ "$(LC_ALL='' LC_MESSAGES='' LANG='' ods_searxng_default_lang 2>/dev/null)" == "en" ]] \
  || fail "An empty locale must fall back to English"

# Every tag the helper can return must be one SearXNG accepts; the list is the
# pinned image's sxng_locales, so the pin in compose.yaml must match it.
pinned_image="$(sed -n 's/^[[:space:]]*image:[[:space:]]*\(searxng\/searxng:[^[:space:]]*\).*/\1/p' \
  "$ROOT/extensions/services/searxng/compose.yaml" | head -1)"
[[ -n "$pinned_image" ]] || fail "Could not read the pinned SearXNG image"
grep -Fq "# $pinned_image searx/sxng_locales.py" "$locale_lib" \
  || fail "SearXNG locale tags must be rechecked for $pinned_image (installers/lib/searxng-locale.sh)"
grep -Fq "# $pinned_image searx/sxng_locales.py" "$windows_generator" \
  || fail "SearXNG locale tags must be rechecked for $pinned_image (env-generator.ps1)"

windows_tags="$(sed -n 's/^[[:space:]]*\$tags = "\([^"]*\)" -split .*/\1/p' "$windows_generator")"
[[ "$windows_tags" == "$ODS_SEARXNG_LOCALE_TAGS" ]] \
  || fail "Windows and bash SearXNG locale tag lists differ"

# ── Seznam .cz filter follows the locale ────────────────────────────────────
[[ -z "$(ods_searxng_hostnames_yaml cs-CZ)" ]] || fail "Czech installs must keep Seznam's .cz results"
[[ -z "$(ods_searxng_hostnames_yaml cs)" ]] || fail "Czech installs must keep Seznam's .cz results"
ods_searxng_hostnames_yaml en-US | grep -Fqx "    - '\\.cz\$'" \
  || fail "Non-Czech installs must drop Seznam's .cz results"
ods_searxng_hostnames_yaml de-DE | grep -Fqx "hostnames:" \
  || fail "Non-Czech installs must drop Seznam's .cz results"

# ── Rendered generators ─────────────────────────────────────────────────────
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

grep -Fq '_searxng_lang="$(ods_searxng_default_lang)"' "$linux_phase" \
  || fail "Linux phase 06 must derive the SearXNG language from the install locale"
grep -Fq 'source "$SCRIPT_DIR/installers/lib/searxng-locale.sh"' "$linux_phase" \
  || fail "Linux phase 06 must load installers/lib/searxng-locale.sh"
linux_body="$(awk '
  /cat > "\$INSTALL_DIR\/config\/searxng\/settings.yml" << SEARXNG_EOF/ { body = 1; next }
  /^SEARXNG_EOF$/ { body = 0 }
  body
' "$linux_phase")"
[[ -n "$linux_body" ]] || fail "Could not find the Linux SearXNG settings heredoc"

render_linux() {
  # shellcheck disable=SC2034  # read by the eval'd heredoc
  local SEARXNG_SECRET=contract-secret _searxng_lang
  _searxng_lang="$(LANG="$1" LC_ALL='' LC_MESSAGES='' ods_searxng_default_lang 2>/dev/null)"
  eval "cat <<SEARXNG_EOF
$linux_body
SEARXNG_EOF"
}

render_macos() (
  # shellcheck source=../installers/macos/lib/env-generator.sh
  source "$macos_generator"
  mkdir -p "$tmp/macos-$1"
  LANG="$1" LC_ALL='' LC_MESSAGES='' generate_searxng_config "$tmp/macos-$1" contract-secret true 2>/dev/null
  cat "$tmp/macos-$1/config/searxng/settings.yml"
)

for locale in en_US.UTF-8 cs_CZ.UTF-8; do
  render_linux "$locale" > "$tmp/linux-$locale.yml"
  render_macos "$locale" > "$tmp/macos-$locale.yml"
  cmp -s "$tmp/linux-$locale.yml" "$tmp/macos-$locale.yml" \
    || { diff -u "$tmp/linux-$locale.yml" "$tmp/macos-$locale.yml" >&2 || true
         fail "Linux and macOS SearXNG settings differ for $locale"; }
done

grep -Fqx '  default_lang: "en-US"' "$tmp/linux-en_US.UTF-8.yml" \
  || fail "An en_US install must search in en-US"
grep -Fqx "    - '\\.cz\$'" "$tmp/linux-en_US.UTF-8.yml" \
  || fail "An en_US install must drop Seznam's .cz results"
grep -Fqx '  default_lang: "cs-CZ"' "$tmp/linux-cs_CZ.UTF-8.yml" \
  || fail "A cs_CZ install must search in cs-CZ"
if grep -Fq 'hostnames:' "$tmp/linux-cs_CZ.UTF-8.yml"; then
  fail "A cs_CZ install must keep .cz results"
fi

if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' >/dev/null 2>&1; then
  for rendered in "$tmp"/linux-*.yml "$template"; do
    python3 - "$rendered" <<'PY' || fail "SearXNG settings do not parse as expected: $rendered"
import re, sys, yaml
cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
lang = cfg["search"]["default_lang"]
engines = {e["name"]: e for e in cfg["engines"]}
assert engines["seznam"]["disabled"] is False
patterns = (cfg.get("hostnames") or {}).get("remove") or []
czech = lang.split("-")[0] == "cs"
assert czech == (not patterns), (lang, patterns)
for pattern in patterns:
    assert re.search(pattern, "www.alza.cz") and not re.search(pattern, "www.newegg.com")
PY
  done
else
  echo "SKIP: python3 with PyYAML not available; YAML parse check not run"
fi

# ── Windows generator (runs where PowerShell is installed) ─────────────────
powershell_bin=""
for candidate in pwsh powershell powershell.exe; do
  if command -v "$candidate" >/dev/null 2>&1; then
    powershell_bin="$candidate"
    break
  fi
done
if [[ -n "$powershell_bin" ]]; then
  windows_generator_native="$windows_generator"
  windows_tmp_native="$tmp"
  if command -v cygpath >/dev/null 2>&1; then
    windows_generator_native="$(cygpath -w "$windows_generator")"
    windows_tmp_native="$(cygpath -w "$tmp")"
  fi
  for locale in en-US cs-CZ; do
    WINDOWS_GENERATOR="$windows_generator_native" OUT_DIR="$windows_tmp_native\\windows-$locale" LOCALE_NAME="$locale" \
      "$powershell_bin" -NoProfile -NonInteractive -Command '
        $ErrorActionPreference = "Stop"
        function Write-Utf8NoBom { param([string]$Path, [string]$Content)
          [System.IO.File]::WriteAllText($Path, $Content, (New-Object System.Text.UTF8Encoding $false)) }
        $source = Get-Content -Raw -LiteralPath $env:WINDOWS_GENERATOR
        $ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$null, [ref]$null)
        foreach ($name in "Get-SearxngDefaultLanguage", "New-SearxngConfig") {
          $fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
          . ([scriptblock]::Create($fn.Extent.Text))
        }
        $cases = @{ "en-US" = "en-US"; "de-LU" = "de"; "zh-Hant-TW" = "zh-TW"; "sr-Latn-RS" = "en"; "" = "en"; "cs-CZ" = "cs-CZ" }
        foreach ($key in $cases.Keys) {
          $got = Get-SearxngDefaultLanguage -Locale $key
          if ($got -cne $cases[$key]) { throw "Get-SearxngDefaultLanguage $key gave $got, expected $($cases[$key])" }
        }
        New-Item -ItemType Directory -Force -Path $env:OUT_DIR | Out-Null
        $path = New-SearxngConfig -InstallDir $env:OUT_DIR -SecretKey "contract-secret" -SearchLanguage (Get-SearxngDefaultLanguage -Locale $env:LOCALE_NAME)
        Write-Output $path
      ' >/dev/null || fail "Windows SearXNG generator failed for $locale"
    posix_locale="${locale/-/_}.UTF-8"
    # The Windows here-string has no final newline; compare content only.
    printf '%s\n' "$(tr -d '\r' < "$tmp/windows-$locale/config/searxng/settings.yml")" > "$tmp/windows-$locale.yml"
    cmp -s "$tmp/linux-$posix_locale.yml" "$tmp/windows-$locale.yml" \
      || { diff -u "$tmp/linux-$posix_locale.yml" "$tmp/windows-$locale.yml" >&2 || true
           fail "Linux and Windows SearXNG settings differ for $locale"; }
  done
else
  echo "SKIP: PowerShell not available; Windows generator render not run"
fi

echo "SearXNG config contract checks passed"
