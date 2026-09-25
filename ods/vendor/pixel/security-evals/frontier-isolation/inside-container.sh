#!/usr/bin/env bash
set -euo pipefail
# The harness itself is root and intentionally owns bounded /tmp diagnostics while each
# product command runs as its real lower-privilege deployment identity.

mkdir -p /tmp/repo
cp -a -- /src/. /tmp/repo
useradd --create-home --shell /bin/bash pixelowner
printf '%s\n' 'pixelowner ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/pixelowner
chmod 0440 /etc/sudoers.d/pixelowner
chown -R pixelowner:pixelowner /tmp/repo /home/pixelowner
printf '%s\n' '#!/bin/sh' 'exit 0' > /usr/local/bin/systemctl
chmod 0755 /usr/local/bin/systemctl

mkdir -p /secure/client-config
printf '%s\n' 'test-only-provider-key' > /secure/client-config/openai-api-key
chown pixelowner:pixelowner /secure/client-config/openai-api-key
chmod 0600 /secure/client-config/openai-api-key

repo=/tmp/repo
qualification_policy=/secure/client-config/frontier-policy.json
jq --arg as_of "$(date -u +%Y-%m-%d)" '
  .provider.timeoutSeconds=5 |
  .provider.cost={
    mode:"metered",
    currency:"USD",
    inputMicrosPerMillionTokens:2000000,
    outputMicrosPerMillionTokens:8000000,
    source:"offline isolation fixture",
    asOf:$as_of
  } |
  .budgets.maxEstimatedCostMicros=1000000
' "$repo/deploy/frontier-broker/policy.example.json" > "$qualification_policy"
chown pixelowner:pixelowner "$qualification_policy"
chmod 0600 "$qualification_policy"
answers=/secure/client-config/onboarding.json
jq -n \
  --arg repo "$repo" \
  --arg home /home/pixelowner \
  --arg credential /secure/client-config/openai-api-key \
  --arg policy "$qualification_policy" \
  '{
    deploymentProfile:"prepared",
    capabilityProfile:"minimal",
    ownerName:"Isolation Test",
    organization:"Pixel Test",
    deploymentName:"frontier-isolation",
    timeZone:"UTC",
    agentId:"pixel",
    agentName:"Pixel",
    openclawBin:($repo + "/tests/fixtures/bin/openclaw"),
    openclawHome:($home + "/.openclaw"),
    installDir:($home + "/.local/share/pixel"),
    workspace:($home + "/.openclaw/workspace-pixel"),
    modelProvider:"local",
    modelId:"test-model",
    modelName:"Test Model",
    modelBaseUrl:"http://127.0.0.1:8000/v1",
    modelApiKey:"local-no-auth",
    modelContextWindow:8192,
    modelMaxTokens:1024,
    searxngBaseUrl:"http://127.0.0.1:8890",
    embeddingModel:"test.gguf",
    embeddingCache:($home + "/.cache/embeddings"),
    googleAccount:"isolation@example.invalid",
    calendarId:"primary",
    gatewayPort:18789,
    emailLimbEnabled:false,
    calendarLimbEnabled:false,
    calendarDirectEnabled:false,
    socialLimbEnabled:false,
    webLimbEnabled:false,
    operationsLimbEnabled:false,
    frontierLimbEnabled:true,
    frontierPolicyFile:$policy,
    frontierCredentialFile:$credential
  }' > "$answers"
chown pixelowner:pixelowner "$answers"
chmod 0600 "$answers"

sudo -u pixelowner env HOME=/home/pixelowner XDG_CONFIG_HOME=/home/pixelowner/.config PATH="$PATH" \
  "$repo/pixel" configure --answers "$answers"
sudo -u pixelowner env HOME=/home/pixelowner XDG_CONFIG_HOME=/home/pixelowner/.config PATH="$PATH" \
  "$repo/pixel" frontier-broker --confirm

