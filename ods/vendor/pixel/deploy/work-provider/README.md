# Pixel work-provider plane

This directory defines an additive, closed provider registry and lossless wire adapters for
OpenAI Responses, Anthropic Messages, OpenAI-compatible Chat Completions, and Pixel's existing
local OpenAI-compatible route.

The local profile remains the only profile enabled by default. Every remote profile is public
capability metadata only until an owner-private policy explicitly enables its exact identifier,
credential custody, exact-host egress policy, and budgets. Profiles never contain credentials or
mutable pricing claims.

The normalized transcript preserves provider state without exposing it as visible assistant text.
The remote reasoning lanes replay their protocol-guaranteed reasoning state verbatim for continued
multi-turn tool use: Moonshot Kimi preserves the returned non-empty `reasoning_content` inside the
complete assistant message, OpenAI preserves the returned `reasoning` output items, and Anthropic
preserves the `thinking`/`redacted_thinking` content blocks. OpenRouter is provider-dependent: its
adapter never guarantees `reasoning_content`, so the OpenRouter lane replays the complete returned
assistant message (role plus tool calls) without claiming preserved reasoning. The local adapter
continues to reject `reasoning_content`; the remote reasoning rules do not weaken the existing
local hidden-reasoning boundary.

This layer only translates and validates requests and responses. It does not itself open a
network connection, read a credential, call a provider, run a tool, authorize an external effect,
deploy a release, or authorize security testing.

The initial Chat Completions surface is intentionally text-and-function-tool only. Provider audio,
assistant annotations, Azure filter metadata, and other unqualified response extensions fail
closed instead of being silently discarded. OpenAI Responses results must also be terminal;
`queued` and `in_progress` are not converted into a completed assistant turn.

`qualification-runner.mjs` bootstraps an exact semantic qualification across a closed lane set —
`local-only`, `moonshot-kimi`, `openai`, `anthropic`, and `openrouter` — through a distinct
`qualification-trial` run binding (never a connectivity-smoke probe). It binds one versioned
neutral corpus (`neutral-corpus.mjs`, `corpusVersion 8`) with exact case/request hashes,
exercises only exact corpus requests, and grades each deterministically. Model selection is
explicit and closed per lane: `local-only` is qualification-pinned to `DeepSeek-V4-Flash-0731`;
`moonshot-kimi`, `openai`, and `anthropic` are fixed to their profile defaults (`kimi-k3`,
`gpt-5.6`, `claude-sonnet-4-5-20250929`); and `openrouter` is owner-pinned to an exact owner-private
policy model, never a placeholder or `auto`. Every required case is repeated independently under
a closed `--trials 3|4|5` integer (default 3, minimum 3, maximum 5) with a distinct idempotency
key per trial and request; a qualification is emitted only when every repetition of every case
passes.

The fixed corpus aligns one deterministic case to each router task class the capability
attests — `structural-review`, `patch-proposal` (a one-file disposable diff), and
`failure-triage` — plus the `tool-choice-continuation`, true `long-context-sentinel`, and
`output-capacity` controls. The `structural-review` case embeds a small neutral module and
requires the model to derive exact facts from its structure (export count, defective function,
defect kind); the `failure-triage` case embeds a fixed neutral log and requires the model to
derive an exact classification/resolution/retry result from the log. Neither supplies its
answer, and the deterministic grader parses and compares the expected structured facts exactly.
The `tool-choice-continuation` case is a genuine two-request exchange: request 1
presents fixed neutral tool definitions and must independently cause the provider to choose
`edit_file` with exact bounded arguments; request 2 is built from the provider's exact returned
assistant message/provider state plus a fixed synthetic tool result, and requires a strict
JSON semantic confirmation of both the selected tool and observed result. The local replay
explicitly disables hidden reasoning so the continuation budget is usable output. The
`moonshot-kimi`, `openai`, and `anthropic` lanes require their protocol-guaranteed reasoning
state (Kimi `reasoning_content`, OpenAI `reasoning` items, Anthropic thinking blocks) to be
returned and then preserved verbatim in the replay; the `openrouter` lane replays the complete
returned assistant message (role plus tool calls) and never claims preserved
`reasoning_content`. The corpus never fabricates a prior tool call or reasoning content.

The `long-context-sentinel` case proves retrieval: the unique sentinel appears exactly once near
the beginning, the end instruction refers to it without repeating its value, and the provider
must report at least `LONG_CONTEXT_MIN_INPUT_TOKENS` (32768) input tokens for every repetition
so the estimator alone is insufficient. The final reply must be byte-exact (no trimming).

The `output-capacity` control proves output headroom: the provider must emit only the fixed
neutral word at or above the word floor while independently reporting at least
`OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS` (512) output tokens for every repetition. Promotion derives
`outputTokenMax` as the minimum reported output-capacity count across every passing repetition,
capped at 512 — never the small maximum of the short semantic cases.

Evidence binds every request in each trial with a content-free per-request array (request index,
exact input SHA-256, terminal state, reported input/output tokens, and the exact provider-reported
`responseModel`), and the schema validates every SHA-256 including `reasoningContentSha256`.
Every local/OpenAI-compatible and remote transport fails known when the returned provider model
does not exactly equal the pinned run model; a missing model also fails known for qualification
and semantic execution. Provider aliases and `auto` are never accepted.

