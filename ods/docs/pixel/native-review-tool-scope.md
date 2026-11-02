# Native correction tool scope, ODS extension v1

This is an ODS compatibility extension to the source-pinned OpenClaw 2026.6.33
runtime. It is not a stock upstream hook or a new executor. Its structural
overlay is `plugin/native-review-tool-scope.d.ts`; it does not augment generated
SDK type aliases or change their repair authority.

The native `before_agent_finalize` event acknowledges support with
`nativeRetryToolsAllowVersion: 1` only when the repaired outer runtime supplied
an authenticated, live scope and the attempt captured its original admitted
tool origins. A version string or a plugin configuration flag is insufficient.
Without that event acknowledgment the Portal integration correction is not
eligible. Partial or unknown runtime bytes must not start a broader correction.

An admitted retry may include `retry.toolsAllow`: one to 32 unique literal leaf
names matching `[A-Za-z][A-Za-z0-9_.-]{0,127}`. Wildcards, groups, duplicates,
unknown names, ambiguous origins and empty intersections fail closed. Every
present constraint from the combined native hook candidates participates in
the intersection, including candidates whose own retry budget is exhausted.
It can never add a tool or override native owner, sandbox or provider policy.

The private scope belongs to the existing native run-context object and exact
run/session/key/agent/lifecycle identity. A helper-owned WeakMap and WeakSet
retain that custody; there is no registry keyed by model text. The first
policy-admitted concrete leaf set is the immutable origin ceiling. Legitimate
factory reconstruction uses stable native plugin provenance and tool name,
not JavaScript function identity. A newly allowed or substituted origin cannot
enter on a fallback. Caps meet monotonically and persist across provider
fallbacks sharing that native owner. A present invalid constraint poisons the
scope so a later fallback cannot return to the full inventory.

The original outer `params.toolsAllow`, prompt mode, skills, history, bootstrap,
model route and sampling settings remain unchanged. The separate private cap
is applied to native policy-filtered leaves before catalog construction and
again after client/forced additions. A correction exposes only the selected
direct adapters; old search/code catalogs are cleared and client tools cannot
be appended. The pinned SDK's one-to-one tool-definition adapter preserves
verified input/output descriptor references before native metadata is copied
to the projected definitions. Arbitrary names alone grant no provenance.
Captured direct tools and deferred children recheck current scope ownership,
cancellation and the cap before dispatch.

Normal first-pass arrays and executable objects are unchanged. If an uncapped
run changes native identity, the optional capability acknowledgment is
withdrawn; the ordinary path continues. An already restricted or poisoned
scope cannot be replaced by an unrestricted fallback. Existing no-replay,
side-effect, retry-budget and cancellation barriers retain precedence.

The ODS Portal correction requests the two existing integration adapters,
`pixel_ods_notes` and `pixel_ods_mcp`. Backend ReviewOnly enforcement remains
mandatory: reads and exact Ask proposals are permitted, while mutations,
notification delivery and approval consumption remain unavailable. A smaller
visible inventory is not authorization and does not prove improved model
behavior.

Qualification requires the actual repaired SDK: native hook normalization,
fallback custody, two direct final tool definitions, no client/forced/catalog
escape and closed late dispatch. Repair receipts must bind all three runtime
modules and their unchanged adapter dependencies. Existing repair backups and
prior exact byte variants remain preserved. Model benefit must be established
separately with a fixed paired experiment; this protocol alone makes no such
claim. Real macOS launchd, ACL, Seatbelt and reboot gates also remain pending.

An ordinary provider fallback retains the existing native run-context object.
Explicit clearance rejects already captured closures. A wholly new invocation
after an un-signaled context deletion can, however, create a new native object;
the WeakMap alone cannot prove that this is the old restricted execution.
The ODS layer therefore also gates the exact already-latched run at the existing
`before_agent_run` boundary. It requires immutable native
`nativeRetryToolsAllowState` v1 metadata reporting an active cap and exactly the
two actual final adapters. Missing, reset, invalid or inconsistent scope blocks
before inference. A fresh owner with a new native run id keeps the ordinary
path; a previous conversation's latch is not sufficient to block that new run.
This is layered ODS enforcement, not a claim that the stock SDK remembers a
deleted record.

In the pinned runtime, tool construction and `before_prompt_build` already
precede `before_agent_run`; the latter remains before prompt submission. The
extension adds metadata to that existing admission event and does not move
admission, add earlier I/O, alter model context or create another executor.

## Required first native integration attempt

The private structural experiment additionally supports literal
`retry.toolChoice: 'required'`, with a separate actual-event acknowledgment
`nativeRetryToolChoiceVersion: 1`. Cap-only v5 does not acknowledge this field.
The acknowledgment requires the reviewed boundary-aware native
`openai-completions` stream, the ODS `ods-gateway/ods/current` loopback relay
route, and the installed native wrapper. Custom provider streams, other APIs
remote base URLs and an explicit `compat.supportsTools: false` do not qualify. A nominal provider/API label alone is
not evidence of required-choice support. The protected repair's read-only
dependency hashes bind native stream resolution, boundary transport selection,
required-choice serialization/reconciliation and payload callback ordering.
The diagnostic-events dependency binds its earlier repair's canonical final
bytes, rather than pretending it is an unchanged upstream module.