state=/var/lib/pixel-frontier-broker
policy=/etc/pixel-frontier-broker/policy.json
credential="$state/private/provider-key"
test "$(stat -c '%U:%G:%a' "$credential")" = 'root:pixel-frontier:640'
test "$(stat -c '%U:%G:%a' "$state/private")" = 'root:pixel-frontier:750'
test "$(stat -c '%U:%G:%a' "$policy")" = 'root:pixel-frontier:640'
sudo -u pixel-frontier-broker test -r "$credential"
sudo -u pixel-frontier-broker test ! -w "$credential"
sudo -u pixel-frontier-broker test ! -w "$state/private"
sudo -u pixel-frontier-broker /opt/pixel-frontier-broker/verify-codex.py /usr/local/bin/codex >/tmp/installed-codex-probe.json
jq -e '.status == "pass" and .networkTarget == "loopback-only" and .strictOutputSchema == true' /tmp/installed-codex-probe.json >/dev/null
sudo -u pixelowner test ! -r "$credential"
sudo -u pixelowner test ! -r "$policy"
sudo -u pixelowner test -w "$state/requests"
sudo -u pixelowner test -w "$state/feedback"
sudo -u pixelowner test -r "$state/results"
sudo -u pixelowner test -r "$state/metrics/usage.json"
sudo -u pixelowner jq -e '.totals.jobs == 0 and .privacy and (.jobId | not) and (.prompt | not) and (.credential | not)' "$state/metrics/usage.json" >/dev/null
for directory in private request-archive plans approvals authority runtime cache integrations; do
  sudo -u pixelowner test ! -r "$state/$directory"
done
test "$(stat -c '%U:%G:%a' "$state/cache")" = 'pixel-frontier-broker:pixel-frontier:700'
test "$(stat -c '%U:%G:%a' "$state/integrations")" = 'pixel-frontier-broker:pixel-frontier:700'

local_job="frontier-$(date +%s%3N)-abcdef111111"
local_receipt="local-$(date +%s%3N)-abcdef111111"
jq -n --arg job "$local_job" --arg receipt "$local_receipt" --arg created "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '{
  schemaVersion:2,
  jobId:$job,
  kind:"plan_review",
  createdAt:$created,
  requester:"pixel",
  classification:"public",
  dataCategories:["structural"],
  payload:{objective:"Keep this sufficient synthetic review local",assumptions:[],constraints:[],localFindings:[],acceptanceCriteria:[]},
  maxOutputTokens:256,
  reason:"local-only isolation test",
  routing:{schemaVersion:1,receiptId:$receipt,observedAt:$created,localAttemptCount:1,localOutcome:"completed-sufficient",reasonCodes:["local-sufficient"]},
  boundary:"broker decides"
}' | sudo -u pixelowner tee "$state/requests/$local_job.json" >/dev/null
sudo -u pixel-frontier-broker /opt/pixel-frontier-broker/broker.py \
  --policy "$policy" --state "$state" --once >/tmp/local-broker-once.json
jq -e '.processed == 1' /tmp/local-broker-once.json >/dev/null
sudo -u pixelowner jq -e '.status == "local-only" and .providerInvoked == false and .routingReceipt.decision == "local-only"' "$state/results/$local_job.json" >/dev/null
sudo -u pixelowner test ! -e "$state/plans/$local_job.json"
sudo -u pixelowner jq -e '.routing.decisions["local-only"] == 1 and .routing.providerCalls == 0 and .savings.avoidedProviderCalls == 1 and (.jobId | not)' "$state/metrics/usage.json" >/dev/null

grant=/tmp/frontier-isolation-grant.json
jq -n '{
  id:"isolation-window",
  level:"bounded-auto",
  taskClasses:["plan_review"],
  classifications:["public"],
  maxExecutions:1,
  windowSeconds:300,
  maxInputTokens:2048,
  maxOutputTokens:256,
  maxFailures:1
}' > "$grant"
chown pixelowner:pixelowner "$grant"
sudo -u pixelowner env HOME=/home/pixelowner XDG_CONFIG_HOME=/home/pixelowner/.config PATH="$PATH" \
  "$repo/pixel" frontier-authority grant "$grant" 5 --confirm >/tmp/frontier-grant-result.json
