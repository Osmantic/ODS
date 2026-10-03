#!/usr/bin/env bash
set -euo pipefail

SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
cleanup() { rm -rf -- "$tmp"; }
trap cleanup EXIT

for name in source-a source-b; do
  mkdir -p "$tmp/$name"
  tar --exclude=.git --exclude=.env --exclude=.generated --exclude=.runtime --exclude=dist \
    --exclude=node_modules --exclude=__pycache__ --exclude='*.pyc' \
    -C "$SOURCE" -cf - . | tar -xf - -C "$tmp/$name"
done

shared="$tmp/shared"
mkdir -p "$shared/home" "$shared/openclaw" "$shared/install" "$shared/workspace" "$shared/cache"
printf '%s\n' '{"gateway":{"auth":{"token":"location-invariance-fixture-token"}}}' > "$shared/openclaw/openclaw.json"
answers="$shared/answers.json"
node - "$answers" "$shared" "$SOURCE/tests/fixtures/bin/openclaw" <<'NODE'
const fs = require("fs");
const [output, shared, openclawBin] = process.argv.slice(2);
fs.writeFileSync(output, JSON.stringify({
  deploymentProfile: "prepared",
  capabilityProfile: "minimal",
  ownerName: "Reproducibility Owner",
  organization: "Test Client",
  deploymentName: "location-invariance",
  timeZone: "America/New_York",
  agentId: "pixel",
  agentName: "Pixel",
  openclawBin,
  openclawHome: `${shared}/openclaw`,
  installDir: `${shared}/install`,
  workspace: `${shared}/workspace`,
  modelProvider: "local",
  modelId: "test-model",
  modelName: "Test Model",
  modelBaseUrl: "http://127.0.0.1:8000/v1",
  modelApiKey: "local-no-auth",
  modelContextWindow: 8192,
  modelMaxTokens: 1024,
  searxngBaseUrl: "http://127.0.0.1:8890",
  embeddingModel: "test.gguf",
  embeddingCache: `${shared}/cache`,
  googleAccount: ["owner", "example.com"].join("@"),
  calendarId: "primary",
  gatewayPort: 18789
}));
NODE

# generatedAt is observational metadata, not a source-location input. Freeze it so this
# test varies exactly one dimension: the absolute directory containing identical source.
fixed_date="$shared/fixed-date.cjs"
node - "$fixed_date" <<'NODE'
const fs = require("fs");
fs.writeFileSync(process.argv[2], `
const RealDate = Date;
const fixed = "2026-08-26T12:00:00.000Z";
global.Date = class extends RealDate {
  constructor(...args) { super(...(args.length ? args : [fixed])); }
  static now() { return RealDate.parse(fixed); }
};
`);
NODE

for name in source-a source-b; do
  (
    cd "$tmp/$name"
    HOME="$shared/home" XDG_CONFIG_HOME="$shared/home/.config" \
      NODE_OPTIONS="--require=$fixed_date" ./pixel configure --answers "$answers"
  )
  if grep -Eq '^PIXEL_(OPS|FRONTIER)_POLICY_SOURCE=' "$tmp/$name/.env"; then
    echo "$name retained a source-root-dependent policy path" >&2
    exit 1
  fi
  grep -Fx "PIXEL_FRONTIER_CREDENTIAL_SOURCE='/secure/client-config/openai-api-key'" "$tmp/$name/.env" >/dev/null
done

cmp "$tmp/source-a/.env" "$tmp/source-b/.env"
diff -ru "$tmp/source-a/.generated" "$tmp/source-b/.generated"

# A force-reconfigure of the same effective deployment must preserve its original
# observational timestamp. Release activation and reactivation rebuild the same
# reviewed deployment at different wall-clock times; allowing generatedAt alone to
# change would make deployment-inputs.sha256 (and therefore the immutable release
# tree) impossible to reproduce.
later_date="$shared/later-date.cjs"
node - "$later_date" <<'NODE'
const fs = require("fs");
fs.writeFileSync(process.argv[2], `
const RealDate = Date;
const fixed = "2026-08-27T13:14:15.000Z";
global.Date = class extends RealDate {
  constructor(...args) { super(...(args.length ? args : [fixed])); }
  static now() { return RealDate.parse(fixed); }
};
`);
NODE
(
  cd "$tmp/source-b"
  HOME="$shared/home" XDG_CONFIG_HOME="$shared/home/.config" \
    NODE_OPTIONS="--require=$later_date" ./pixel configure --answers "$answers" --force
)
cmp "$tmp/source-a/.generated/deployment.json" "$tmp/source-b/.generated/deployment.json"

# Reactivation begins from a fresh private copy of the rehearsal source, not the
# original activation source. Carrying the hash-bound activation deployment record
# into that new root must preserve the reviewed tree across the root and wall-clock
# change; configure remains responsible for rejecting substantive drift below.
activation_record="$shared/activation-deployment.json"
install -m 600 "$tmp/source-a/.generated/deployment.json" "$activation_record"
rm -rf -- "$tmp/source-b/.generated"
install -d -m 700 "$tmp/source-b/.generated"
install -m 600 "$activation_record" "$tmp/source-b/.generated/deployment.json"
(
  cd "$tmp/source-b"
  HOME="$shared/home" XDG_CONFIG_HOME="$shared/home/.config" \
    NODE_OPTIONS="--require=$later_date" ./pixel configure --answers "$answers" --force
)
cmp "$tmp/source-a/.env" "$tmp/source-b/.env"
cmp "$tmp/source-a/.generated/deployment.json" "$tmp/source-b/.generated/deployment.json"

# A real deployment-input change must not inherit the old timestamp. This keeps
# generatedAt honest while making only semantically identical rebuilds reproducible.
changed_answers="$shared/changed-answers.json"
node - "$answers" "$changed_answers" <<'NODE'
const fs = require("fs");
const [source, output] = process.argv.slice(2);
const value = JSON.parse(fs.readFileSync(source, "utf8"));
value.deploymentName = "location-invariance-changed";
fs.writeFileSync(output, JSON.stringify(value));
NODE
(
  cd "$tmp/source-b"
  HOME="$shared/home" XDG_CONFIG_HOME="$shared/home/.config" \
    NODE_OPTIONS="--require=$later_date" ./pixel configure --answers "$changed_answers" --force
)
jq -e '.generatedAt == "2026-08-27T13:14:15.000Z" and .deploymentName == "location-invariance-changed"' \
  "$tmp/source-b/.generated/deployment.json" >/dev/null
if cmp -s "$tmp/source-a/.generated/deployment.json" "$tmp/source-b/.generated/deployment.json"; then
  echo "changed deployment incorrectly retained the previous deployment record" >&2
  exit 1
fi
printf 'Source-location deployment inputs are reproducible.\n'
