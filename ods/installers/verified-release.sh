#!/usr/bin/env bash
# This body is also the inspectable Linux/macOS quickstart; no remote script is
# executed until gh has authenticated the exact source archive.
(
set -euo pipefail
command -v gh >/dev/null || { echo 'Install GitHub CLI from https://cli.github.com first.' >&2; exit 1; }
command -v unzip >/dev/null || { echo 'Install unzip first.' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'Install Python 3 first (JSON metadata parsing).' >&2; exit 1; }
command -v curl >/dev/null || { echo 'Install curl first.' >&2; exit 1; }
ods_get() { curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location --connect-timeout 15 --max-time 600 "$@"; }
ods_tag="$(ods_get https://api.github.com/repos/Osmantic/ODS/releases/latest | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("tag_name", "") if d.get("immutable") is True and d.get("draft") is False and d.get("prerelease") is False else "")')"
[[ "$ods_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
    echo 'No immutable signed stable release is available. No installation was changed; main is a separate development channel.' >&2
    exit 1
}
ods_tag_object="$(ods_get "https://api.github.com/repos/Osmantic/ODS/git/ref/tags/$ods_tag" | python3 -c 'import json,sys; d=json.load(sys.stdin).get("object", {}); print(d.get("sha", "") if d.get("type") == "tag" else "")')"
[[ "$ods_tag_object" =~ ^[0-9a-f]{40}$ ]] || { echo 'Stable tag must be annotated and signed.' >&2; exit 1; }
ods_commit="$(ods_get "https://api.github.com/repos/Osmantic/ODS/git/tags/$ods_tag_object" | python3 -c 'import json,sys; d=json.load(sys.stdin); v=d.get("verification", {}); o=d.get("object", {}); print(o.get("sha", "") if v.get("verified") is True and v.get("reason") == "valid" and o.get("type") == "commit" else "")')"
[[ "$ods_commit" =~ ^[0-9a-f]{40}$ ]] || { echo 'Release tag signature was not verified.' >&2; exit 1; }
ods_stage="$(mktemp -d "${TMPDIR:-/tmp}/ods-release.XXXXXXXX")"
ods_archive="ODS-$ods_tag-source.zip"
ods_get "https://github.com/Osmantic/ODS/releases/download/$ods_tag/$ods_archive" -o "$ods_stage/$ods_archive"
ods_get "https://github.com/Osmantic/ODS/releases/download/$ods_tag/provenance.sigstore.jsonl" -o "$ods_stage/provenance.sigstore.jsonl"
gh attestation verify "$ods_stage/$ods_archive" \
    --bundle "$ods_stage/provenance.sigstore.jsonl" --repo Osmantic/ODS --hostname github.com \
    --signer-workflow Osmantic/ODS/.github/workflows/release-provenance.yml \
    --source-ref "refs/tags/$ods_tag" --source-digest "$ods_commit" --deny-self-hosted-runners
unzip -q "$ods_stage/$ods_archive" -d "$ods_stage/source"
echo "Verified $ods_tag ($ods_commit). Source retained at $ods_stage/source"
bash "$ods_stage/source/install.sh" "$@"
)
