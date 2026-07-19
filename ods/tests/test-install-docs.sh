#!/usr/bin/env bash
# Keep public install commands and provenance guidance aligned.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"

CANONICAL_ENDPOINT="https://install.osmantic.com/ods.sh"
CANONICAL_REPO_URL="https://github.com/Osmantic/ODS.git"
STABLE_VERSION="$(
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["release"]["version"])' \
        "$ROOT_DIR/manifest.json"
)"
STABLE_TAG="v$STABLE_VERSION"

fail() {
    echo "[FAIL] $*"
    exit 1
}

pass() {
    echo "[PASS] $*"
}

require_literal() {
    local file="$1"
    local literal="$2"
    local description="$3"

    grep -qF -- "$literal" "$file" \
        || fail "$description missing from ${file#"$REPO_ROOT"/}"
}

assert_no_retired_names() {
    python3 - "$REPO_ROOT" <<'PY'
import base64
import hashlib
import pathlib
import re
import subprocess
import sys

repo_root = sys.argv[1]
retired_product_prefix = base64.b64decode("ZHJlYW0=").decode("ascii")
retired_product_name = base64.b64decode("c2VydmVy").decode("ascii")
retired_fleet_name = base64.b64decode("ZmxlZXQ=").decode("ascii")
retired_gateway_name = base64.b64decode("Z2F0ZQ==").decode("ascii")
retired_org_prefix = base64.b64decode("bGlnaHQ=").decode("ascii")
retired_org_middle = base64.b64decode("aGVhcnQ=").decode("ascii")
retired_org_suffix = base64.b64decode("bGFicw==").decode("ascii")
retired_org_account_suffix = base64.b64decode("ZGV2cw==").decode("ascii")
retired_umbrella_middle = base64.b64decode("aG91c2U=").decode("ascii")
retired_umbrella_suffix = base64.b64decode("YWk=").decode("ascii")
separator = r"[\s_.-]*"

patterns = [
    re.compile(retired_product_prefix + separator + retired_product_name, re.IGNORECASE),
    re.compile(retired_product_prefix + separator + retired_fleet_name, re.IGNORECASE),
    re.compile(retired_product_prefix + separator + retired_gateway_name, re.IGNORECASE),
    re.compile(
        retired_org_prefix + separator + retired_org_middle + separator + retired_org_suffix,
        re.IGNORECASE,
    ),
    re.compile(
        retired_org_prefix
        + separator
        + retired_org_middle
        + separator
        + retired_org_account_suffix,
        re.IGNORECASE,
    ),
    re.compile(
        retired_org_prefix
        + separator
        + retired_umbrella_middle
        + separator
        + retired_umbrella_suffix,
        re.IGNORECASE,
    ),
    re.compile(r"name=\^?/" + retired_product_prefix + "-", re.IGNORECASE),
    re.compile(
        r"--filter\s+[\"']?name=" + retired_product_prefix + r"(?:[\"'\s]|$)",
        re.IGNORECASE,
    ),
    re.compile(r"[ps]k-lf-" + retired_product_prefix + "-", re.IGNORECASE),
]

retired_binary_hashes = {
    "03d8d3d615f32c1695f0b17b7258c9c64b18ec3b37027bfc17c5112615d0b332",
    "1bd0b57fca19d6eff2d81d4aa060e0ece17d422be77ca12e7a4054f342c22d84",
    "20570383d7b41b936cf2802823015dadd10b5516a5cf7edc9bd90817c5a8a573",
    "253a4b8f4a7ed003711c4b9ec3177cf14e87caf44c673bf02d6b3b110980dec6",
    "34e5b0b822aee482ea5bef4735ee7894b5f5823d8b804ffc6dd05cea538b637e",
    "573034c502121d9962cfa9c4ff40424b1d5ff790f244d2830bdf5d55e148dd2e",
    "71b516c4511bfb5124a064eb6b78c6028280be9f9f393f8179c2b9f86a7683f6",
    "afdc974ce0a383e7934a0c1f6bbc64dad7ac54b9015bb78da30882183e987162",
    "b2ef042415a842f038c9103bfad53f4b73fc6bdceb642fe0491c0ee825868043",
}

def has_retired_reference(value):
    return any(pattern.search(value) for pattern in patterns)

positive_samples = [
    retired_product_prefix + retired_product_name,
    retired_product_prefix + "-" + retired_product_name,
    retired_product_prefix + "_" + retired_fleet_name,
    retired_product_prefix + retired_gateway_name,
    retired_org_prefix + "-" + retired_org_middle + "-" + retired_org_suffix,
    retired_org_prefix + retired_org_middle + retired_org_account_suffix,
    retired_org_prefix + retired_umbrella_middle + "-" + retired_umbrella_suffix,
    "name=^/" + retired_product_prefix + "-",
    "--filter name=" + retired_product_prefix,
    "pk-lf-" + retired_product_prefix + "-",
]
negative_samples = [
    retired_product_prefix + " big",
    retired_org_prefix + "-" + retired_org_middle + "ed copy",
]
if not all(has_retired_reference(sample) for sample in positive_samples):
    raise SystemExit("[FAIL] Retired-name guard misses a supported identifier form")
if any(has_retired_reference(sample) for sample in negative_samples):
    raise SystemExit("[FAIL] Retired-name guard rejects unrelated language")

repo_path = pathlib.Path(repo_root)
tracked_output = subprocess.check_output(
    ["git", "-C", repo_root, "ls-files", "-z"]
)
tracked_files = [
    entry.decode("utf-8", errors="surrogateescape")
    for entry in tracked_output.split(b"\0")
    if entry
]

matches = []
for relative_path in tracked_files:
    if has_retired_reference(relative_path):
        matches.append(relative_path)
        continue

    path = repo_path / relative_path
    if not path.exists():
        continue
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"[FAIL] Could not inspect {relative_path}: {exc}")
    if hashlib.sha256(data).hexdigest() in retired_binary_hashes:
        matches.append(f"{relative_path}: retired binary asset fingerprint")
        continue
    if b"\0" in data:
        continue

    text = data.decode("utf-8", errors="ignore")
    for line_number, line in enumerate(text.splitlines(), start=1):
        if has_retired_reference(line):
            matches.append(f"{relative_path}:{line_number}:{line}")