jq -e '.id == "isolation-window" and .source == "lease" and .expiresAt' /tmp/frontier-grant-result.json >/dev/null
sudo -u pixelowner env HOME=/home/pixelowner XDG_CONFIG_HOME=/home/pixelowner/.config PATH="$PATH" \
  "$repo/pixel" frontier-authority show | jq -e '.grants[] | select(.id == "isolation-window" and .source == "lease")' >/dev/null
test -d /run/pixel-frontier-operator
test -z "$(find /run/pixel-frontier-operator -mindepth 1 -maxdepth 1 -name 'grant.*.json' -print -quit)"
sudo -u pixelowner env HOME=/home/pixelowner XDG_CONFIG_HOME=/home/pixelowner/.config PATH="$PATH" \
  "$repo/pixel" frontier-authority revoke isolation-window --confirm >/tmp/frontier-revoke-result.json
jq -e '.grantId == "isolation-window" and .revokedAt' /tmp/frontier-revoke-result.json >/dev/null

job="frontier-$(date +%s%3N)-abcdef123456"
request="$state/requests/$job.json"
jq -n --arg job "$job" --arg created "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '{
  schemaVersion:1,
  jobId:$job,
  kind:"plan_review",
  createdAt:$created,
  requester:"pixel",
  classification:"public",
  dataCategories:["structural"],
  payload:{objective:"Review the synthetic isolation plan",assumptions:[],constraints:[],localFindings:[],acceptanceCriteria:[]},
  maxOutputTokens:256,
  reason:"isolation test",
  routing:{localAttemptCount:2,localOutcome:"completed-needs-review",reasonCodes:["quality-check","security-review"]},
  boundary:"broker decides"
}' | sudo -u pixelowner tee "$request" >/dev/null
sudo -u pixel-frontier-broker /opt/pixel-frontier-broker/broker.py \
  --policy "$policy" --state "$state" --once >/tmp/broker-once.json
jq -e '.processed == 1' /tmp/broker-once.json >/dev/null
sudo -u pixelowner jq -e '.status == "awaiting-approval" and .classification == "public" and .routingReceipt.localAttemptCount == 2 and (.routingReceipt.reasonCodes | index("security-review"))' "$state/results/$job.json" >/dev/null
sudo -u pixelowner test ! -r "$state/plans/$job.json"
sudo -u pixel-frontier-broker jq -e --arg job "$job" '.jobId == $job and .requestHash and .planHash' "$state/plans/$job.json" >/dev/null

# Exercise the product live-qualification CLI with the container network disabled. This
# intentionally proves a bounded failure receipt, never provider availability or spend.
sudo -u pixel-frontier-broker /opt/pixel-frontier-broker/broker.py \
  --policy "$policy" --state "$state" >/tmp/frontier-qualification-worker.log 2>&1 &
qualification_worker=$!
cleanup_qualification_worker() {
  kill "$qualification_worker" 2>/dev/null || true
  wait "$qualification_worker" 2>/dev/null || true
}
trap cleanup_qualification_worker EXIT
live_authorization=/home/pixelowner/frontier-live-authorization.json
sudo -u pixelowner env HOME=/home/pixelowner XDG_CONFIG_HOME=/home/pixelowner/.config PATH="$PATH" \
  "$repo/pixel" frontier-live-qualify authorization \
  --auth-mode api-key \
  --output "$live_authorization" \
  --max-estimated-cost-micros 100000 \
  --authorize-one-synthetic-provider-call \
  --authorize-api-billing >/tmp/frontier-live-authorization.json
jq -e '.status == "authorization-created" and .authMode == "api-key" and .maxProviderCalls == 1' /tmp/frontier-live-authorization.json >/dev/null
test "$(stat -c '%U:%G:%a' "$live_authorization")" = 'pixelowner:pixelowner:600'
sudo -u pixelowner env HOME=/home/pixelowner XDG_CONFIG_HOME=/home/pixelowner/.config PATH="$PATH" \
  "$repo/pixel" frontier-live-qualify prepare \
  --authorization "$live_authorization" >/tmp/frontier-live-prepared.json
