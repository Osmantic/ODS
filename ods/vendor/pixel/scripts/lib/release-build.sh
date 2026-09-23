#!/usr/bin/env bash
# Shared exact release-build primitive.
#
# The ONLY authority that builds an installable Pixel release tree. Both the ordinary
# apply path (scripts/apply.sh) and the migration prepare path (scripts/migrate-prepare.sh)
# call this single primitive so the two can never drift: whatever apply installs as the
# current release is byte-contract-equivalent to what migration prepare stages.
#
# Usage:
#   source "$ROOT/scripts/lib/release-build.sh"
#   pixel_build_release_stage ROOT TARGET
#
# ROOT is the repository root. TARGET is the directory that becomes the release tree (the
# stage dir for apply, the non-live stage/releases/$TARGET_PIXEL for migration prepare). It must
# not yet exist. The function builds the exact file sets with the same npm ci / venv logic
# as apply, copies the reviewed plan artifacts, and writes install-manifest.sha256 over the
# complete tree.
set -euo pipefail

# Shared canonical complete-tree digest helper. Emits the exact digest of a release tree
# (types, paths, modes, regular bytes/hash/size, symlink target strings; never following
# links) and exits non-zero on any verification failure. Used to prove an existing release
# is byte/mode/symlink-exact before adoption and to re-verify it at receipt time.
pixel_release_tree_sha() {
  local dir=$1
  local __dir
  __dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  PYTHONDONTWRITEBYTECODE=1 python3 -B "$__dir/release-tree-sha.py" "$dir"
}