if matches:
    print("[FAIL] Retired product, organization, or binary asset references remain:")
    print("\n".join(matches))
    raise SystemExit(1)
PY
}

install_docs=(
    "$REPO_ROOT/README.md"
    "$ROOT_DIR/README.md"
    "$ROOT_DIR/QUICKSTART.md"
    "$ROOT_DIR/docs/FAQ.md"
    "$ROOT_DIR/docs/INSTALLER_TRUST.md"
    "$ROOT_DIR/get-ods.sh"
)

clone_docs=(
    "$REPO_ROOT/README.md"
    "$ROOT_DIR/README.md"
    "$ROOT_DIR/QUICKSTART.md"
    "$ROOT_DIR/docs/INSTALLER_TRUST.md"
)

for file in "${install_docs[@]}"; do
    [[ -f "$file" ]] || fail "Expected install document missing: $file"
    require_literal "$file" "$CANONICAL_ENDPOINT" "Canonical install endpoint"
done

for file in "${clone_docs[@]}"; do
    require_literal "$file" "$CANONICAL_REPO_URL" "Canonical clone URL"
done

compatible_ref_docs=(
    "$REPO_ROOT/README.md"
    "$ROOT_DIR/README.md"
    "$ROOT_DIR/QUICKSTART.md"
    "$ROOT_DIR/docs/FAQ.md"
)

for file in "${compatible_ref_docs[@]}"; do
    require_literal "$file" '`ODS_REF` selects a compatible repository' "Compatible bootstrap ref guidance"
    require_literal "$file" 'proxies the current bootstrap from repository `main`' "Hosted main-source guidance"
    require_literal "$file" 'Reviewed merges reach it automatically after edge-cache refresh' "Automatic hosted refresh guidance"
done

assert_no_retired_names

trust_doc="$ROOT_DIR/docs/INSTALLER_TRUST.md"
release_doc="$ROOT_DIR/docs/RELEASE_CHANNELS.md"
require_literal "$trust_doc" 'currently `main`' "Default branch guidance"
require_literal "$trust_doc" 'ODS_REF=' "Release-tag pinning guidance"
require_literal "$trust_doc" 'git checkout AUDITED_COMMIT_SHA' "Exact-commit guidance"
require_literal "$trust_doc" 'X-ODS-Channel: main' "Hosted main-channel guidance"
require_literal "$trust_doc" 'X-ODS-Source-Ref: main' "Hosted main source-ref guidance"
require_literal "$trust_doc" 'serve the same mutable' "Canonical and explicit main alias guidance"
require_literal "$trust_doc" 'five minutes' "Hosted cache freshness guidance"
require_literal "$trust_doc" 'AUDITED_COMMIT_SHA/ods/get-ods.sh' "Immutable bootstrap URL guidance"
require_literal "$trust_doc" 'ods/main.sh' "Hosted main-channel guidance"
require_literal "$trust_doc" 'verify-hosted-bootstrap.sh' "Hosted bootstrap deployment verification"
require_literal "$REPO_ROOT/README.md" "\`$STABLE_TAG\` is the current stable release" "README stable release"
require_literal "$release_doc" "current stable release is \`$STABLE_TAG\`" "Release channel stable release"
require_literal "$trust_doc" "--branch $STABLE_TAG $CANONICAL_REPO_URL" "Manual stable clone"
require_literal "$trust_doc" 'predates that repository layout' "Stable layout guidance"

if grep -qF "ODS_REF=$STABLE_TAG" "$REPO_ROOT/README.md" "$trust_doc"; then
    fail "$STABLE_TAG must not be documented through the incompatible sparse-checkout bootstrap"
fi

hosted_verifier="$ROOT_DIR/scripts/verify-hosted-bootstrap.sh"
[[ -x "$hosted_verifier" ]] || fail "Hosted bootstrap verifier must be executable"
require_literal "$hosted_verifier" 'x-ods-source-ref' "Hosted source-ref verification"
require_literal "$hosted_verifier" 'x-ods-presentation' "Hosted presentation verification"
require_literal "$hosted_verifier" 'ODS_HOSTED_BOOTSTRAP_SOURCE_REF:-main' "Hosted main source-ref default"
require_literal "$hosted_verifier" 'cmp -s' "Hosted bootstrap byte comparison"

security_doc="$ROOT_DIR/SECURITY.md"
require_literal "$security_doc" 'security@osmantic.com' "Security reporting address"
require_literal "$security_doc" 'inbound alias monitored through the shared' "Security alias routing guidance"

if grep -qF 'separately deployed bootstrap revision' "${compatible_ref_docs[@]}" "$trust_doc"; then
    fail "Install guidance still describes a separately promoted hosted bootstrap"
fi

pass "Install commands and provenance guidance are consistent"
