#!/bin/bash
# Android Lite (Termux) contract smoke — runs in CI on Linux, no phone required.
# Proves: script syntax, mobile catalog integrity (real pinned checksums),
# side-effect-free dry-run, CLI surface, and no full-stack (Docker) leakage.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[smoke] android-lite contract"

# ── 1. Script syntax ────────────────────────────────────────────────────────
for file in installers/mobile/install-mobile.sh \
            installers/mobile/ods-mobile \
            installers/mobile/lib/constants.sh \
            installers/mobile/lib/model-pull.sh \
            tests/smoke/android-lite.sh; do
    bash -n "$file"
done

# ── 2. Mobile catalog integrity ─────────────────────────────────────────────
CATALOG=config/mobile-models.json
jq -e '.version == 1' "$CATALOG" >/dev/null

default_model="$(jq -r '.default_model' "$CATALOG")"
jq -e --arg id "$default_model" \
    '.models[] | select(.id == $id)' "$CATALOG" >/dev/null

# Every entry must carry real provenance: a 64-hex sha256 (no placeholders),
# a positive byte size, RAM floor, and context defaults.
jq -e '.models | length > 1' "$CATALOG" >/dev/null   # model-agnostic: >1 entry
jq -e '.models | all(
        (.id | length > 0) and
        (.gguf_file | length > 0) and
        (.gguf_url | startswith("https://")) and
        (.gguf_sha256 | test("^[0-9a-f]{64}$")) and
        (.size_bytes > 0) and
        (.min_ram_gb > 0) and
        (.context_default > 0) and
        (.provenance | length > 0)
      )' "$CATALOG" >/dev/null

# ── 3. Dry-run is side-effect-free and full-stack-free ──────────────────────
# Runs with a throwaway HOME on a non-Termux host: must exit 0, write nothing,
# and never mention the Docker stack. A dry-run that tried to call pkg/git/curl
# for real would fail loudly here.
fake_home="$(mktemp -d)"
dry_out="$(HOME="$fake_home" ODS_PLATFORM_OVERRIDE=android-termux \
    bash installers/mobile/install-mobile.sh --dry-run)"

test -z "$(ls -A "$fake_home")"   # no writes into HOME
rmdir "$fake_home"

# shellcheck disable=SC1091
llama_tag="$(source installers/mobile/lib/constants.sh && echo "$LLAMA_CPP_ANDROID_TAG")"
grep -q "$llama_tag" <<<"$dry_out"
grep -q "$default_model" <<<"$dry_out"
if grep -qiE "docker|compose|n8n|open-webui|dashboard-api|host-agent" <<<"$dry_out"; then
    echo "[smoke] FAIL: android-lite dry-run mentions the full Docker stack" >&2
    exit 1
fi

# --skip-model dry-run still plans a working runtime + CLI
skip_out="$(ODS_PLATFORM_OVERRIDE=android-termux \
    bash installers/mobile/install-mobile.sh --dry-run --skip-model)"
grep -q "$llama_tag" <<<"$skip_out"
grep -qi "skip" <<<"$skip_out"

# Fresh-install ordering: a fresh Termux has no jq until phase_packages installs
# it, so the installer must not need jq before then. Prove it: dry-run must
# succeed with jq absent from PATH entirely.
nojq_bin="$(mktemp -d)"
ln -s "$(command -v dirname)" "$nojq_bin/dirname"
nojq_out="$(PATH="$nojq_bin" ODS_PLATFORM_OVERRIDE=android-termux \
    /bin/bash installers/mobile/install-mobile.sh --dry-run)"
grep -q "pkg install" <<<"$nojq_out"
rm -rf "$nojq_bin"

# Phase order contract: packages install before jq-dependent model resolution,
# and the CLI/catalog configure step lands before the model pull so a failed
# download still leaves 'ods-mobile models pull' available for recovery.
phase_seq="$(grep -E '^phase_[a-z_]+$' installers/mobile/install-mobile.sh | tr '\n' ' ')"
test "$phase_seq" = "phase_preflight phase_packages phase_resolve_model phase_build phase_configure phase_model phase_summary "

# ── 4. ods-mobile CLI surface ───────────────────────────────────────────────
CLI=installers/mobile/ods-mobile
for cmd in status chat serve bench models; do
    grep -q "$cmd)" "$CLI"
done
grep -q -- "--ctx" "$CLI"          # context exposed on chat/serve/bench
grep -q "127.0.0.1" "$CLI"         # loopback default
grep -q "4096" "$CLI"              # conservative context default

# bench must actually pass the context to llama-bench, not just print it.
# llama-bench at the pinned tag has no -c/--ctx-size; context depth is -d.
grep -qE 'llama-bench" -m "\$MODEL_PATH" -d "\$RUN_CTX"' "$CLI"

bash "$CLI" help >/dev/null
bash "$CLI" version >/dev/null

# Functional check against a fixture home (read-only commands work off-device).
fixture="$(mktemp -d)"
mkdir -p "$fixture/config" "$fixture/models" "$fixture/bin" "$fixture/lib"
cp "$CATALOG" "$fixture/config/mobile-models.json"
cp installers/mobile/lib/model-pull.sh "$fixture/lib/"
models_out="$(ODS_MOBILE_HOME="$fixture" bash "$CLI" models list)"
grep -q "$default_model" <<<"$models_out"
rm -rf "$fixture"

# ── 5. Installer installs the static CLI file (never generates one) ─────────
grep -q 'CLI_SRC="$SCRIPT_DIR/installers/mobile/ods-mobile"' installers/mobile/install-mobile.sh
grep -q 'install -m 755 "$CLI_SRC"' installers/mobile/install-mobile.sh

echo "[smoke] PASS android-lite"