jq -e '.status == "awaiting-confirmation" and .syntheticOnly == true and .maxProviderCalls == 1 and .confirmationHash and .jobId and .planHash' /tmp/frontier-live-prepared.json >/dev/null
qualification_id=$(jq -r '.qualificationId' /tmp/frontier-live-prepared.json)
confirmation_hash=$(jq -r '.confirmationHash' /tmp/frontier-live-prepared.json)
qualification_job=$(jq -r '.jobId' /tmp/frontier-live-prepared.json)
sudo -u pixelowner jq -e '
  .status == "awaiting-approval" and
  .classification == "public" and
  .dataCategories == ["structural"] and
  .maxOutputTokens == 256 and
  .providerInvoked == false and
  .routingReceipt.localAttempt.reasonCodes == ["security-review"] and
  .sanitizedPreview.payload.assumptions == ["All content in this capsule is synthetic and public."]
' "$state/results/$qualification_job.json" >/dev/null
sudo -u pixelowner env HOME=/home/pixelowner XDG_CONFIG_HOME=/home/pixelowner/.config PATH="$PATH" \
  "$repo/pixel" frontier-live-qualify confirm "$qualification_id" "$confirmation_hash" \
  --authorization "$live_authorization" --transmit >/tmp/frontier-live-receipt.json
jq -e '
  .status == "fail" and
  (.outcome == "provider-failed" or .outcome == "provider-not-invoked") and
  .syntheticOnly == true and
  .authorizationBound == true and
  .exactApprovalBound == true and
  .maxProviderCalls == 1 and
  .providerCallsObserved <= 1 and
  .providerCallCeilingHeld == true and
  .authMode == "api-key" and
  .billingBoundary == "platform-api" and
  (.jobId | not) and (.prompt | not) and (.response | not) and (.credential | not) and (.modelId | not)
' /tmp/frontier-live-receipt.json >/dev/null
first_receipt_hash=$(sha256sum /tmp/frontier-live-receipt.json | awk '{print $1}')
sudo -u pixelowner env HOME=/home/pixelowner XDG_CONFIG_HOME=/home/pixelowner/.config PATH="$PATH" \
  "$repo/pixel" frontier-live-qualify confirm "$qualification_id" "$confirmation_hash" \
  --authorization "$live_authorization" --transmit >/tmp/frontier-live-recovered.json
test "$(sha256sum /tmp/frontier-live-recovered.json | awk '{print $1}')" = "$first_receipt_hash"
test "$(jq -s -r --arg job "$qualification_job" '[.[] | select(.jobId == $job)] | length' "$state/authority/usage.jsonl")" = 1
cleanup_qualification_worker
trap - EXIT

grep -F 'User=pixel-frontier-broker' /etc/systemd/system/pixel-frontier-broker.service >/dev/null
grep -F 'ProtectSystem=strict' /etc/systemd/system/pixel-frontier-broker.service >/dev/null
grep -F 'ProtectProc=invisible' /etc/systemd/system/pixel-frontier-broker.service >/dev/null
grep -F 'InaccessiblePaths=-/srv -/mnt -/media' /etc/systemd/system/pixel-frontier-broker.service >/dev/null
grep -F 'ExecStartPre="/opt/pixel-frontier-broker/verify-codex.py" "/usr/local/bin/codex"' /etc/systemd/system/pixel-frontier-broker.service >/dev/null
if grep -Eq '^PIXEL_FRONTIER_(POLICY_PATH|CREDENTIAL_PATH)=' "$repo/.generated/gateway.env"; then
  echo 'Gateway environment received Frontier authority' >&2
  exit 1
fi

chatgpt_auth=/secure/client-config/codex-auth.json
chatgpt_answers=/secure/client-config/onboarding-chatgpt.json
printf '%s\n' '{"auth_mode":"chatgpt"}' > "$chatgpt_auth"
chmod 0600 "$chatgpt_auth"
chown pixelowner:pixelowner "$chatgpt_auth"
jq --arg repo "$repo" --arg auth "$chatgpt_auth" \
  '.frontierPolicyFile=($repo + "/deploy/frontier-broker/policy.chatgpt.example.json") | .frontierCredentialFile=$auth' \
  "$answers" > "$chatgpt_answers"
