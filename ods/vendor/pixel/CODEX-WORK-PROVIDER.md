# Pixel Codex Work Provider

Status: compiler, owner-private mock lifecycle, credential-free CLI qualification,
signed-MFA and credential-custody foundations, and an outer-isolated Codex container path
with an exact-host egress proxy. The example policy and every task class are disabled. No
production identity provider, service identity, credential, proxy network, or published
runner image is installed, and no provider call or live external side effect has been
performed. The mock path requires two explicit test flags. Its
separate CLI qualifier can launch only with an empty file-backed auth home
and a sterile environment, is tested against a fake local executable, reports
`codex-cli-qualified-fake`, and records that no external provider was invoked.

## Purpose

This is a separately governed Deep Work spillover path for work that a local model already
attempted but could not finish or confidently verify. It does not widen Pixel's existing
Frontier task classes. Its goals are to keep useful work local first, minimize the exact
material that may leave the machine, make billing mode unmistakable, and bind any future
provider turn to one inspectable plan.

This is a logical privacy boundary, not a physical air gap. If a later operator approves a
live turn, the sanitized capsule and basic provider metadata will leave the machine and be
handled under the configured OpenAI account, workspace, region, retention, and service
terms. The private request and replacement map must remain local.

## Implemented compile path

1. A strict private request binds one job, checkpoint, local-attempt receipt, task class,
   classification, data categories, objective, criteria, documents, and content hashes.
2. A separate private policy must explicitly enable both the provider and exact task.
3. Compilation rejects locally sufficient work, expired requests, restricted data,
   never-egress categories, credentials, private keys, authentication material, session
   tokens, regulated records, raw source bodies, undeclared source code, reserved
   placeholders, opaque high-entropy tokens, unsupported language labels, and all policy,
   size, token, or cost overruns.
4. Declared names and identifiers plus common email, URL, absolute-path, IP, and phone
   forms become synthetic `PIXELWORK` placeholders. Private owner, client, request, job,
   checkpoint, and document identities cannot enter the capsule.
5. The compiler emits sequential `DOC_001` identities, a strict sanitized capsule, a
   hash-bound structured-output schema, and a private preview plan. The replacement map
   has a private random commitment nonce to resist offline guessing from its plan hash.
6. The plan says `providerInvoked: false` and `credentialsProjected: false`. It requires a
   future external, single-use approval over the complete plan hash. Compilation itself
   cannot consume such an approval.

## Implemented private mock lifecycle

The disabled test foundation stores the exact request, policy, plan, and replacement map in
canonical owner-only files. Every read is bounded, no-follow, single-link, canonical JSON,
and recompiles the request and policy to prove the plan and map are reproducible.

An authorization contract binds the exact plan, request, policy, capsule, output schema,
job, checkpoint, provider, billing acknowledgment, data-owner confirmation, and a hash of
external human-authentication evidence. The builder no longer manufactures those
confirmations. The legacy synthetic path still requires `allowMockAuthorization`
explicitly. A separate production-shaped entry point accepts only a verified signed MFA
assertion as described below.

Mock execution validates the complete authorization before atomically creating a durable
single-use claim. The claim exists before adapter entry, so a race, crash, timeout, or
uncertain response burns the authorization instead of retrying. The adapter receives only a
deep-frozen capsule, output schema, provider label, limits, and plan hash—never the private
request, map, owner/client identity, credential, or authentication evidence. The mock
lifecycle accepts only an explicitly enabled `mock` adapter and a `test-mock`
authorization. It cannot enter the isolated CLI path. Conversely, the isolated CLI path
requires an `external-signed-mfa` authorization and cannot consume a mock grant.

Successful mock output must be bounded strict UTF-8, exact canonical JSON, match the
hash-bound schema and task, target only synthetic documents, use only issued placeholders,
contain no secret or opaque token, and remain inside reported token limits. Rehydration then
occurs locally from the private map, including restoration of local document identities.
The private output is owner-only; the ordinary result receipt contains only hashes,
bounded usage, fixed states, and false authority. Invalid or failed output is not retained.

