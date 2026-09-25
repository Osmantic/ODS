# Frontier Limb

The Frontier Limb gives Pixel an optional, policy-controlled second opinion from Codex
without turning Codex into Pixel's primary agent or giving it access to Pixel's files,
memory, source limbs, model endpoint, or machine tools. Pixel remains local-first. The
remote provider receives only a small typed capsule produced by a separate privacy
broker.

This can use either a saved ChatGPT-authenticated Codex CLI session for eligible
subscription access or an authorized OpenAI API key for separately billed usage. The
deployment must follow the applicable account, workspace, organization, retention,
residency, and usage policies. See OpenAI's current
[Codex authentication](https://learn.chatgpt.com/docs/auth) and
[non-interactive mode](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
documentation before deployment.

It is not a literal air gap: an approved sanitized capsule crosses the network. It is a
software egress boundary designed to make that crossing narrow, visible, reversible
before transmission, and independently enforceable.

## Scope and adaptive routing

The model-facing plugin exposes two typed submission shapes:

- `pixel_frontier_plan_review`: objective, assumptions, constraints, local findings,
  and acceptance criteria.
- `pixel_frontier_failure_triage`: normalized error class, bounded failure description,
  attempted fixes, constraints, and expected behavior.

Every v2 submission requires a versioned, content-free local receipt: a unique receipt
ID and timestamp, local-attempt count, enumerated local outcome, and one to four
enumerated reasons. The broker validates outcome/reason coherence and binds the receipt,
exact request, and policy to its routing decision. Receipt text is never model-authored,
contains no task prose, and is not sent inside the provider capsule. Older v1 requests
remain readable but are explicitly labeled `legacy-unreported` and do not gain adaptive
cache behavior.

The broker, not the local model, makes one of seven deterministic decisions:

| Decision | Effect |
|---|---|
| `local-only` | Accept the reported sufficient local result; create no plan and make no provider call |
| `local-retry` | Require another bounded local attempt; create no plan and make no provider call |
| `operator-context` | Ask locally for missing operator context; create no plan and make no provider call |
| `preview` | Publish the exact sanitized capsule locally; never transmit it |
| `propose` | Hold the exact sanitized capsule for an external exact-hash approval |
| `bounded-auto` | Execute only under a matching, budgeted grant or lease |
| `reject` | Fail closed for inconsistent receipts, policy violations, or unsafe content |

When work advances beyond a no-egress local decision, safety- or security-review reasons
always force `propose`, even when a bounded-auto grant matches. A quality circuit also downgrades bounded-auto routing after the configured
number of locally reported regressions or unusable results.

There is deliberately no generic prompt, file upload, repository, URL, message, or log
forwarder. The local model must first reduce the task to one of these structures. Codex
returns one schema-validated object containing a summary, bounded findings, risks, and
confidence.

## Boundary and data flow

```text
private local model
      |
      | typed request; no provider credential
      v
gateway-owned request spool
      |
      v
Frontier Broker (separate system identity)
      |-- validates the local receipt, shape, classification, and data categories
      |-- routes local-only/retry/context without compiling or transmitting
      |-- rejects restricted/never-egress/credential/quarantine content
      |-- replaces supported email, phone, private-IP, URL, and path identifiers
      |-- calculates token/cost estimates and an immutable plan hash
      |-- stops at preview or approval when policy requires it
      |-- deduplicates exact eligible capsules through a private bound cache
      v
one ephemeral, tool-disabled Codex CLI process
      |-- isolated API-key login or broker-private ChatGPT auth cache
      |-- no shell, web, image, app, hook, goal, or multi-agent tools
      |-- read-only sandbox and structured output schema
      v
Frontier Broker validates output and rehydrates only known placeholders
      |
      v
bounded advisory result visible to Pixel
      |
      | private local critique, verification, and final composition
      v
content-free integration receipt --> quality circuit and aggregate savings evidence
```

The gateway can write request/cancel files and read result/event projections. It cannot
read the provider credential or auth cache, policy, sanitized plans, approval records,
raw request archive, replacement map, private cache, integration archive, authority
state, or provider runtime. In API-key mode, the
credential file and its containing directory are root-owned, so the worker can read the
key through its group but cannot replace it. In ChatGPT mode, only the broker owns the mode-`0700` Codex
auth directory and its single-link mode-`0600` `auth.json`, because Codex must refresh
that cache in place. The root-owned parent remains non-writable. Codex receives neither
the gateway credential nor the local model credential.
It may also read one broker-produced `metrics/usage.json` projection. That file contains
only rolling job/token/status totals, routing decisions and attempts, cache/provider-call
counts, quality outcomes, estimated savings, usage separated by authentication/billing
mode, the current selected boundary, and remaining policy limits—never prompts,
identifiers, job IDs, credentials, local conclusions, or account details.

## Classification

Every request declares both a classification and data categories. The broker does not
infer business sensitivity from prose.

| Classification | Default behavior | Notes |
|---|---|---|
| `public` | policy preview/proposal/lease | Structural category only |
| `internal-derived` | policy preview/proposal/lease | Distilled source-derived, proprietary-code, or security structure may be permitted |
| `confidential` | exact-plan approval | Never eligible for automatic execution |
| `restricted` | reject | Never transmitted |

Mandatory never-egress categories include credentials, private keys, session tokens,
authentication material, raw source bodies, and regulated records. Personal identifiers and customer-confidential
material require `confidential` classification. Supported identifiers are replaced
locally, but names and novel identifiers cannot be perfectly recognized; the operator
must inspect the exact sanitized capsule before approving confidential work.

Secret detection covers known token/key formats, credential assignments, private-key
blocks, high-entropy tokens, and zero-width format-character evasion. Reserved
replacement placeholders and source-quarantine markers are rejected. These controls
reduce accidental disclosure; they are not a substitute for data governance or legal
review where regulated data is involved.

## Authority levels

- `disabled`: reject all submissions.
- `preview`: compile and store the exact sanitized plan; never call Codex.
- `propose`: compile and hold the job until an operator approves its exact SHA-256 plan.
- `bounded-auto`: available only through a matching standing grant or temporary lease
  for public/internal-derived work. It is constrained by task, classification, expiry,
  executions, input/output tokens, failures, and time window.

Confidential jobs and spillover requests carrying `safety-review` or `security-review`
always become proposals, even if a grant otherwise matches. A no-egress local retry or
operator-context decision may happen first. Stale/future request or receipt
timestamps, expiry, cancellation, policy drift, exact request changes, replay, duplicate
approval, cost/token/job budget exhaustion, and open failure or quality circuits fail
closed. Pause blocks new work; temporary lease IDs are single-use and revocable.

## Private cache and duplicate-spend prevention

Successful public or internal-derived advice may be cached only when policy enables it.
The key binds the exact sanitized capsule, normalized policy, provider kind,
authentication mode, model, output cap, and output contract. Confidential jobs,
approval-required classifications, forced safety/security review, and an open quality
circuit never read or populate the cache. Cache entries are broker-private, single-link,
mode `0600`, time-limited, size-limited, and fully output-validated before reuse.

Compilation and execution share a cross-process transaction. Exact simultaneous jobs
therefore serialize: the first eligible job may call the provider, while the second can
reuse the validated cache entry without a second spend. Pending proposals are checked
again after exact-hash approval and before that approval is consumed. A cache hit is
reported as `executionSource: frontier-cache`, `providerInvoked: false`, and a zero-usage
turn with separate estimated savings; it never impersonates a live provider call.

## Configure and install

The credential-free local page can generate either managed authentication policy and one
of three rolling 24-hour local safety profiles: Starter (5 reviews, 50,000 input tokens,
10,000 output tokens, 2 failures), Balanced (20/200,000/40,000/5), or Expanded
(50/500,000/100,000/10). It never accepts a credential or performs a provider login.
These are Pixel broker ceilings, not ChatGPT workspace controls or API Platform billing
controls. A private custom policy is terminal-managed; configure rejects a page preset or
authentication choice that conflicts with it.

Choose one explicit authentication mode in the Frontier policy. For a personal,
trusted local deployment that has eligible Codex subscription access, start from
`deploy/frontier-broker/policy.chatgpt.example.json`. Run `codex login` (or
`codex login --device-auth` on a headless host), verify it with `codex login status`,
then copy the resulting `auth.json` to a separate mode-`0600` input file outside the
repository. Treat this file like a password:

```json
{
  "frontierLimbEnabled": true,
  "frontierAuthMode": "chatgpt",
  "frontierBudgetProfile": "custom",
  "frontierPolicyFile": "/secure/client-config/frontier-policy.json",
  "frontierCredentialFile": "/secure/client-config/codex-auth.json"
}
```

The installer copies that cache into the isolated Frontier identity; it does not reuse
the gateway owner's ambient Codex home. ChatGPT-authenticated execution follows the
selected ChatGPT workspace's permissions, retention/residency controls, and eligible
Codex usage limits. Availability depends on the account and workspace.

For usage-based API access, keep `provider.authMode` set to `api-key` and provide the key
in a separate mode-`0600` file:

```json
{
  "frontierLimbEnabled": true,
  "frontierAuthMode": "api-key",
  "frontierBudgetProfile": "custom",
  "frontierPolicyFile": "/secure/client-config/frontier-policy.json",
  "frontierCredentialFile": "/secure/client-config/openai-api-key"
}
```

API-key execution is billed through the OpenAI Platform account at API rates; a ChatGPT
subscription does not pay that API bill. Existing v1 policies without `authMode` retain
`api-key` behavior for compatibility.

Frontier mode refuses a public primary-model URL. The local Pixel model must use
loopback, RFC1918, `.local`, or a hostname/IP explicitly listed in `modelPrivateHosts`.
An exact `modelPrivateHosts` entry is an operator attestation, not independent proof
that the endpoint is private; the generated deployment record distinguishes detected
private addressing from operator-attested addressing.
Review `deploy/frontier-broker/policy.example.json`, then run:

```bash
./pixel configure --answers /secure/client-config/onboarding.json
./pixel frontier-broker --confirm
./pixel plan
./pixel apply --confirm
./pixel verify
```

The installer validates the API key or private ChatGPT auth-cache shape, checks an
imported ChatGPT login with `codex login status`, validates the Codex executable and
structured-output/ephemeral configuration switches, and performs a synthetic loopback-only request to
verify the exact config and exposed tool manifest without spending tokens. It also
validates system identities, permissions, and gateway denial of private broker state.
The same offline qualification is an `ExecStartPre` gate, so every service start fails
closed if an in-place Codex change exposes an unapproved tool or invalidates strict
configuration.

The separate `./pixel frontier-live-qualify` workflow verifies the installed provider
edge without accepting arbitrary content. It creates a mode-`0600` short-lived consent
record, prepares a fixed synthetic public/structural capsule with forced exact approval,
and requires a second hash-bound `confirm ... --transmit` command before any provider
use. The authorization is single-use and permits one call, 12,000 accounted input tokens,
and 256 output tokens. API-key mode also requires pricing evidence no more than 31 days
old and consent covering the worst-case policy estimate, subject to a one-dollar hard
ceiling. See [FRONTIER-LIVE-QUALIFICATION.md](FRONTIER-LIVE-QUALIFICATION.md).

## Operator workflow

```bash
./pixel frontier-show frontier-...
./pixel frontier-approve frontier-... PLAN_SHA256 --confirm
./pixel frontier-usage

./pixel frontier-authority show
./pixel frontier-authority audit 100
./pixel frontier-authority grant grant.json 60 --confirm
./pixel frontier-authority revoke GRANT_ID --confirm
./pixel frontier-pause "incident containment" --confirm
./pixel frontier-resume "review complete" --confirm
```

Inspect the capsule itself, its classification/categories, provider/model, token cap,
policy hash, expiry, and plan hash. Approval authorizes that one cloud transmission; it
does not authorize any action recommended by the result.
`frontier-approve` must run as the non-root deployment owner in a real terminal with
password-backed sudo. It clears cached authentication, requests fresh administrator
authentication, displays the complete protected plan, requires a one-time hash-bound phrase, and
clears the timestamp again on normal exit. The protected plan is rendered as
ASCII-escaped JSON so control and directionality characters remain visible text.
Non-terminal and passwordless-sudo attempts fail
before broker approval. This human boundary supplements, and never replaces, the
broker's exact plan and policy revalidation.
The result's `sanitizedPreview` is the exact capsule—not a summary—when the decision is
`preview` or `propose`. The routing receipt explains why the local model requested
spillover. After a successful live or cached result, the local model must verify and
critique the advice, compose the final answer locally, and call
`pixel_frontier_finalize`. The returned local conclusion and verification notes stay in
the gateway turn; only the exact result hash, a salted local-output commitment,
finding-index disposition, verification count, verdict, and quality label cross into the
broker's integration archive. The commitment salt remains local so short private text
cannot be tested against the projected hash. A remote response is never
the final answer merely because it passed its schema.

`frontier-usage` shows the current rolling aggregate without exposing individual task
content. It separates actual provider calls from cache hits and avoided calls. It reports
a currency estimate only when the operator supplies a dated metered-rate source; ChatGPT
subscription mode is reported as subscription usage, and unavailable pricing stays
explicitly unavailable rather than becoming a fabricated zero-cost estimate.

For an already configured private schema-v2 custom policy, the local page can draft an
exact 15-minute budget proposal. It returns no policy path or credential and has no apply
or activation endpoint. The trusted terminal command is:

```bash
./pixel frontier-budget apply --proposal-id frontier-budget-... --proposal-hash SHA256 --confirm
```

The hash also binds the browser-hidden canonical policy path. Application rechecks the
current private onboarding and source-policy bytes, writes an exact owner-only backup,
and changes only the `budgets` object. It neither configures nor activates the generated
broker policy and cannot call a provider. ChatGPT custom budgets keep cost null under
subscription mode. API-key custom budgets require a dated metered private policy and an
explicit estimated-cost ceiling; policies whose price is unavailable must be edited and
reviewed wholly in the terminal. A durable pre-edit claim lets the same exact command
finish an interruption by recognizing only the prior or exact updated policy bytes; an
expired unclaimed proposal still fails.

## Cost and token behavior

Local reasoning, retrieval, summarization, retry, operator-context collection, final
critique, and ordinary tool work consume no Frontier tokens. Only an executed sanitized
capsule and structured provider response count toward actual Frontier usage. Per-task
byte/token limits, a rolling global budget, per-grant budgets, exact-request deduplication,
and observed Codex usage receipts make spillover measurable. Local-only decisions and
cache hits record avoided-call estimates separately from actual usage.

`provider.cost.mode` is one of `unavailable`, `subscription`, or `metered`. Metered mode
requires operator-supplied USD micro-rates per million input/output tokens, a source, and
an as-of date, plus a rolling `maxEstimatedCostMicros` ceiling. The estimate uses the
bounded capsule input and requested maximum output and is checked before execution; it is
not a provider invoice. Subscription mode never pretends the subscription has a per-call
price. Provider-side limits remain the outer cost control.

A failure circuit stops repeated paid calls after the configured threshold. A successful
turn must include a valid Codex usage receipt. Failed or cancelled turns without one
conservatively charge the task's full input allowance and the request's full output
allowance locally. The Codex CLI does not
expose a server-side hard output-token switch for this workflow, so a provider attempt
can still be billed above the requested cap before the broker rejects and accounts for
it; provider-added input overhead or CLI-managed retries can also exceed the capsule
estimate. In `api-key` mode those tokens are separately billed API usage; in `chatgpt`
mode they consume whichever eligible Codex allowance or credits apply to the selected
ChatGPT plan/workspace. Current Codex reserves its built-in OpenAI provider configuration, so Pixel
does not override its retry transport under strict config. Use small caps, short
timeouts, and provider-side project budgets as the outer cost circuit.

## Testing and limitations

`tests/test_frontier_broker.py` covers the seven routing decisions, receipt coherence,
exact preview/proposal/approval, rehydration, never-egress and secret rejection, cost
ceilings, cache binding/tamper/expiry, simultaneous duplicate suppression, approval-time
deduplication, local integration binding/replay, quality downgrade, hostile output,
cancellation, policy drift, leases, pause/resume, and a fake Codex CLI contract.
`tests/frontier-finalize.test.mjs` verifies that local conclusions and verification text
never enter feedback receipts. The `security-evals/frontier-pressure/` suite adds the
privacy compiler, invalid-receipt, routing-matrix, dedup-binding, and persisted
load/replay/projection cases. Clean-room
release tests verify disabled-limb omission, identity isolation, and deployment rollback.

Current Codex releases retain `request_user_input`, `update_plan`, and `view_image` as built-ins after the
configurable shell, web, browser, computer, image-generation, app, plugin, hook, goal,
workspace-dependency, elicitation, and multi-agent surfaces are disabled. Installation
fails if any other tool appears. Non-interactive `exec` has no reply or approval channel,
and the capsule supplies no local path: path-like content is replaced locally, `ProtectHome` and inaccessible
share paths constrain the service, and all broker private state is text rather than image
content. Requalify this exact surface on every Codex upgrade.

Live provider qualification still requires deployment-owned authorization and an already
configured private saved ChatGPT Codex login or API key. The product workflow permits only
its fixed synthetic capsule and emits a content-free receipt. Its private immutable claim
makes an exact confirm retry recover or fail inconclusively without starting a second
provider turn. A fake-adapter or network-disabled pass proves the local boundary and CLI
contract, not OpenAI account availability, billing, model entitlement, retention, or
regional policy; only a deployment-owned `pass` receipt closes the live gate.

The configured Codex executable is part of the trusted computing base. Its model-facing
tool manifest is qualified and its filesystem view is constrained, but the service needs
provider network access; a malicious executable or host kernel is outside this software
boundary. Pin, verify, and requalify the CLI, and add a deployment egress proxy/firewall
when policy requires destination-level network enforcement.

The gateway can fill its writable Frontier request/cancel projections even though the
broker will reject malformed or over-quota work. Put the Frontier state on a monitored,
quota-limited filesystem (or an equivalent service-level storage limit) so a compromised
gateway cannot exhaust the host disk. The private routing ledger also has explicit byte
and record ceilings and fails closed when either is reached; monitor that state before a
long-running deployment reaches the ceiling.