The runner persists the initial ledger and every claim before invocation plus each terminal
transition in an owner-private run store, and never persists prompts, responses, reasoning, tool
arguments, or credentials. Only a run where every required repetition passes and the ledger is
closed emits a sealed semantic-capability router document via `qualification-promotion.mjs`
(`work-provider-router-qualification-v1`, `attestationKind: semantic-capability`), with a
conservative content-free capability envelope (no vision, standard tools, workspace context),
bounded expiry, and deterministic conservative pricing (local zero; Moonshot sealed conservative
budget-rate constants; and OpenAI/Anthropic/OpenRouter no invented per-token rate, requiring an
exact owner-private policy `pricing` binding whose absence makes promotion fail closed rather
than emit a live price claim). Promotion does not trust `status`:
it revalidates the complete sealed attestation invariants against the closed run ledger (all
repetitions present and passed, every expected task/trial present once, every request succeeded
with exact `responseModel`, no null token counts, `evidenceSha256`, and the matching ledger run
ID/SHA/provider/model/binding/counters), so a tampered qualified attestation fails closed and
never emits a router qualification. Failed and uncertain trials emit
no semantic qualification. An uncertain case is non-retryable and non-qualifying, and the ledger
is closed only when all requests are known terminal. Lane selection is explicit (`--lane
local-only|moonshot-kimi|openai|anthropic|openrouter`) with no silent fallback; every remote
lane's host testing accepts only the closed `--proxy-route loopback|container` enum.

```bash
node deploy/work-provider/qualification-runner.mjs \
  --lane local-only --corpus-version 8 --trials 3 --ledger-root /abs/private/ledger \
  --report /abs/private/report.json \
  --qualification-report /abs/private/qualification.json

node deploy/work-provider/qualification-runner.mjs \
  --lane moonshot-kimi --corpus-version 8 --trials 3 \
  --policy /abs/private/policy.json --sha256 <hex> \
  --credential /abs/private/moonshot-kimi-key --proxy-route loopback \
  --ledger-root /abs/private/ledger --report /abs/private/report.json \
  --qualification-report /abs/private/qualification.json

node deploy/work-provider/equivalence-runner.mjs \
  --lane local-only --ledger-root /abs/private/equivalence-ledger \
  --qualification-report /abs/private/qualification.json \
  --qualification-sha256 <artifactSha256> \
  --report /abs/private/equivalence-report.json
```

Report and qualification paths must be distinct owner-private paths. They are created once with
mode `0600`; existing files and symlinks are rejected rather than overwritten. Equivalence reads
the qualification artifact through bounded no-follow custody and strict duplicate-key-rejecting
JSON before validating its hashes and promoted router qualification.

The conservative `private-policy.example.json` is suitable for connectivity smoke and small
bounded jobs, not a complete five-trial semantic campaign. Corpus version 8 reserves 35 requests
at `--trials 5` (seven requests across the six cases per trial) and estimates about 289000
initial input tokens before provider-state replay. The reserved output budget at `--trials 5`
depends on the lane's maximum output headroom: 33280 for `local-only`, 43520 for
`moonshot-kimi`/`openai`/`openrouter`, and 61465 for `anthropic` (whose extended-thinking
protocol requires a `max_tokens` strictly above the per-effort thinking budget). Because
`openrouter` is owner-pinned, its exact input-token estimate varies with the owner-private model
identifier. Use explicit owner-private ceilings of at least 40 requests, 500000 input tokens, and
65000 output tokens (plus the existing cost, network-byte, and timeout ceilings) to cover every
lane's complete five-trial campaign. The runner still checks cumulative measured usage before
every claim and fails closed if any owner ceiling is reached.

The production runner resolves transports only through the closed registry and cannot be given a
caller-supplied transport. Hermetic tests inject a transport through the explicit
`workProviderQualificationTestSeam` factory, which the static release contract forbids from being
used in production code paths.

The local vLLM adapter preserves the same strict remote response contract after a narrow local-only
compatibility step. It accepts only the reviewed null-valued vLLM metadata fields, discards the
provider's private `reasoning` string before normalization, and rejects non-null metadata or remote
`reasoning_content`. The production local transport is fixed to `127.0.0.1:8000`; only its frozen,
named test export can inject a hermetic exchange. Once a complete HTTP response is received, strict
JSON, adapter, model, usage, or identifier failures are terminal `failed-known` outcomes rather
than ambiguous network outcomes.

## Owner-private credential custody filename binding

`install-owner-test-key.sh --provider <id> --directory <dir>` delegates to the hardened Node
ingress, which atomically creates an owner-only credential at a provider-specific closed name:
`moonshot-kimi-key`, `openai-key`, or `anthropic-key`. The owner-private policy schema
(`work-provider-private-policy-v1.schema.json`) binds each provider to exactly its own
provider-specific filename, so an Anthropic policy can only consume `anthropic-key` (and never
`openai-key` or `moonshot-kimi-key`). Cross-provider filename confusion is rejected at policy
validation time, before any custody read.

Legacy compatibility: an ingress provider may still author `fileName: "provider-key"`, matching
pre-4.3.1 owner-private deployments that placed the key at a single shared `provider-key` file.
This deliberate path is preserved only because it cannot be confused with a provider-specific
ingress credential (the ingress never writes `provider-key`). The Moonshot container launcher
requires exactly one of `moonshot-kimi-key` or `provider-key`, passes only that closed, non-secret
filename to the worker, and lets the bound policy and custody layer reject any mismatch. New
deployments use `moonshot-kimi-key`; the legacy name remains usable during migration.

Migration: after re-installing a provider credential through the ingress, set the policy
`credentialCustody.fileName` to the provider-specific name (for example `anthropic-key`), bind the
credential directory containing that file, and re-run the provider smoke/qualification. Owner
deployments that continue to rely on a manually placed `provider-key` may keep `fileName:
"provider-key"` unchanged; only that legacy name and the provider's own ingress name are accepted.
