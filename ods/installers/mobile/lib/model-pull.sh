#!/bin/bash
# Purpose: Shared model download + sha256 verification for Android Lite.
# Expects: curl, jq, sha256sum on PATH. Pure at source time (functions only).
# Provides: ods_mobile_model_field(), ods_mobile_pull_model()
# Modder notes: Sourced by BOTH install-mobile.sh (from the repo) and the
#   installed ods-mobile CLI (from $ODS_MOBILE_HOME/lib/ — the installer copies
#   this file there). One download/verify path, no duplication. Keep it free of
#   Termux-specific calls so it also runs in CI and on Linux hosts.

# ods_mobile_model_field <catalog.json> <model-id> <field>
# Prints the field value; exits non-zero if the model or field is missing/null.
ods_mobile_model_field() {
    local catalog="$1" model_id="$2" field="$3"

    jq -re --arg id "$model_id" --arg f "$field" \
        '.models[] | select(.id == $id) | .[$f]' "$catalog"
}

# ods_mobile_pull_model <catalog.json> <model-id> <dest-dir>
# Downloads the model GGUF with resume support and verifies its pinned sha256.
# A checksum mismatch deletes the partial file and fails loudly — never keeps
# unverified model bytes on disk.
ods_mobile_pull_model() {
    local catalog="$1" model_id="$2" dest_dir="$3"
    local gguf_file gguf_url gguf_sha size_bytes dest

    gguf_file="$(ods_mobile_model_field "$catalog" "$model_id" gguf_file)"
    gguf_url="$(ods_mobile_model_field "$catalog" "$model_id" gguf_url)"
    gguf_sha="$(ods_mobile_model_field "$catalog" "$model_id" gguf_sha256)"
    size_bytes="$(ods_mobile_model_field "$catalog" "$model_id" size_bytes)"
    dest="$dest_dir/$gguf_file"

    if [[ -f "$dest" ]]; then
        echo "[INFO] $gguf_file already present, verifying checksum..."
        if echo "$gguf_sha  $dest" | sha256sum -c - >/dev/null; then
            echo "[INFO] checksum OK — nothing to download."
            return 0
        fi
        echo "[ERROR] Existing $dest fails its pinned sha256." >&2
        echo "        Remove it (ods-mobile models rm $model_id) and pull again." >&2
        return 1
    fi

    echo "[INFO] Downloading $gguf_file ($((size_bytes / 1024 / 1024)) MB) ..."
    curl -fL --retry 3 -C - -o "$dest.part" "$gguf_url"

    echo "[INFO] Verifying sha256 ..."
    if ! echo "$gguf_sha  $dest.part" | sha256sum -c - >/dev/null; then
        rm -f "$dest.part"
        echo "[ERROR] sha256 mismatch for $gguf_file — deleted the download." >&2
        echo "        Expected: $gguf_sha" >&2
        echo "        The upstream file changed or the download corrupted." >&2
        echo "        If upstream re-published the model, the pinned catalog" >&2
        echo "        entry must be re-verified and updated — do not bypass." >&2
        return 1
    fi

    mv "$dest.part" "$dest"
    echo "[INFO] Verified and installed: $dest"
}