Only the exact two Portal adapters, with their original native plugin origins,
can be combined with required choice. This selects neither an operation nor its
arguments. Model text still chooses the adapter, operation and arguments;
ordinary native hooks and host ReviewOnly authority still decide what is
permitted. Listing MCP capabilities or reading a note may satisfy required
choice without preparing the exact requested approval, so tool use by itself
is not a successful recovery.

Private phases are `armed`, `issued`, `tool-started`, then terminal closure.
Pre-issuance reconstruction retains `armed`. The first logical native stream
invocation reserves `issued` synchronously and uses the provider's supported
`toolChoice: 'required'` option. The final payload callback rejects a missing or
changed requirement and any inventory wider than the two native adapters.
It snapshots the native serialized tool descriptors and schemas before a prior
payload callback runs, rejects any callback changes to them and detaches the
accepted tool objects from callback-owned aliases before submission.
Authenticated current scope, abort state and the exact expected phase are
checked again after awaiting that callback, before any payload is accepted.
Cancellation, context replacement or closure during the callback prevents
submission, including for a later auto summary.
The wrapper captures the original API/provider/model/base-URL scalars and
cancellation signal. Both the construction/call identities and the native
callback's supplied model must still match after the await; a tool-support
opt-out or changed route refuses. Accessors for critical payload model/choice/
tools fields are refused, and detached return values retain the captured model
and validated choice rather than re-reading callback-owned critical fields.
An own `toJSON` serialization hook is also refused: otherwise the later native
OpenAI client could serialize a different model, choice or tool inventory.
This bounded callback-data contract is not process-global prototype isolation
from arbitrary privileged native code.
No prompt, description, history, template, seed or other sampling option is
changed. Provider-internal retries remain part of the same logical request.
If an outer fallback tries to infer again after issuance but before a native
tool attempt, it is refused; it cannot repeatedly force calls or downgrade to
auto. Unsupported APIs also refuse rather than silently dropping the field.

`tool-started` means entry into an authenticated, current, capped native tool
definition's `execute`, before delegating to the existing SDK admission and
adapter. This is an attempt, not proof of a backend effect. Pending, ready,
refused or unknown outcomes cannot rearm required choice. Later summary calls
use native auto with the same two-tool ceiling and ReviewOnly floor. An
explicit incompatible native tool choice (including `none` or a named-tool
restriction) is refused rather than overwritten or broadened, both before the
required request and after tool start. Normal first-pass options are untouched. Prose,
raw function markup, incomplete calls or schema rejection before native execute
entry do not consume the phase; another model invocation is then refused.
That fail-closed case can limit automatic error feedback; it does not trigger
an unbounded forced-call loop.

The existing before-inference gate also requires frozen, exact-run
`nativeRetryToolChoiceState` metadata `{version: 1, ownerRunId,
mode: 'required-first', phase: 'armed' | 'tool-started'}`. An issued, closed,
reset, missing or unsupported proof cannot authorize a fresh inference.
The original cancellation, unknown-outcome and current-owner fences win.
Fresh owner runs remain ordinary and use native default tool choice.

Mechanical qualification must prove normal 22-tool/auto behavior unchanged,
corrective two-tool/required serialization, an actual current native adapter
attempt, then two-tool/auto summary; typed policy refusals and unused grants
must remain intact. A real backend returning HTTP 200 is insufficient: a typed
native tool call and the exact policy outcome are required. The fixed paired
model experiment remains a separate gate, with failure retained as evidence.
The current prototype and planned model experiment target the configured local
Qwen 3.5 9B route through llama.cpp b11429 and the ODS loopback relay. This does
not establish behavior for every model behind `ods/current`, cloud backends or
macOS hardware. An explicit tools-disabled model may be rejected by the normal
production tool-policy admission even before any provider request; it must not
be relabeled as a successful recovery or have that policy relaxed for a test.

The native cancellation qualification also covers the existing controller's
prompt-lock reacquisition. A writer without a retained holder joining while
that same controller already has a pending reacquisition must await it and
recheck the retained holder,
rather than queue a second physical acquisition against its own lock. The
repair reuses that controller-local promise; physical exclusion, external
takeover checks, cancellation deadlines and lock ownership are unchanged.
It introduces no reentrant lock permission or forced-clear behavior. The full
Portal pre-header Stop case remains a required actual-native gate.

A separate stale-refresh guard captures that controller's fence generation and
fence reference before reading its snapshot. If a newer owned publication,
takeover or ended fence supersedes the read while it awaits, the old snapshot
cannot overwrite the authoritative fence. Ordinary refresh and all takeover
classification, trust and history-pruning checks retain their existing paths.
Actual guarded SessionManager and physical-lock regressions cover both races;
they do not establish the full Portal cancellation result by themselves.