`structural-only` accepts structures, interfaces, patch outlines, test failures,
specifications, and schemas. It rejects source, diffs, and raw build logs.
`owner-authorized-content` can carry source or diffs only when the task policy enables that
mode and the request explicitly declares `proprietary-code`. Neither mode permits a
credential or another never-egress category.

## Implemented signed-MFA and credential-custody foundations

The authorization verifier accepts an exact-shape Ed25519 assertion from one policy-pinned
external issuer and public key. The assertion binds the complete plan hash, provider,
model, billing acknowledgment, a fresh typed challenge, password authentication, and
either an authenticator-app code or email one-time code. It is valid for at most five
minutes. Pixel receives no password, one-time code, session token, identity claim, or
private signing key. Before creating the one-use authorization, the private store writes a
content-free consumption tombstone; replay, concurrency, or an interrupted authorization
write therefore burns the evidence rather than creating two grants.

This repository exercises that contract with synthetic Ed25519 fixtures. It does not ship
or configure the external identity-provider service that performs password and MFA checks.
Production use requires a deployment-owned issuer whose key fingerprint and identity are
pinned in private policy.

The credential-custody module validates a broker-private, absolute, single-link regular
file under a private real directory. ChatGPT mode accepts only a bounded JSON Codex cache
whose authentication mode is `chatgpt`; a returned refresh is installed only after full
shape and mode validation and an atomic private-file replacement. API mode accepts only
one bounded line and refuses any file identity or timestamp change between inspection and
projection. Receipts expose no path, owner, account, token, content, or content hash;
opaque handles contain no reflectable properties and keep their state in a private
`WeakMap`.

For the isolated adapter, credential bytes and the sanitized task are separate fields in
one canonical, length-framed stdin message. The container creates a private ephemeral
`HOME`, `CODEX_HOME`, and temporary directory. ChatGPT material becomes only the temporary
Codex `auth.json`; API material is passed to `codex login --with-api-key` on that process's
stdin. Neither form enters arguments, ambient environment, plans, operator projections,
logs, or result receipts. The credential directory is removed on every terminal path, and
only a validated ChatGPT refresh can return through the private broker channel.

These foundations are connected only to the separately enabled outer-isolated adapter.
The repository still uses synthetic credential fixtures and does not install a production
service identity, credential, or identity provider.

## Implemented outer-isolated container and exact-host egress path

The provider-capable adapter is a distinct, single-use execution boundary. It accepts only
an `external-signed-mfa` authorization, a pinned local runner image identity, an exact
Codex release and native-binary hash, an exact entrypoint hash, a broker-owned internal
network, and one already-running pinned egress proxy. It rejects image, label, platform,
network-member, proxy-policy, resource, mount, environment, port, device, user, or
hardening drift before the credential is opened.

The runner starts with a read-only root filesystem, all Linux capabilities removed,
`no-new-privileges`, a non-root identity, bounded CPU/memory/process/runtime/output,
broker-owned temporary filesystems, no Docker socket, no host/user configuration, and no
direct external network. Its only network path is the internal bridge to the pinned proxy.
Worker DNS is disabled; the runner addresses the inspected proxy by its pinned private IP,
so only the proxy can resolve a provider hostname.
The proxy accepts only canonical HTTP `CONNECT` for the one policy-pinned provider host on
port 443, resolves and vets every address, rejects the complete answer set if any address
is private/local/reserved, connects to the vetted address to prevent DNS rebinding, keeps
TLS end-to-end, and bounds headers, concurrent tunnels, bytes, connect time, and idle time.
It never logs tunnel bodies. At startup the proxy verifies that the canonical mounted
policy hashes to the same value bound into its container environment and label; the adapter
rechecks that binding before credential projection. A turn cannot succeed until forced
container removal is independently confirmed, and a ChatGPT refresh is installed only
after that confirmation.

Pixel locally built the two exact pinned Debian images and exercised the runner offline,
read-only, non-root, without a network or credential. It also exercised the proxy
read-only, capability-free, non-root, and resource-bounded without provider access. The
malformed-frame image smoke test failed closed. These are local no-provider construction
checks, not published-image, supported-host, production-service, credential, or live-call
qualification.