chmod 0600 "$chatgpt_answers"
chown pixelowner:pixelowner "$chatgpt_answers"
sudo -u pixelowner env HOME=/home/pixelowner XDG_CONFIG_HOME=/home/pixelowner/.config PATH="$PATH" \
  "$repo/pixel" configure --answers "$chatgpt_answers" --force
sudo -u pixelowner env HOME=/home/pixelowner XDG_CONFIG_HOME=/home/pixelowner/.config PATH="$PATH" \
  "$repo/pixel" frontier-broker --confirm

auth_dir="$state/private/codex-auth"
auth_cache="$auth_dir/auth.json"
test ! -e "$credential"
test "$(stat -c '%U:%G:%a' "$auth_dir")" = 'pixel-frontier-broker:pixel-frontier:700'
test "$(stat -c '%U:%G:%a' "$auth_cache")" = 'pixel-frontier-broker:pixel-frontier:600'
sudo -u pixel-frontier-broker test -r "$auth_cache"
sudo -u pixel-frontier-broker test -w "$auth_cache"
sudo -u pixel-frontier-broker test -w "$auth_dir"
sudo -u pixel-frontier-broker test ! -w "$state/private"
sudo -u pixelowner test ! -r "$auth_cache"
sudo -u pixelowner test ! -r "$policy"
jq -e '.provider.authMode == "chatgpt"' "$policy" >/dev/null
grep -F "PIXEL_FRONTIER_AUTH_MODE='chatgpt'" "$repo/.generated/frontier-broker.env" >/dev/null
if grep -F "$auth_cache" /etc/systemd/system/pixel-frontier-broker.service; then
  echo 'Refreshable ChatGPT auth cache was forced read-only' >&2
  exit 1
fi
status=$(sudo -u pixel-frontier-broker env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$auth_dir" CODEX_HOME="$auth_dir" LANG=C.UTF-8 \
  /usr/local/bin/codex login status -c 'cli_auth_credentials_store="file"' 2>&1)
[[ "$status" == *ChatGPT* ]]

chatgpt_job="frontier-$(date +%s%3N)-abcdef654321"
jq -n --arg job "$chatgpt_job" --arg created "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '{
  schemaVersion:1,
  jobId:$job,
  kind:"plan_review",
  createdAt:$created,
  requester:"pixel",
  classification:"public",
  dataCategories:["structural"],
  payload:{objective:"Review the synthetic subscription boundary",assumptions:[],constraints:[],localFindings:[],acceptanceCriteria:[]},
  maxOutputTokens:256,
  reason:"subscription isolation test",
  routing:{localAttemptCount:1,localOutcome:"policy-required-review",reasonCodes:["security-review"]},
  boundary:"broker decides"
}' | sudo -u pixelowner tee "$state/requests/$chatgpt_job.json" >/dev/null
sudo -u pixel-frontier-broker /opt/pixel-frontier-broker/broker.py \
  --policy "$policy" --state "$state" --once >/tmp/chatgpt-broker-once.json
jq -e '.processed == 1' /tmp/chatgpt-broker-once.json >/dev/null
sudo -u pixelowner jq -e '.status == "awaiting-approval" and .providerAuthMode == "chatgpt" and .routingReceipt.localOutcome == "policy-required-review"' "$state/results/$chatgpt_job.json" >/dev/null
sudo -u pixelowner test ! -r "$state/plans/$chatgpt_job.json"

printf '%s\n' '{"status":"pass","checks":["offline-codex-surface","dedicated-user","root-owned-api-key","chatgpt-auth-cache","auth-mode-residue-removal","gateway-denial","authority-cli","typed-spool","adaptive-local-only","routing-receipt","usage-projection","private-cache","private-integration","private-plan","network-disabled-live-qualification","single-use-live-recovery","systemd-hardening"]}'
