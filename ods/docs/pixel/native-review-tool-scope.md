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