Clean-build comparison initially caught timestamped package logs and randomly named
Codex version-probe helpers under root's home. The Dockerfiles now discard all such build
state. A digest-pinned BuildKit qualification builds each image twice from scratch with a
fixed source-date epoch and rewritten OCI timestamps; both pairs must be byte-identical.

## Implemented credential-free CLI qualification

The qualification-only adapter freezes the intended Codex CLI invocation before any live
authorization or credential work is added. It places a fixed instruction and the sanitized
capsule in one canonical JSON envelope on stdin, so no task content or plan hash enters
the process argument list. The CLI gets fresh private home, `CODEX_HOME`, temp, and empty
workspace directories; an empty `PATH`; sterile Windows account/domain variables; no API,
proxy, or inherited credential environment; file-only credential storage; ignored user
configuration and rules; ephemeral history; read-only sandboxing; and explicit denial of
shell, web search, browser, computer use, images, apps, plugins, hooks, goals, skills,
workspace dependencies, tool suggestions, and multi-agent features. Strict config makes
an unknown hardening key fail rather than disappear silently.

The qualifier accepts only an ordered, bounded JSONL transcript for one thread and one
turn. Reasoning and the final agent message are the only allowed items; a command, file,
MCP, web, error, extra message, unsupported field, malformed UTF-8/JSON, inconsistent or
missing usage receipt, post-completion event, output mismatch, byte flood, timeout, abort,
or nonzero exit fails closed. The final-message file must be a bounded single-link regular
file and match the transcript exactly. The process is killed as a group on POSIX and as a
tree on Windows, and the one-use runtime is removed.

This proves Pixel's command construction, environment minimization, process lifecycle,
event grammar, and fake-output boundary. It does **not** prove that a particular Codex
release honors every setting, that provider transport is destination-restricted, that a
credential is safely held, or that a live response has provider provenance. The qualifier
is deliberately not connected to `executeWorkCodexAuthorization`; passing an actual
Codex binary cannot create a valid production claim through this path.

## Billing boundary

The default example uses Codex CLI ChatGPT authentication and labels it
`chatgpt-plan-or-credits-not-api-billing`. Eligibility and limits depend on the configured
ChatGPT plan or workspace. This route does not claim that an API Platform key is covered by
a ChatGPT subscription.

API-key mode is a separate policy shape labelled `separately-billed-api-platform`. It
requires dated operator-supplied input/output rates and a worst-case micro-dollar ceiling.
The compiler rejects a plan above that ceiling. A price estimate is a local policy check,
not a provider invoice or guarantee.

## Security properties and limits

- No ambient config, rules, apps, plugins, skills, tools, network tools, multi-agent work,
  write sandbox, or external authority is represented in the plan.
- The future execution contract is ephemeral, read-only, tool-disabled, network-tool-
  disabled, structured-output-only advisory work. Provider transport itself necessarily
  requires tightly controlled network access.
- Provider output is untrusted. The output schema cannot grant execution, verification,
  merge, deployment, publication, messaging, purchase, production, policy, or completion
  authority.
- Deterministic redaction cannot recognize every novel name, trade secret, encoded value,
  or semantic disclosure. The data owner still decides classification and whether content
  mode is appropriate. When in doubt, keep the job local.
- Hashes bind exact local objects but do not make their contents safe. Private requests,
  mappings, approvals, and later results require owner-only storage and lifecycle controls.

## Remaining implementation gates

Before any real Codex turn, Pixel still needs a production external password/MFA issuer
with enrollment and recovery, deployment-owned service identities and credential
installation, deployment lifecycle for the private network/proxy/runner, published image
digests and supported-host image qualification, and an installed-runtime config/tool-
surface attestation. The complete product also still needs local critique and independent
verification, cache and exact-duplicate suppression, spend and quality circuits, crash
recovery and retention cleanup, content-free operator evidence, clean
install/upgrade/rollback/removal, and a separately authorized fixed synthetic live
qualification. None is implied by green synthetic MFA, custody, mock, fake-CLI, local
container, or no-provider proxy tests.