pixel_build_release_stage() {
  local root=$1 target=$2
  local install_manifest_temporary=""
  [[ -d "$root" ]] || pixel_die "release-build: repository root does not exist: $root"
  [[ ! -e "$target" ]] || pixel_die "release-build: refusing to overwrite existing release target: $target"
  # Every reviewed plan artifact required by apply.sh must be present. A shared primitive
  # cannot silently build an unreviewed release.
  for required in openclaw.json openclaw.sha256 release-identity.json deployment.sha256 source-runtime.sha256; do
    [[ -f "$root/dist/$required" ]] || pixel_die "release-build: missing reviewed plan artifact dist/$required (run ./pixel plan)"
  done

  install -d -m 700 "$target/plugin" "$target/plugin-ops" "$target/plugin-frontier" "$target/web-courier" \
    "$target/source-broker" "$target/github-broker" "$target/ops-broker" "$target/frontier-broker" \
    "$target/deploy/work-provider/adapters" "$target/deploy/work-provider/profiles" \
    "$target/deploy/work-provider-router" "$target/deploy/work-model-proxy" \
    "$target/schemas" "$target/scripts/lib"

  cp "$root/plugin/package.json" "$root/plugin/package-lock.json" "$root/plugin/openclaw.plugin.json" \
     "$root/plugin/index.js" "$root/plugin/chat-audit.js" "$root/plugin/email-query.js" \
     "$root/plugin/email-query.test.mjs" "$root/plugin/authorize.mjs" \
     "$root/plugin/oauth-security.mjs" "$root/plugin/web-courier.js" "$root/plugin/web-tool.js" "$target/plugin/"
  cp "$root/plugin-ops/package.json" "$root/plugin-ops/package-lock.json" "$root/plugin-ops/openclaw.plugin.json" \
     "$root/plugin-ops/index.js" "$root/plugin-ops/chat-audit.js" "$root/plugin-ops/publish-json.js" \
     "$root/plugin-ops/secure-read.js" "$target/plugin-ops/"
  cp "$root/plugin-frontier/package.json" "$root/plugin-frontier/package-lock.json" "$root/plugin-frontier/openclaw.plugin.json" \
     "$root/plugin-frontier/index.js" "$root/plugin-frontier/chat-audit.js" "$root/plugin-frontier/publish-json.js" \
     "$root/plugin-frontier/secure-read.js" "$root/plugin-frontier/local-finalize.js" "$target/plugin-frontier/"
  [[ ${PIXEL_SOURCE_BROKER_ENABLED:-1} == 0 ]] || (cd "$target/plugin" && npm ci --omit=dev --ignore-scripts)
  [[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 0 ]] || (cd "$target/plugin-ops" && npm ci --omit=dev --ignore-scripts)
  [[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 0 ]] || (cd "$target/plugin-frontier" && npm ci --omit=dev --ignore-scripts)
  install -m 600 "$root/deploy/web-courier/courier.py" "$root/deploy/web-courier/requirements.lock" "$target/web-courier/"
  install -m 700 "$root/deploy/source-broker/broker.py" "$target/source-broker/broker.py"
  install -m 700 "$root/deploy/github-broker/broker.py" "$target/github-broker/broker.py"
  install -m 700 "$root/deploy/ops-broker/broker.py" "$target/ops-broker/broker.py"
  install -m 700 "$root/deploy/frontier-broker/broker.py" "$target/frontier-broker/broker.py"
  install -m 700 "$root/scripts/verify-frontier-codex.py" "$target/frontier-broker/verify-codex.py"
  # Provider-neutral owner credential ingress: the closed production runtime closure.
  # The owner shell delegates all credential file-creation authority to the Node ingress
  # CLI, which imports the core/internal modules and the provider registry adapters and
  # spawns the renameat2 Python no-replace helper. Test-only ingress/smoke seams are
  # excluded from production install.
  install -m 700 "$root/deploy/work-provider/install-owner-test-key.sh" "$target/deploy/work-provider/install-owner-test-key.sh"
  install -m 600 "$root/deploy/work-provider/provider-credential-ingress.mjs" "$target/deploy/work-provider/provider-credential-ingress.mjs"
  install -m 600 "$root/deploy/work-provider/provider-credential-ingress-core.mjs" "$target/deploy/work-provider/provider-credential-ingress-core.mjs"
  install -m 600 "$root/deploy/work-provider/provider-credential-ingress-internal.mjs" "$target/deploy/work-provider/provider-credential-ingress-internal.mjs"
  install -m 600 "$root/deploy/work-provider/renameat2_noreplace.py" "$target/deploy/work-provider/renameat2_noreplace.py"
  install -m 600 "$root/deploy/work-provider/provider-registry.mjs" "$target/deploy/work-provider/provider-registry.mjs"
  install -m 600 "$root/deploy/work-provider/adapter-contract.mjs" "$target/deploy/work-provider/adapter-contract.mjs"
  install -m 600 "$root/deploy/work-provider/adapters/anthropic-messages.mjs" "$target/deploy/work-provider/adapters/anthropic-messages.mjs"
  install -m 600 "$root/deploy/work-provider/adapters/local-openai.mjs" "$target/deploy/work-provider/adapters/local-openai.mjs"
  install -m 600 "$root/deploy/work-provider/adapters/openai-chat.mjs" "$target/deploy/work-provider/adapters/openai-chat.mjs"
  install -m 600 "$root/deploy/work-provider/adapters/openai-responses.mjs" "$target/deploy/work-provider/adapters/openai-responses.mjs"
  install -m 600 "$root/scripts/lib/secure-files.mjs" "$target/scripts/lib/secure-files.mjs"
  install -m 600 "$root/scripts/lib/work-contract.mjs" "$target/scripts/lib/work-contract.mjs"
  install -m 600 "$root/scripts/lib/json-schema.mjs" "$target/scripts/lib/json-schema.mjs"
  # Provider runtime: the closed transitive runtime closure for real local-only,
  # moonshot-kimi, openai, and anthropic smoke, qualification, equivalence,
  # egress-proxy, routing, and model-proxy operation. Derived from actual imports
  # and executable entrypoints; test/example/dev seams and generated evidence are
  # excluded. The provider registry eagerly loads every profile at module load and
  # work-contract eagerly loads every referenced schema, so the profiles/ and
  # schemas/ sets below are import-time closure requirements, not new defaults or
  # broadened authority. All modes remain owner-only (600/700).
  install -m 600 "$root/deploy/work-provider/credential-custody.mjs" "$target/deploy/work-provider/credential-custody.mjs"
  install -m 600 "$root/deploy/work-provider/Dockerfile.egress-proxy" "$target/deploy/work-provider/Dockerfile.egress-proxy"
  install -m 600 "$root/deploy/work-provider/Dockerfile.moonshot-worker" "$target/deploy/work-provider/Dockerfile.moonshot-worker"
  install -m 700 "$root/deploy/work-provider/run-moonshot-smoke-container.sh" "$target/deploy/work-provider/run-moonshot-smoke-container.sh"
  install -m 600 "$root/deploy/work-provider/dev-proxy-cli.mjs" "$target/deploy/work-provider/dev-proxy-cli.mjs"
  install -m 600 "$root/deploy/work-provider/egress-proxy.mjs" "$target/deploy/work-provider/egress-proxy.mjs"
  install -m 600 "$root/deploy/work-provider/equivalence-runner.mjs" "$target/deploy/work-provider/equivalence-runner.mjs"
  install -m 600 "$root/deploy/work-provider/executor.mjs" "$target/deploy/work-provider/executor.mjs"
  install -m 600 "$root/deploy/work-provider/generic-remote-transport.mjs" "$target/deploy/work-provider/generic-remote-transport.mjs"
  install -m 600 "$root/deploy/work-provider/grading.mjs" "$target/deploy/work-provider/grading.mjs"
  install -m 600 "$root/deploy/work-provider/harness.mjs" "$target/deploy/work-provider/harness.mjs"
  install -m 600 "$root/deploy/work-provider/local-policy.mjs" "$target/deploy/work-provider/local-policy.mjs"
  install -m 600 "$root/deploy/work-provider/local-transport.mjs" "$target/deploy/work-provider/local-transport.mjs"
  install -m 600 "$root/deploy/work-provider/moonshot-container-entrypoint.mjs" "$target/deploy/work-provider/moonshot-container-entrypoint.mjs"
  install -m 600 "$root/deploy/work-provider/moonshot-smoke-cli.mjs" "$target/deploy/work-provider/moonshot-smoke-cli.mjs"
  install -m 600 "$root/deploy/work-provider/moonshot-transport.mjs" "$target/deploy/work-provider/moonshot-transport.mjs"
  install -m 600 "$root/deploy/work-provider/neutral-corpus.mjs" "$target/deploy/work-provider/neutral-corpus.mjs"
  install -m 600 "$root/deploy/work-provider/patch-extraction.mjs" "$target/deploy/work-provider/patch-extraction.mjs"
  install -m 600 "$root/deploy/work-provider/patch-verifier.mjs" "$target/deploy/work-provider/patch-verifier.mjs"
  install -m 600 "$root/deploy/work-provider/private-policy.mjs" "$target/deploy/work-provider/private-policy.mjs"
  install -m 600 "$root/deploy/work-provider/provider-smoke-cli.mjs" "$target/deploy/work-provider/provider-smoke-cli.mjs"
  install -m 600 "$root/deploy/work-provider/provider-smoke-core.mjs" "$target/deploy/work-provider/provider-smoke-core.mjs"
  install -m 600 "$root/deploy/work-provider/qualification-promotion.mjs" "$target/deploy/work-provider/qualification-promotion.mjs"
  install -m 600 "$root/deploy/work-provider/qualification-runner.mjs" "$target/deploy/work-provider/qualification-runner.mjs"
  install -m 600 "$root/deploy/work-provider/run-ledger.mjs" "$target/deploy/work-provider/run-ledger.mjs"
  install -m 600 "$root/deploy/work-provider/run-store.mjs" "$target/deploy/work-provider/run-store.mjs"
  install -m 600 "$root/deploy/work-provider/transport-registry.mjs" "$target/deploy/work-provider/transport-registry.mjs"
  install -m 600 "$root/deploy/work-provider-router/qualification.mjs" "$target/deploy/work-provider-router/qualification.mjs"
  install -m 600 "$root/deploy/work-provider-router/router-policy.mjs" "$target/deploy/work-provider-router/router-policy.mjs"
  install -m 600 "$root/deploy/work-provider-router/router.mjs" "$target/deploy/work-provider-router/router.mjs"
  install -m 600 "$root/deploy/work-model-proxy/inference-policy.mjs" "$target/deploy/work-model-proxy/inference-policy.mjs"
  install -m 600 "$root/deploy/work-model-proxy/proxy.mjs" "$target/deploy/work-model-proxy/proxy.mjs"
  # All eight provider profiles are eagerly loaded by the registry at module load
  # (owner-only read), so each is an import-time closure requirement.
  install -m 600 "$root/deploy/work-provider/profiles/anthropic.json" "$target/deploy/work-provider/profiles/anthropic.json"
  install -m 600 "$root/deploy/work-provider/profiles/fireworks.json" "$target/deploy/work-provider/profiles/fireworks.json"
  install -m 600 "$root/deploy/work-provider/profiles/groq.json" "$target/deploy/work-provider/profiles/groq.json"
  install -m 600 "$root/deploy/work-provider/profiles/local.json" "$target/deploy/work-provider/profiles/local.json"
  install -m 600 "$root/deploy/work-provider/profiles/moonshot-kimi.json" "$target/deploy/work-provider/profiles/moonshot-kimi.json"
  install -m 600 "$root/deploy/work-provider/profiles/openai.json" "$target/deploy/work-provider/profiles/openai.json"
  install -m 600 "$root/deploy/work-provider/profiles/openrouter.json" "$target/deploy/work-provider/profiles/openrouter.json"
  install -m 600 "$root/deploy/work-provider/profiles/together.json" "$target/deploy/work-provider/profiles/together.json"
  # The schemas referenced by work-contract.mjs at module load (resolved as
  # ../../schemas/<name> from scripts/lib). work-contract is already a projected
  # shared runtime module, so its eager schema loads are import-time closure
  # requirements. Only the genuinely imported subset of schemas/ is projected.
  while IFS= read -r schema_name; do
    install -m 600 "$root/schemas/$schema_name" "$target/schemas/$schema_name"
  done < "$root/scripts/lib/release-provider-schemas.txt"
  if [[ ${PIXEL_WEB_COURIER_ENABLED:-1} == 1 ]]; then
    release_version=$(<"$root/VERSION")
    final_venv="$PIXEL_INSTALL_DIR/releases/$release_version/web-courier/.venv"
    PYTHONDONTWRITEBYTECODE=1 python3 -B -m venv "$target/web-courier/.venv"
    PYTHONDONTWRITEBYTECODE=1 "$target/web-courier/.venv/bin/python" -B -m pip install --disable-pip-version-check --require-hashes \
      --no-compile \
      --only-binary=:all: --no-index --no-deps --find-links "$PIXEL_WEB_COURIER_WHEELHOUSE" \
      --requirement "$target/web-courier/requirements.lock"
    PYTHONDONTWRITEBYTECODE=1 "$target/web-courier/.venv/bin/python" -B -m pip check
    PYTHONDONTWRITEBYTECODE=1 python3 -B "$root/scripts/lib/normalize-release-venv.py" \
      "$target/web-courier/.venv" "$final_venv"
  fi
  cp "$root/VERSION" "$target/VERSION"
  cp "$root/dist/openclaw.sha256" "$target/openclaw.sha256"
  install -m 600 "$root/dist/release-identity.json" "$target/release-identity.json"
  install -m 600 "$root/dist/deployment.sha256" "$target/deployment-inputs.sha256"
  install -m 600 "$root/dist/source-runtime.sha256" "$target/source-runtime.sha256"
  # Create the manifest temp beside the target so the final rename stays on the same
  # filesystem and is atomic (same guarantee the apply path previously enforced by
  # creating the temp under the releases directory).
  install_manifest_temporary=$(mktemp "$(dirname "$target")/.install-manifest.XXXXXXXXXX")
  if ! (cd "$target" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) > "$install_manifest_temporary"; then
    rm -f -- "$install_manifest_temporary"
    pixel_die "release-build: failed to build install-manifest for $target"
  fi
  chmod 600 "$install_manifest_temporary"
  mv -f -- "$install_manifest_temporary" "$target/install-manifest.sha256"
}
