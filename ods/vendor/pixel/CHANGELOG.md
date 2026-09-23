# Changelog

## 4.3.23 - Unreleased (candidate)

- Preserve one exact terminal failed rollback outside the bounded staging namespace so
  production can recover a single candidate slot without deleting failed evidence.
- Bind full content manifests, active services/version, the live rollback marker, fixed
  same-filesystem destination, and eight-to-seven capacity proof into an interruption-safe
  single-use archive transaction.

## 4.3.22 - Unreleased (candidate)

- Verify a single policy-valid sandbox that stopped at a recognized lifecycle boundary
  after a controlled reboot while preserving the warning that live runtime isolation
  still requires a fresh tool turn.
- Fail closed on ambiguous stopped states, restart-enabled containers, confinement drift,
  duplicates, and all unrecognized exit codes.

## 4.3.21 - Unreleased (candidate)

- Carry the exact hash-bound activation deployment record into a fresh reactivation
  source, preserving deterministic release bytes when deployment inputs are unchanged.
- Bind the activation record, retained deployment-input manifest, and retained install
  manifest into the single-use reactivation claim without weakening strict tree equality.

## 4.3.20 - Unreleased (candidate)

- Reclaim bounded staging capacity from a terminal rolled-back update after the active
  Pixel version has safely advanced beyond the version restored by that old journey.
- Refuse cleanup while the candidate is active or the host has regressed below the
  restored version, and bind the observed active version into the immutable cleanup
  claim and audit tombstone without weakening legacy receipt validation.

## 4.3.19 - Unreleased (candidate)

- Reconcile an exact owner-private semantic-acceptance staging twin when a concurrent
  caller observes the committed record before the publisher unlinks that twin.
- Continue rejecting unrelated hardlink aliases, malformed staging names, changed
  inodes, non-private files, and link counts outside the one interrupted-publish shape.

## 4.3.18 - Unreleased (candidate)

- Reuse the canonical generation timestamp when every material deployment input is
  unchanged, so activation and reactivation reproduce the same immutable release tree.
- Refresh that timestamp when a material deployment input changes, retaining a visible
  and deterministic audit signal without weakening exact-release adoption.

## 4.3.17 - Unreleased (candidate)

- Make the reviewed OpenClaw checksum manifest independent of the planner's absolute
  source location, so activation and reactivation build the same canonical release tree.
- Exercise the real planner from two distinct absolute roots, require every reviewed
  plan artifact to remain byte-identical, and retain a tamper-refusal negative control.

## 4.3.16 - Unreleased (candidate)

- Build retained releases without bytecode and verify them as an exact complete tree,
  with the release digest also rejecting extended attributes so immutable custody
  covers file metadata as well as bytes.
- Record a durable pre-mutation marker before reactivation can change live state.
- After a terminal failure that provably left the restored release active with no
  rollback marker or rollback claim, authorize an explicit new hash-bound reactivation
  attempt without executing candidate code.

## 4.3.15 - Unreleased (candidate)

- Record a terminal `job-finished` event when an owner-confirmed Operations job is
  cancelled before execution, keeping the durable event stream consistent with the
  terminal cancelled result without running the planned effect.

## 4.3.14 - Unreleased (candidate)

- Expose the Web Courier projection in rendered agent allowlists, including web-only
  source-broker deployments, so the registered pixel_web_browse tool is callable live.
- Add the agent-facing Web Courier projection: pixel_web_browse routes through the host Web
  Courier and exposes the rendered text projection to the agent.
- Ship complete provider runtime closure in the release, with the full provider schema and
  runtime surface included.
- Reconcile installed operations policy against the broker, binding the validated policy
  digest to plans and inventory.
- Make the raw-shell cwd optional, delegating to the broker default while retaining the
  approval and allowed-root gates.

## 4.3.13 - Unreleased (candidate)

- Make generated deployment inputs independent of the absolute source extraction
  directory by removing unused Operations and Frontier policy-source metadata.
- Keep the real Frontier credential source, generated policy bytes, enabled-broker
  validation, and fail-closed policy and billing boundaries unchanged.
- Add a two-location clean configure regression that compares the complete generated
  environment and deployment tree while holding observational time constant.

## 4.3.12 - Unreleased (candidate)

- Make Web Courier virtual-environment builds reproducible across independent staging
  roots and fail closed if any transient path survives in file content or a symlink.
- Persist bounded, owner-private, content-addressed Builder failure diagnostics so a
  failed durable goal remains actionable without widening its success envelope.
- Extend the existing release cleanup transaction to terminal no-mutation activation
  failures, preserving authenticated audit tombstones and preventing candidate reuse.

## 4.3.11 - Unreleased (candidate)

- Reissue the already-qualified 4.3.10 functional tree under a new immutable release
  identity after a legacy 4.3.3 controller safely consumed the first 4.3.10 activation
  claim without changing the live deployment.
- Keep release-update authority and transaction semantics unchanged. The Tower2 canary
  runbook now establishes the signed candidate's target bootstrap prerequisites before
  asking the retained pre-4.3.6 controller to consume the activation claim.

## 4.3.6 - Unreleased (candidate)

- Establish the candidate's exact sandbox image during the single-use activation transaction,
  after private configuration and before planning, so a clean forward update cannot consume its
  activation claim merely because the target image did not already exist.
- Preserve the active sandbox image for offline rollback through the pinned bootstrap path and
  record bootstrap failures as content-free, pre-deployment activation failures.

## 4.3.5 - Unreleased (candidate)

- Transactionally update every already-installed, enabled source, Operations, and Frontier
  broker executable so the privileged runtime bytes match the active immutable release.
- Preserve and hash-bind the exact prior broker bytes during apply, restore them during failed
  apply compensation or explicit rollback, and fail closed when recovery cannot be proven.
- Make Web Courier policy refusal a distinct nonzero result and require its exact refusal
  envelope during verification, preventing refused or misleading content from appearing successful.

## 4.3.4 - Unreleased (candidate)

- Reconcile the Operations Broker inventory projection every 30 seconds so expired authority
  lease IDs disappear without requiring an unrelated grant, revoke, pause, or resume operation.
- Serialize periodic inventory refreshes with authority mutations while leaving execution-time
  authority checks unchanged and fail-closed.

## 4.3.3 - Unreleased (candidate)

- Preserve bounded Builder work when a model completes without a usable final report, while
  continuing to reject zero-change completion and keeping the stricter final-report contract for
  the other work profiles.
- Wait for a quiescent, identity- and budget-matched model receipt before settling Builder work,
  closing the observed race where the controller inspected a receipt while inference was still
  active.
- Retain bounded, schema-constrained Scout failure diagnostics so a failed child can explain its
  failure without widening the accepted success envelope or claiming completion.
- Accept reviewed systemd template units in Builder workspaces and tolerate Docker 29's absent
  network during exact cleanup, without weakening path, authority, or cleanup verification.

## 4.3.2 - Unreleased (candidate)

- Extend the closed qualification-trial lane set from `local-only|moonshot-kimi` to
  `local-only|moonshot-kimi|openai|anthropic|openrouter` and bind the neutral corpus to
  version 8. Each lane keeps an exact, closed model-selection mode: `local-only` is
  qualification-pinned, `moonshot-kimi`/`openai`/`anthropic` are fixed to their profile
  defaults (`kimi-k3`, `gpt-5.6`, `claude-sonnet-4-5-20250929`), and `openrouter` is owner-pinned to an
  exact owner-private policy model (never a placeholder or `auto`).
- Add exact replay semantics for the reasoning lanes. Moonshot Kimi preserves the returned
  non-empty `reasoning_content` verbatim; OpenAI preserves the returned `reasoning` output
  items; Anthropic preserves the `thinking`/`redacted_thinking` content blocks. OpenRouter is
  provider-dependent and never claims preserved `reasoning_content`: it replays the complete
  returned assistant message (role plus tool calls) without inventing a reasoning claim.
- Require an exact owner-private `pricing` binding for OpenAI, Anthropic, and OpenRouter
  semantic promotion. These lanes carry no invented per-token rate: promotion fails closed
  (emits no router qualification) when the private policy omits `pricing`, while `local-only`
  stays zero-priced and Moonshot uses its sealed conservative budget-rate constants.
- Give the Anthropic extended-thinking lane the required `max_tokens` headroom strictly above
  its per-effort thinking budget so the sealed reasoning effort is never silently dropped, and
  keep the local lane's raw-diff/hidden-reasoning boundary unchanged.
- Keep all remote providers disabled by default and lane selection explicit with no silent
  fallback. The Anthropic candidate lane has now completed a real exact-model 18/18 neutral
  qualification and 3/3 routed semantic-equivalence run through owner-private custody and
  proxy-only egress. It is not part of the live release until the candidate is reviewed,
  merged, and deployed; real qualification of OpenAI and OpenRouter is still pending.
- Normalize one exact whole-response `json` fence before strict duplicate-key-aware parsing in
  provider qualification and the neutral harness. The deterministic rubric still requires the
  exact expected fields and rejects prose, malformed JSON, duplicate keys, or semantically wrong
  fenced output; this closes the Anthropic structured-response portability gap without changing
  the sealed neutral corpus.

## 4.2.0 - Unreleased

- Add a closed multi-provider execution substrate for local OpenAI-compatible models,
  OpenAI Responses and Chat Completions, Anthropic Messages, Moonshot/Kimi, Together,
  Fireworks, Groq, and OpenRouter. Remote providers are disabled by default and require an
  exact owner-private provider policy, credential custody, cost and token ceilings, and an
  exact-host egress proxy; credentials never travel through environment variables, argv,
  logs, receipts, or worker-visible configuration.
- Add three explicit routing modes: independent `local-only`, exact `explicit-provider`, and
  deterministic `policy-router`. Policy routing is local-first and may select a remote only
  when a current semantic qualification proves the local model cannot satisfy the declared
  envelope and the exact remote can. There is no silent cloud fallback.
- Bind local and remote qualifications to semantic task classes, context/tool/vision/token
  envelopes, expiry, evidence hashes, and conservative current pricing. Connectivity smoke
  evidence never qualifies semantic work. Confidential, restricted, and every declared
  sensitive category force local execution or rejection in this release.
- Add content-free, exactly-once provider ledgers and decision receipts. An uncertain provider
  outcome cannot be retried or rerouted until explicit reconciliation, and neither routing nor
  provider evidence grants merge, deploy, publication, external-message, credential, network,
  or security-testing authority.
- Prove a real bounded Moonshot Kimi K3 transport call under isolated credential custody and
  proxy-only egress. The connectivity receipt does not claim K3 coding or general-agent
  qualification; that remains a separate gated campaign.
- Add a sealed neutral qualification-trial runner that bootstraps an exact semantic
  qualification for local DeepSeek-V4-Flash-0731 and Moonshot kimi-k3 under a distinct
  qualification-trial run binding. It binds one versioned neutral corpus with exact case/request
  hashes, admits transports only for exact corpus requests, and repeats every required case
  independently under a closed `--trials 3|4|5` integer (default 3) with distinct idempotency
  keys per trial and request; it qualifies only when every repetition of every case passes.
- Make the tool-choice-continuation case a genuine two-request exchange: request 1 presents
  fixed neutral tool definitions and must independently cause the provider to choose `edit_file`
  with exact bounded arguments; request 2 is built from the provider's exact returned assistant
  message/provider state plus a fixed synthetic tool result and requires a strict semantic JSON
  observation. The local continuation disables hidden reasoning for deterministic usable output;
  K3 preserves the returned non-empty `reasoning_content` verbatim in replay. The corpus never
  fabricates a prior tool call or reasoning content.
- Align fixed deterministic corpus cases to each attested router task class
  (`structural-review`, `patch-proposal`, and `failure-triage`) while keeping the
  `tool-choice-continuation` and true `long-context-sentinel` controls; only exercised task
  classes are attested.
- Make the long-context case prove retrieval: the unique sentinel occurs exactly once near the
  beginning, the end instruction refers to it without repeating its value, every repetition must
  report at least 32768 provider input tokens (the estimator alone is insufficient), and the
  final reply must be byte-exact (no trimming).
- Bind evidence to every request in each trial with a content-free per-request array (request
  index, exact input SHA-256, terminal state, and reported input/output tokens for requests 1
  and 2), and validate every SHA-256 including reasoningContentSha256; no prompt, response,
  reasoning, tool argument, or path enters evidence.
- Add a sealed, deterministic promotion step (`qualification-promotion.mjs`) that emits a
  `work-provider-router-qualification-v1` semantic-capability document only when every required
  repetition passes and the ledger is closed, binding evidenceSha256 to the complete trial
  attestation with a canonical qualificationSha256, a bounded expiry, and a conservative
  content-free envelope (no vision, standard tools, workspace context) that can drive the
  production router. Failed and uncertain trials emit no semantic qualification.
- Make promotion pricing conservative and deterministic: local pricing stays zero while
  satisfying the per-run capability schema minimum, and Moonshot uses an explicit closed
  conservative budget-rate constant that never exceeds the owner private-policy ceilings.
- Rename qualification counters (`distinctCaseCount`, `requiredRepetitions`,
  `passedRepetitions`, `failedRepetitions`, `uncertainRepetitions`) so distinct cases, repeated
  trials, and request counts cannot be misread, and remove unused attestation constants.
- Close the uncertain-evidence loss: every attempted request is appended exactly once to the
  per-case evidence before halting later trials/cases, so an uncertain first/second/non-tool
  request is never omitted from the attestation. Uncertainty preserves one attempt, no
  automatic retry, ledger status uncertain, no ledger close, and `semanticQualification: null`.
- Capture and enforce the provider-reported response model: normalized adapter output binds a
  validated, bounded `providerModel` from the top-level OpenAI chat, OpenAI Responses, and
  Anthropic Messages response model. Local/OpenAI-compatible and all remote transports fail
  known when the returned model is not exactly equal to the pinned run model, and a missing
  model also fails known for qualification and semantic execution; provider aliases and `auto`
  are never accepted. The exact `responseModel` is bound into content-free per-request evidence.
- Replace the nominal structural-review and failure-triage prompts with genuine deterministic
  semantic fixtures: structural-review embeds a small neutral module and requires exact facts
  derived from its structure, and failure-triage embeds a fixed neutral log with enough evidence
  to derive an exact classification/resolution/retry result. Neither supplies its answer; the
  deterministic grader parses and compares the expected structured facts.
- Add an `output-capacity` control that requires at least 512 fixed neutral words and at least
  512 provider-reported output tokens while staying semantically exact and content-free in
  evidence. The local control disables hidden reasoning so reported completion capacity is
  visible usable output. Promotion `outputTokenMax` is the minimum reported output-capacity
  count across all passing repetitions capped at 512, never the small maximum of the short
  semantic cases; any repetition below either floor or containing another word fails. Total
  requests at `--trials 5` remain within the public profile maximum of 40.
- Version the repaired corpus as version 4 and give K3 a bounded 4096-token output-capacity
  ceiling so preserved-thinking variance cannot consume the 600-word proof, while the complete
  five-trial campaign remains within the owner policy's 50000-token output budget.
- Tighten the K3 patch-proposal prompt to K3's documented guidance (role, explicit task,
  structural output example, and exact byte/delimiter constraints) so it reliably emits one
  clean one-file diff without embedding the expected diff. Use K3's supported `high` reasoning
  effort for this strict construction task; the strict disposable verifier is unchanged.
- Replace K3's free-form patch-proposal response with exactly one schema-bounded
  `submit_patch({ patch })` tool call. Kimi's supported `tool_choice=auto` is paired with one
  available tool and an explicit mandatory-use prompt; a shared strict extractor rejects zero, multiple,
  wrong-name, malformed or duplicate-key, extra-key, non-string, oversized, NUL-containing,
  and content-plus-tool submissions before the unchanged disposable verifier and deterministic
  grader run. Corpus version 7 makes the two unchanged unified-diff context prefixes explicit so
  K3 cannot silently drop the leading context marker on the closing brace. The local patch lane
  remains a deterministic raw diff. Corpus version 7 keeps the
  five-trial campaign at 35 requests and 48,640 reserved output tokens (290,000 estimated initial
  input tokens).
- Make local patch qualification request-deterministic (`reasoning_effort=none`,
  `temperature=0`) and stop the pinned DSV4 launcher from globally overriding request
  temperature. The launcher retains the qualified `top_p` default, while exact request
  contracts control their own sampling temperature; every other local corpus request binds
  its prior effective temperature explicitly so the launcher repair cannot silently change it.
- Make promotion revalidate the complete sealed attestation invariants instead of trusting
  `status`: all required repetitions present and passed, every expected distinct task and trial
  index present once, every request succeeded with exact `responseModel`, no null token counts,
  `evidenceSha256` matches canonical per-case, and the ledger run ID/SHA/provider/model/binding/
  counters correspond with the ledger closed and its final SHA matching the attestation. A
  tampered qualified attestation fails closed and never emits a router qualification.
- Audit token and cost estimates from actual canonical request size and enforce per-run request
  budgets for the six-case corpus; close the ledger only when all requests are known terminal
  and keep stdout and reports strictly content-free.
- Remove the arbitrary transport override from the production runner: production resolves only
  the closed transport registry, and hermetic tests inject a transport through an explicit
  test-only factory that the static release check forbids outside tests.
- Require explicit lane selection (`local-only` or `moonshot-kimi`) with no silent fallback and
  accept only the closed loopback/container proxy-route enum for K3 host testing.
- Preserve the frozen 4.0 evidence line and the 4.1 candidate record. This release uses the
  signed forward-update contract from the minimum 4.0 release and does not relabel 4.1 as
  supported.
- Add an explicit closed `modelSelection` policy to every provider profile: `fixed` for
  Moonshot Kimi, OpenAI, and Anthropic (using exactly `defaultModel`); `owner-pinned` for
  OpenRouter, Together, Fireworks, and Groq (requiring an owner-private `model` that is never
  a placeholder/auto/provider-selected value and must match qualification, router decision,
  run binding, request, and returned response model); and `qualification-pinned` for local.
  All validation flows through a single closed helper in `provider-registry.mjs`, and every
  path that could execute the literal `provider-selected-model` placeholder is removed.
- Make response-model binding durable: every run-ledger request carries a `responseModel`
  that starts `null`; a successful qualification-trial or semantic settlement/reconciliation
  requires a validated exact response model equal to `ledger.model`, connectivity-smoke may
  remain `null` only for backward-compatible reachability, and non-success settlements cannot
  claim one. The response model is persisted atomically before any response is disclosed, and
  promotion cross-checks each content-free evidence `responseModel` and input hash/usage
  against the corresponding ledger request (including the two-request tool case) instead of
  only comparing aggregate counters. Tamper and crash-state tests cover the new contract.
- Add closed generic production transports (`generic-remote-transport.mjs`) with exact static
  provider-ID-to-wire mappings and no caller-controlled URL/path/header for OpenAI
  (`/v1/responses`), Anthropic (`/v1/messages`, fixed `anthropic-version: 2023-06-01`),
  OpenRouter, Together, Fireworks, and Groq (each `chat/completions`), all reusing the proven
  proxy-only CONNECT/TLS framing. Transports enforce exact owner-private host allowlists,
  custody handles, strict UTF-8/JSON, response byte/time bounds, exactly one explicit response
  framing mechanism (exact `Content-Length` or extension/trailer-free chunked encoding),
  provider-reported model equality, exact token usage, credential zeroing, and deterministic
  failed-known vs. uncertain error classification; no arbitrary production exchange callback is
  exposed. All are registered in the frozen transport registry.
- Add hermetic wire tests (`work-provider-transports.test.mjs`) that assert the exact CONNECT
  host, TLS server name, HTTP method/path, auth header, fixed headers, request JSON shape,
  adapter selection, response-model check, usage, and failure classification for every
  provider using test-only fake exchanges, including adversarial wrong host/path/auth/
  protocol/model, missing usage/model, duplicate/conflicting framing, oversized/invalid
  UTF-8/JSON, and owner-pinned placeholder models. No test contacts the network or reads a
  real key.
- Qualify the live local vLLM response shape without widening any remote provider parser: the local
  adapter admits only the exact reviewed null-valued vLLM metadata extensions, explicitly discards
  private local `reasoning`, and preserves exact model and usage binding. Non-null local metadata,
  malformed reasoning, and remote-style `reasoning_content` fail closed. The local production
  transport now rejects caller-supplied exchanges, exposes a frozen test-only seam, and classifies
  fully received strict-JSON/adapter/model/usage failures as deterministic `failed-known` outcomes.
- Record an untrusted malformed patch as an ordinary failed semantic qualification and continue the
  sealed campaign, while preserving a distinct verifier-infrastructure error that still aborts
  fail-closed. This prevents a valid provider response containing a bad patch from leaving a
  content-free run ledger open without a terminal qualification report.
- Add a closed content-free `failureCode` to every qualification request evidence item. Successful
  requests require `null`; failed-known and uncertain requests require one bounded category such as
  proxy, TLS, timeout, response framing/size/JSON/UTF-8/adapter/model/usage/identifier/metadata,
  provider HTTP, request validation, or unknown transport. Diagnostics never persist exception text, provider content, or credentials,
  and semantic promotion requires every request code to be `null`.
- Complete the live vLLM compatibility boundary at the choice layer: the observed
  `routed_experts`, `stop_reason`, and `token_ids` extensions are accepted only when exactly `null`
  and are discarded before the shared strict parser. Non-null values remain rejected, and remote
  OpenAI-compatible adapters are not widened.
- Revise the sealed provider qualification corpus to version 2 after real DSV4 and K3 trials:
  retain margin above the authoritative 32K reported-input floor, grade output capacity by a
  fixed neutral-word and reported-token floor rather than brittle byte counting, compare fixed
  tool arguments as strict JSON, admit only narrowly normalized one-file diffs, and preserve
  content-free static HTTP parser subtypes for diagnosis without storing provider content.
- Revise the sealed provider qualification corpus to version 3 after the next exact live gate:
  make the tool continuation a strict semantic JSON observation, use the proven local
  `reasoning_effort=none` mode for deterministic visible-output and continuation controls while
  retaining K3's supported reasoning mode, close every replay field to the lane-bound corpus,
  and prefer typed content-free proxy/TLS/parser failure categories over message inference.
- Accept repeated unconsumed HTTP response headers while continuing to reject duplicates of
  framing, content type, content encoding, and request identity. This admits standards-compatible
  edge metadata without weakening body framing or ambiguous-outcome handling.
- Modernize the routed-equivalence harness so the genuine fixed semantic corpus cases
  (`structural-review`, `patch-proposal`, `failure-triage`) run through the production router,
  semantic binding, durable executor, closed transport, and deterministic grader, while the
  qualification controls (`tool-choice-continuation`, `long-context-sentinel`,
  `output-capacity`) remain in the qualification runner. The equivalence harness never mints a
  semantic qualification itself: it requires the exact owner-private qualification artifact
  path and bound SHA-256 (`--qualification-report` / `--qualification-sha256`), validates its
  schema/hash, qualified status, and lane/provider/model/corpus provenance, and passes that
  exact promoted qualification to the router and binding. The qualification runner promotes a
  content-free exclusive mode-`0600` qualification artifact (mandatory when qualified, written
  only when qualified) binding the exact semantic qualification to the qualified trial's
  qualification/evidence/ledger hashes and lane, provider, model, corpus, trials, and status.
  Artifact and evidence reports require distinct owner-private create-once paths, and artifact
  reads are bounded, no-follow, and strict duplicate-key-rejecting JSON. The content-free CLI
  (`equivalence-runner.mjs`) emits hashes, counters, and grades without
  prompt or response text, and refuses tampered, missing, unqualified, wrong-lane/provider/
  model/corpus, and self-minted envelopes. Uncertain outcomes remain non-retried and
  non-rerouted.
- Extend the release manifest/contract with `modelSelection`, `responseModelBinding`,
  `genericRemoteTransportModule`, and `equivalenceRunnerModule` fields and update the static
  release checks to run the hermetic wire and equivalence suites and reject the
  `provider-selected-model` placeholder in shipped profiles.
- Reconcile every source SHA-256 embedded in `Dockerfile.moonshot-worker` with the shipped
  provider modules and add a regression that recomputes every pin, preventing a stale hash from
  making an otherwise-qualified Moonshot worker image unbuildable.

## 4.1.0 - Unreleased

- Productize the rootless trusted-owner Pixel mesh as `deploy/mesh/` (mesh-0.1.0):
  an additive installer (`install.py`) and peer status/message transport
  (`pixel_mesh_peer.py`) that run one owner-trusted Pixel per tower without touching
  the hardened system deployment, the custody source, or any live runtime. Paths and
  host settings are parameterized through environment variables with the
  Tower1/Tower2/Tower3 defaults preserved and a fixed peer allowlist. The installer
  fails closed on a missing OpenClaw binary, uses only explicit argv (no shell
  interpolation), and retains a `previous` release for rollback. Unit tests cover the
  installer's safe temporary-home/dry-run contract and the peer transport/evidence
  contract without real SSH, systemd, Discord, credentials, live profiles, or live
  runtime state. See `deploy/mesh/README.md`.
  The credential-safe Discord handoff utility defaults to inspection-only,
  requires explicit apply, validates staged configs, preserves the established
  Pixel workspace, and retains private restorable backups without printing
  channel credentials.
- M7 adversarial review/repair: tighten the bounded readiness reader so a
  declared Content-Length must equal the exact completed stream length at clean
  EOF (declared-short and declared-long mismatches both fail closed), reject a
  nonterminal zero-length chunk so an endless empty-chunk stream can never pin
  the guardian, and release/cancel the response body on every early rejection
  path (non-200, invalid/oversize length, missing/invalid body, stream error,
  oversize, and zero-length) without letting a cleanup throw change the
  fail-closed outcome. The default Docker-start closure is re-verified to pass
  the exact container id, closed-over canonical socket path, 60s timeout, and
  reviewed endpoint identity while never forwarding the caller-provided nonce
  as Docker attribution; the nonce remains durable journal attribution only.
- M7 repair pass 2: harden the last-moment seams and evidence truthfulness of the
  M7 campaign recovery guardian. The default Docker-start closure now binds the
  exact closed-over socket path (no self-reference) through an injectable lower
  -level start seam; the default readiness probe now drains the response through
  a truly byte-bounded streaming reader that rejects invalid/negative/lying
  Content-Length and cancels on oversize, decodes with strict UTF-8 and Pixel's
  strict JSON parser (duplicate keys, malformed JSON, and invalid UTF-8 never
  become readiness proof), and never lets raw body bytes escape. The guardian
  now re-proves the full live state (exact stopped/running production,
  campaign-child terminal/absent, clean comparison/GPU isolation, and the secure
  endpoint identity) immediately before the Docker start, again before the
  production-started advance, and again before the readiness-proven advance,
  requiring exact restartCount 0 and canonical full-fingerprint stability across
  every post-204 observation. Result authority is now truthful per result (only
  results that actually authorize/performed the operation grant the scoped
  start/readiness authority), while M7's standing capability is represented
  separately, so no-active, manual-attention, prepared-cancelled, and M8-hold
  results never falsely grant an unscoped production start/readiness operation.

- M7: extend the single campaign recovery guardian from `isolation-clean`
  through `production-start-authorized`, `production-started`, and
  `readiness-proven`, then an explicit M8-required hold. Each invocation
  performs at most one identity-bound transition/effect bundle and returns, and
  every production authorization, Docker start attempt, readiness probe, and
  journal advance re-loads under custody and re-proves exact config/custody/
  journal/lease authority plus the phase-specific live state at the last
  possible seam. The authorization pass records only a one-time 64-hex nonce
  and the exact secure Docker socket identity; the start pass makes exactly one
  fail-closed Docker Engine start request and attributes success only to an
  exact definite 204 bound into a durable receipt with the post-start
  StartedAt; the readiness pass performs one bounded credential-free loopback
  probe and records only status 200, latency, provenAt, productionStartedAt,
  and the response SHA-256 before advancing durably. M7 never settles, cancels,
  or removes the active journal and never claims campaign completion; it grants
  only the minimal exact local authority to restart and probe the already-bound
  production container. Replays, endpoint/config/lease drift, isolation or
  child regressions, and readiness/start ambiguity all fail closed with
  content-free manual-attention evidence and no ambiguous re-effect.
- M6: real campaign crash-recovery engine. The campaign recovery guardian now
  implements the full recovery contract through the durable `isolation-clean`
  phase under maintenance custody with a current exact campaign guardian lease
  and exact config/journal identity revalidation. It cancels an inert `prepared`
  journal only after mechanical proof, idempotently stops the exact bound
  production from `production-stop-authorized`, and proves/enforces exact child
  terminality (only the exact nonce-derived unit, after recorded process
  identity + InvocationID/control-group/MainPID proof, reproving an empty exact
  cgroup) before advancing monotonically to `isolation-clean`. M6 never starts
  production, authorizes a production start, probes readiness, settles an active
  journal, removes foreign resources, or relaunches a campaign child; beyond
  `isolation-clean` it returns an explicit M7-required hold. Every unresolved,
  substituted, stale, or changed identity fails closed with content-free
  manual-attention evidence. Flips `CAMPAIGN_RECOVERY_ENGINE_READY` to true and
  adds a dedicated deterministic recovery test suite.
- M6 repair: recovery now converges the pre-isolation phases instead of false
  blocking when exact evidence permits. A `production-stopped` journal with no
  child ever authorized and a `campaign-child-authorized` journal with an
  authoritative not-found unit advance directly to `isolation-clean` (returning
  the M7 hold) after exact stopped-production and clean-isolation proof, never
  launching or fabricating a child identity. Each invocation performs at most
  one identity-bound phase transition/effect bundle and then returns; a watcher
  loops immediately on a non-manual progress status and always publishes/refreshes
  its lease from a freshly loaded under-custody binding and the exact current
  journal. A narrow last-moment authority recheck (re-load, re-inspect, exact
  journal-identity compare, revalidate bindings, and require a live lease bound
  to the same journal identity) guards every cancel, production stop, child-unit
  stop, and journal advance, and the child unit is re-shown and its complete
  process identity re-derived immediately before any stop.
- M6 repair: recorded-child terminality is now proven from the immutable process
  incarnation (exact bootId, pid, startTicks) instead of the mutable complete
  identity, so a live recorded child that moved out of its old unit cgroup can
  never be declared terminal or persisted isolation-clean. Terminal handling
  accepts only an authoritative not-found unit or a loaded+inactive+dead unit
  with MainPID 0, and for either shape proves the recorded incarnation is gone
  plus the exact recorded unit cgroup is empty. Cleared terminal
  InvocationID/ControlGroup (which real systemd clears on a loaded oneshot after
  terminal exit) no longer block legitimate recovery, but terminality is never
  inferred from cleared metadata alone. Strict InvocationID/ControlGroup/MainPID
  and complete process-identity checks before any stop are preserved, and the
  relaxed terminal handling never authorizes a stop or other destructive effect.
- M6 TOCTOU repair: every journal advance is now preceded by both the exact
  last-moment config/custody/journal/lease authority recheck and a fresh
  phase-specific live-state reproof so isolation-clean is never persisted from
  stale evidence. Immediately before advancing, the guardian observation-only
  re-proves the exact bound production remains stopped (`production-stop-authorized`
  -> `production-stopped`), and for every transition into `isolation-clean`
  re-proves production stopped, exact comparison/GPU isolation clean, and the
  exact recorded child terminality/cgroup emptiness (or the authoritative
  not-found nonce-derived unit at `campaign-child-authorized`). The reproof
  never stops production and never stops a unit; any production restart or
  substitution, late comparison/GPU resource appearance, authorized-unit
  appearance, or active-child terminal/cgroup regression fails closed with
  manual-attention evidence and no additional effect. Prepared cancellation
  retains its exact last-moment authority and inert/clean proof unchanged.

- M5 third review: the true first-cause forced outcome is canonical. In
  `launchBoundedChild`, an output overflow that happens before the wall-clock
  timeout remains the canonical cause even if the timeout fires before `close`
  or the grace settlement; the timeout callback never overwrites an existing
  forced cause. Deterministic fake-child tests cover stdout-overflow-first,
  stderr-overflow-first, and timeout-first (then later stream data) with no
  `close`, each settling directly by grace with exact canonical fields.

- M5 third review: no string-spoofed `CampaignFailure` bypass. The launcher no
  longer uses `error.name === "CampaignFailure"` anywhere as authorization to
  preserve/restore an error. `launchCampaignChildSupervised` now covers every
  operation through the exact durable `comparison-cleanup-pending` transition
  with a content-free catch, then performs classification outside that
  pre-terminal try/catch. The controller's `error instanceof CampaignFailure`
  mechanical gate is the only path that may restore production; a name-spoofed
  ordinary `Error` is converted to a content-free `WorkCampaignChildLaunchError`,
  remains at the truthful journal phase, and never reaches classification.

- M5 third review: test process safety. The fake no-close child uses no positive
  PID (0) so it can never signal a real process group. Same-group and detached
  descendant tests register exact-PID cleanup immediately after reading the PID,
  before assertions that could fail, and the cleanup helper verifies
  `/proc/<pid>/cmdline` is the exact test fixture plus expected mode before any
  signal, refusing on absent or changed identity. A helper test proves
  identity-mismatched cleanup refuses to signal.

- M5 second repair: the positive terminal proof is now the sole restoration gate. Every
  exception raised before the exact `comparison-cleanup-pending` advance in
  `launchCampaignChildSupervised()` is converted to a content-free
  `WorkCampaignChildLaunchError` regardless of original class (contract publication
  `WorkMaintenanceSecureFileError`, ordinary `Error`, guardian/journal/systemd errors, or
  supervisor validation errors). In `runModelCampaignMaintenance`, any unexpected/non-
  `CampaignFailure` error after production stopped holds production (`manual-attention-
  production-held`) and never restores; only an explicitly classified `CampaignFailure`
  thrown after the positive terminal/cgroup proof and the durable cleanup-pending transition
  may restore. Adversarial tests use real non-launcher error classes at contract
  publication, systemd start, receipt validation, artifact read, and terminal proof seams
  and assert `startProduction` count zero and truthful journal phases.

- M5 second repair: forced timeout/overflow outcomes are canonical and self-validated. A
  single first-cause termination state (`timeout`, `stdout-overflow`, `stderr-overflow`) is
  introduced; forced outcomes always resolve to `exitCode=null`, `signal=null`, exactly one
  of `timedOut`/`outputOverflow`, and `spawnError=false`, regardless of the later close/error
  event, and competing timers are cleared. The constructed receipt is validated with
  `validateCampaignChildReceipt` before publishing any result/diagnostic/receipt artifact.
  Tests drive real timeout and both overflow paths through `superviseCampaignChild` and
  validate the actual published receipts.

- M5 second repair: completion has a direct timeout+grace settlement independent of `close`.
  `armGrace()` no longer unrefs the only timer required to prove bounded completion; at the
  bounded grace deadline it resolves once with the canonical first-cause forced outcome if
  `close` has not arrived. A fake child whose `close` never fires and whose pipe destroy does
  not synthesize it proves resolution within timeout+grace with bounded hashes/bytes.

- M5 second repair: the descendant test is truthful. The fixture no longer uses an unresolved
  top-level await (which Node exits with code 13) and no longer launches the grandchild with
  `detached:true`. For the process-group guarantee the descendant stays in the inner child's
  process group and writes its exact PID to an owner-private test-only PID file, verified by
  exact PID (no `pgrep` substring search). A separate adversarial detached-descendant case
  proves the supervisor alone cannot kill a descendant that calls `setsid`, cleans up that
  exact PID in `t.after`, and documents systemd `KillMode=control-group` + `RuntimeMaxSec` as
  the cgroup backstop requiring later real-systemd qualification. The launcher/cgroup terminal
  gate refuses `comparison-cleanup-pending` while such a cgroup descendant is live.

- M5 second repair: direct primitive tests cover Buffer publication, a genuinely parallel
  two-writer `Promise.allSettled` race (exactly one succeeds and the winning bytes remain
  coherent), exact cgroup matching versus prefix/suffix/substring attacks, runtime-bound
  derivation, and terminal-state substitutions. `exactCgroupLineMatches` and
  `isAcceptedTerminalState` are exported narrowly for deterministic direct tests.
  `CAMPAIGN_RECOVERY_ENGINE_READY` remains false.


- Run the campaign child through an exact nonce-named transient user service and a bounded
  Node supervisor (M5) instead of a direct execFile. Under custody, after durable
  `production-stopped`, a single nonzero 64-hex launch nonce is generated and the real
  campaign recovery journal is durably advanced to `campaign-child-authorized` before any
  transient unit or worker exists; the exact live guardian lease and the exact same
  authorized journal are reasserted immediately before launch, the full-nonce unit name is
  proven absent, and the unit is started exactly once. The supervisor is the unit MainPID
  and is the only authority that launches the exact Python executable with `shell:false`, a
  fixed env/cwd, no stdin, bounded pipes, and a hard timeout, capping bytes before growth
  can exceed the bound and terminating on overflow. Strict unit facts and the full `/proc`
  identity (boot ID, PID, start ticks, uid, real exe, exact argv, cgroup) are bound into the
  journal `campaignChild` at `campaign-child-active`, and only after the strict durable
  receipt, bounded stdout/diagnostic coherence, and proof that the supervisor is gone/terminal
  with no live cgroup descendants and no unit substitution does the journal advance to
  `comparison-cleanup-pending`. Any launch request or ambiguous effect retains the truthful
  journal and never relaunches or cancels. M6 recovery, M7 restart transitions, M8 settlement,
  and the fail-closed campaign recovery guardian readiness constant remain untouched.

- M5 repair: create-only no-clobber durable publication for the contract, receipt, result, and
  diagnostic artifacts (hardened same-directory temp + `link()` + unlink + directory fsync +
  exact target reproof) so an existing regular file, symlink, hardlink, or concurrent
  publication is never overwritten or replaced. The bounded supervisor launches the inner child
  without a shell in its own POSIX process group, SIGKILLs the whole group on timeout or
  overflow, and detaches its pipe handles after a short fixed kill grace so completion never
  depends on descendant-held descriptors; the transient unit carries a `RuntimeMaxSec` derived
  from the bound plus a fixed grace so a stuck supervisor cannot live forever. Receipt polling
  retries only while the receipt is provably absent and fails immediately on any tamper/ambiguity.
  Strict terminal proof requires either the same loaded unit with the same InvocationID and exact
  ControlGroup in an accepted terminal state, or a narrowly defined not-found case backed by the
  recorded identity plus an exact empty cgroup proof; exact cgroup paths are normalized and
  compared, never substring-matched. Result and diagnostic artifacts are validated owner-private
  (exact owner, private mode, nlink=1) from the open-handle metadata, and receipt outcomes obey
  one explicit mutually coherent contract. The controller holds production
  (`manual-attention-production-held`) on any ambiguous or non-terminal child-launch failure and
  never calls startProduction until the launcher has proven terminal identity/cgroup cleanup and
  durably advanced the real journal to `comparison-cleanup-pending`. The child deliberately
  retains fixed Docker-mediated campaign authority via the Docker socket; that socket authority
  is documented, not claimed isolated.

- Attribute production restart to an exact, fail-closed Docker Engine start attempt. Before the
  start, production must be proven stopped with the exact bound container id, and a
  cryptographically random attempt nonce/intent is durably recorded. The start uses the Docker
  Engine API (`POST /containers/{exact-id}/start`) so an actual transition by this request (204)
  is distinguished from an already-running no-op (304). The durable 204 receipt is bound to the
  nonce, exact container id, endpoint/socket identity, status 204, and exact post-start
  `StartedAt`. A 304, an unexpected status/body, a changed container id, an external start before
  the call, or a running production with only intent and no durable success receipt holds manual
  attention and preserves the journal; a crash after 204 but before the receipt is an honest
  ambiguous/manual state that is never silently adopted, and a replayed receipt never causes a
  second start effect.

- Make the maintenance recovery guardian the normal supervised path, not an optional
  hardening add-on. The direct `model-qualification-maintenance.mjs run` path now
  mechanically requires a fresh, config-bound, ready live-guardian lease immediately before
  journal creation and again immediately before the stop, so an absent, dead, stale,
  substituted, or race-to-death guardian fails closed before production is touched. The
  lease is a real process-identity proof, not a forgeable unit string: the watcher derives
  its own boot ID, PID, `/proc/<pid>/stat` start ticks, executable realpath, argv, and
  cgroup from secure `/proc` reads, and the run path rereads and compares those live facts,
  so a recycled or unrelated live PID, a PID-start mismatch, or a wrong executable/argv/
  cgroup is rejected. The proof also binds the exact custody-lock identity SHA, guardian
  module SHA, config raw hash, unit contract, and exact systemd invocation identity, the
  exact rendered/installed unit SHA, installed fragment bytes, FragmentPath, ControlGroup,
  ActiveState, SubState, and MainPID, with exact real paths (never basenames) for the node,
  guardian module, and config argv. Supported Linux fails closed when the boot ID or exact
  process identity cannot be proven (there is no `unknown` skip). The lease uses owner-private
  no-follow/exclusive durable storage with mandatory directory fsync and timestamp coherence
  (created <= refreshed <= now, no far-future freshness), validates nlink/owner/mode after
  write/read, and only the watcher that owns the exact lease may remove it. Readiness is
  fail-closed and observable under the `Type=exec` contract (no sd_notify): the watcher
  publishes a non-ready proof lease only after it has reread and validated its config,
  custody, code, and unit bindings, then publishes ready only after that proof write durably
  succeeded; a failed durable write at startup aborts nonzero so systemd never reports a
  usable guardian. The maintenance run requires that exact ready lease conjoined with the
  exact live systemd unit/process identity, so a plain owner process writing a lease is never
  sufficient. The default
  recovery preparation now attaches its private prepared binding before freezing (the
  previous freeze-then-define always threw), and recovery freshly proves qualification
  runner/backend/network absence on every pass regardless of the durable isolation phase.
  The maintenance configuration and every transitive authority-bearing Docker/backend/
  environment/qualification input are reread and re-prepared under the acquired custody lock
  and compared (raw-byte hashes, operation hash, custody identity, qualification identity,
  executable paths, and production binding) to the pre-lock routing/review binding, so any
  load-to-lock mutation or path substitution fails closed with zero Docker mutation and
  every action, journal record, and lease check derives from the under-lock load. A prepared
  inert journal is cancelled only through an identity-checked durable cancellation primitive
  (never a substituted-path unlink, with directory fsync), and a stop whose outcome cannot
  be confirmed (inspection failure or an unconfirmed restart) is never inferred as "no
  destructive action" from a later running state. Guardian manual-attention boundaries now
  honestly report content-free committed cleanup booleans and never claim no mutation when
  exact cleanup committed. The journal enforces that `guardianStartedAt` is null before
  production-started and recorded at/after it, and recovery reads the freshly advanced
  durable journal for its result phase. Production-start authorization is bound to the
  guardian's own exact recorded start: an external start between authorization and the
  guardian start holds manual attention, and a changed `StartedAt` is never accepted solely
  because the phase number is high enough. The supervised install/remove now require an
  exact confirmation binding operation, canonical unit path, config identity, and
  installed/render bytes; inert review emits the exact install/remove confirmation values;
  removal refuses while the guardian lease is still present and never swallows a
  `disable --now` failure; and a Pixel-owned older unit may be upgraded only with an
  exact-confirmed install while unrelated content is always refused. The unit renderer uses
  secure private readers with full maintenance/Docker/backend validation, fails closed on
  derivation failure, and rejects systemd-unsafe paths before review.

- Add a crash-safe, persistent recovery guardian for model qualification maintenance (Layer 2B).
  The guardian is a `Type=exec` event-driven watcher (`watch --config PRIVATE_JSON`) that stays
  alive before any maintenance journal exists, reacts to journal/config changes, survives its own
  crash via `Restart=always`, installs SIGTERM/SIGINT/abort handlers that durably remove only its
  own exact lease before exiting, and acquires the exact advisory-flock custody lock only for each
  recovery pass and never busy-loops on a stable manual-attention condition (bounded backoff).
  Recovery reuses Pixel's exact backend lifecycle (`stopModelBackend` with the full prepared launch,
  exact lifecycle confirmation, and a fresh exact postflight) so a same-name foreign backend or
  network is never removed, and the qualification runner cleanup validates full ID/name/image
  digest/operation label/network mode. The monotonic journal now carries guardian phases
  (isolation-clean, production-start-authorized, production-started) with a recorded exact guardian
  start, so a changed production `StartedAt` is accepted only when it equals the guardian's own
  exact recorded start, while an external start between authorization and the guardian start and
  a pre-maintenance fingerprint still running both stay safe. All emitted
  guardian statuses are validator-bound to a closed set. The systemd user unit uses the unescaped
  `%I` binding, is `Type=exec` (readiness is the durable ready lease conjoined with the exact live
  systemd unit/process identity, never a fake sd_notify stream), grants exact `ReadWritePaths`
  (only the custody/journal directory and owner-private backend coordination root), imposes no
  startup timeout that can preempt the bounded restore timeout, and is installed via separated
  inert render/validate and exact-confirmed install/remove with byte-exact materialization,
  preimage/ownership/link checks, strict validation of any prior Pixel unit (never a single grep
  marker), and safe removal that refuses while an active recovery journal exists and only removes
  the unit after the watcher durably removes its own lease and the unit is verified inactive.

- Make the paired campaign genuinely pre-disclosure and condition-counterbalanced.
  Tuning and freeze invocations no longer open or compatibility-review held-out
  task bytes; the freeze commits their content identities and asserts the held-out
  payload boundary remained unopened, while held-out progress binds the exact
  validated freeze hash. Warm campaigns reverse every cold per-task arm order so
  the same task runs Pixel-first and Codex-first once across the two conditions.

- Make comparison autonomy mechanically accountable. Pixel product paths now emit
  content-free harness-observed interaction receipts, and Codex runs derive the same
  receipt from closed-stdin JSONL lifecycle events. Evaluation separately measures
  actual post-admission operator input, requested attention, approvals, scope expansion,
  safety blocks, and interruptions; worker self-report, cross-arm source substitution,
  omitted receipts, and execution/observation disagreement fail closed. Excess Pixel
  attention is capability-blocking even when the operator never responds, preventing a
  restrictive harness from scoring as autonomous merely because input was unavailable.

- Add a destination- and source-bound maintenance coordinator for resumable local DSV4
  comparison batches. One exact confirmation can stop only the reviewed production model,
  run at most the reviewed number of cold or warm paired tasks, require exclusive GPU
  custody and zero residual `pixel-outcome-` or `pixel-work-` containers, networks, or volumes, restore the same
  production container, and prove credential-free loopback readiness. Campaign errors with
  clean teardown restore service; leaks or competing GPU consumers hold service for manual
  attention instead of risking a collision.

- Replace the manual enabled-policy pointer edit in the DSV4 comparison path with a
  destination-bound review/apply operation that writes one new private system configuration
  changing only `policyTemplatePath`. Reject duplicate decoded keys in private policy and
  qualification JSON before they can cross that handoff.

- Close the next DSV4 comparison handoff: require the exact successful Pixel system
  policy-binding receipt before a destination-bound review/apply operation can write a new
  Pixel/Codex pair configuration. The operation changes only the Pixel system and unused
  preflight paths, refuses hand edits, overwrite, stale-preflight reuse, linked inputs, and
  post-review substitution, and grants no model, task, network, or external-effect authority.

- Bind local-model Docker qualification confirmation to the exact owner-private Docker
  configuration bytes so a reviewed receipt destination cannot be substituted before run.

- Establish Deep Work as the truthful post-4.0 successor line. The qualified Pixel 4.0
  compatibility record and live-audit bytes remain frozen. Pixel 4.1 uses the final
  bootstrap-compatible manifest understood by the frozen 4.0 updater, requires 4.0.0 as
  its upgrade floor, and changes its own updater to require `forward` policy for 4.1.1 and
  later releases.

- Align the one-cycle controller receipt with its event-driven runtime: durable checkpoint
  transitions report `event-or-watchdog-recovery`, quiescent states report `event-noop`,
  and documentation now distinguishes continuous goal pursuit from the optional timed
  appliance soak.

- Add disabled Deep Work capability-pack declaration admission. Pixel can inspect an untrusted exact
  declaration, sign or verify canonical bytes under a distinct Ed25519 namespace, atomically
  install a private immutable copy, and re-audit every installed version against the current
  publisher trust root. Linked or changing inputs, signer substitution, signature tampering,
  duplicate versions, unsafe permissions, trust revocation, and incomplete crash residue fail
  closed. Exact-review removal transfers the declaration into recovery custody before
  deletion, publishes a no-residue tombstone, survives interruption, and blocks
  same-version replay. Declaration admission pulls, inspects, and executes no image and grants no tool,
  data, network, external-effect, or completion authority.

- Add the separate disabled capability-image admission lifecycle. Pixel admits only an
  already-local canonical repository manifest digest through an empty credential-free Docker
  configuration and separately binds the signed local Docker image ID and Linux platform.
  It validates a fixed safe image environment, signed metadata, and exact executable bytes through a bounded tar stream from a hardened
  never-started container, and proves forced removal after every create attempt. Exact-review
  revocation retains the unowned image. A schema-bound single-writer record serializes image
  inspection, revocation, and pack removal; intent-only, post-move, post-custody, and
  post-finalization crashes recover without guessing. Pack removal now persists independent
  custody proof before deleting payload bytes. Image admission remains disabled and
  begins health-not-probed, with no tool registration or authority. A permanent opt-in
  supported-host gate builds a real networkless scratch image and proves the complete
  disabled admission/revocation/removal lifecycle and exact test-owned cleanup.

- Add explicit confirmed disabled capability health probing. Pixel runs only MCP discovery
  and signed-schema tool listing against the exact admitted image in a sterile disposable
  container; it creates no grant, invokes no tool, receives no client data, and enables
  nothing. Durable operation custody, mandatory force-removal and exact absence proof,
  chained content-free receipts, exact crash recovery, concurrent one-winner behavior, and
  quarantine after three consecutive failures are covered by hostile deterministic tests.
  The supported-host scratch-image gate now proves this real health lifecycle too.

- Add the internal disabled single-use capability runtime. Recent passing health, one exact
  expiring grant, and one checkpoint-bound watchdog decision are required before Pixel burns
  durable claim/execution custody and performs exactly one networkless MCP call. Validated
  structured output returns only to the live caller; terminal receipts contain no arguments
  or result content. Exact container and optional tmpfs-workspace ownership checks, absence
  proof, stale-health rejection, concurrent one-winner behavior, foreign-resource refusal,
  sanitized failures, tamper detection, and no-replay crash recovery are covered. The real
  supported-host fixture now proves an actual tool call. Controller/profile mediation and
  enabled end-to-end qualification remain closed gates.

- Add pure event-driven capability authorization before the disabled runtime. An exact signed
  pack can receive a job envelope only after the ordinary OMP lease is consumed and its exact
  running checkpoint exists. Private policy, job authorization, and worker-request schemas
  clamp tools, effects, classification, sessions, lifetime, resources, signed input schema,
  and the trusted watchdog-event head. Repeated events separated by arbitrary elapsed time
  still stop with no grant; freshness clocks only invalidate stale authority. Substitution,
  forged authorization widening, scope expansion, malformed inputs, and forged event heads
  fail closed.

- Add disabled durable capability request custody and watchdog settlement. One owner-private
  single-flight queue atomically claims each raw request, records append-only content-free
  phases, stages successful structured output under job-only retention, appends one exact
  event, and publishes one exact response. Forced crashes after authorization, launch, output,
  event, response, and settlement recover without duplicate execution or events. Launch is the
  conservative no-replay horizon; missing transient output becomes an honest uncertainty
  response. Concurrent controllers have one runtime winner, watchdog stop closes later enqueue,
  and aggregate session runtime/storage ceilings fail closed at authorization.

- Add the internal job-scoped OMP capability catalog and trusted-extension foundation. One
  exact authorization and signed pack derive a fresh content-addressed catalog with
  deterministic `pixel_cap_*` aliases and exact signed raw schemas. The extension registers
  only those names for that worker, serializes requests against validated settled event heads,
  rejects stale or substituted responses and output, and closes after any post-publication
  ambiguity. It has no network, credentials, child-process, grant, ambient-registration,
  external-effect, or completion authority. Real profile mounting/routing, lifecycle
  accounting and cleanup, operator status, and live enabled OMP qualification remain gates.

- Replace the arbitrary multi-day promotion blocker with an exact-source event-horizon
  gate. The required campaign exercises the full 64-milestone limit as repeated independent
  branches and verified convergence across 84 dependency links, 256 forced post-commit
  exits, and 385 fresh controller processes. Elapsed time is measured only to prove process
  separation. The 48-hour systemd/controlled-reboot campaign remains available as optional
  appliance endurance evidence and no longer stands in for useful long-goal progress.

- Add a durable owner-facing private context-session lifecycle. Exact review/apply creation
  binds one plan, checkpoint, context, classification, state root, and lifetime; content-free
  listing and inspection are separate from explicit trusted-terminal display. Forking starts
  a distinct job lineage without reusing authority or lowering classification. Exact removal
  uses verified hard-link custody, content-free tombstones, crash recovery at every custody
  boundary, and visible recovery-attention state. Concurrent confirmation and removal races
  converge, while linked, substituted, cross-root, stale, or malformed inputs fail closed.
  The plain-language guide wraps create, fork, and remove without giving the browser private
  content or mutation authority.

- Add atomic guided launch preparation for long-horizon goals. One exact reviewed draft
  hash now produces the final-path-bound controller assembly and all one to sixty-four
  policy-clamped child plans and expiring single-use leases in one private package. Input,
  policy, draft, controller, and compiled-child drift fail closed; interrupted and
  concurrent publication cannot leave a partial package. The result remains inactive and
  creates no ledger, worker, service, schedule, network request, provider call, external
  effect, or completion authority. A read-only whole-package inspection returns its exact
  manifest hash, and the unified launch command stages only that still-current package;
  service activation remains separate.

- Add disabled-by-default loopback Deep Work goal authoring. An owner-private fixed-path
  configuration binds policy, admitted input catalog/object store, draft store, and
  retention; the process-lifetime private view exposes only enabled work kinds and generic
  opaque input summaries. Owners can describe up to sixteen milestones as an acyclic
  branch-and-converge graph with exact done-when checks and effort ceilings; the safe
  default still links each new milestone to the preceding one. Hash-bound confirmation runs only the fixed goal
  drafter and creates an inert private draft. Stale policy/catalog/configuration, private
  research, classification downgrade, path/field smuggling, unknown handles, malformed
  catalogs, output races, and full retention fail closed; the browser gains no input
  admission, compile, stage, scheduling, execution, egress, scope, or completion authority.

- Add explicit local-folder admission for Deep Work. Pixel streams each owner-selected
  directory into a deterministic, normalized, strict-ustar content object, keeps embedded
  agent/configuration controls inert, deduplicates identical trees, and emits the private
  catalog consumed by guided goals. It rejects symbolic and hard links, special files,
  path/projection collisions, source mutation, output overlap, resource ceilings, and
  publication races; source paths and filenames never enter the terminal receipt.
- Extend guided admission through private Data Lab work: owner-named dataset files receive
  exact raw-content hashes and byte sizes inside the immutable snapshot, while missing,
  empty, duplicate, disguised-extension, and inert-control-path mappings fail closed.
  `analyze-data` briefs derive the complete policy-clamped local runtime, dataset, output,
  artifact, and isolated exact-replay contract without hand-authored hashes.

- Add inert guided long-goal drafting for Scout, Builder, and public Researcher work. A
  private owner brief supplies ordinary objectives, done-when statements, dependencies,
  selected content-addressed inputs, and an effort choice; Pixel derives exact capabilities,
  outputs, independent verification, and budgets from enabled private policy, clamps every
  ceiling, rehashes selected objects, and atomically emits child requests, input manifests,
  a goal declaration, and one private review. It grants no execution, lease, scheduling,
  credential, external-effect, or completion authority.

- Repair the signed-release source proof so qualification no longer requires a commit to
  contain its own hash. The envelope now binds the qualified functional commit and exact
  packaged commit/tree separately; signing proves clean HEAD, ancestry, and an exact
  three-file ordinary-file evidence delta, requires the versioned audit and generated table
  in the archive, and rejects incomplete/ambiguous evidence or any post-qualification
  functional change. Before key use it also proves the complete archive source bytes,
  file/directory set, and normalized modes match the exact Git tree, allowing only the
  separately bound generated SBOM addition.
- Add a manual, private-runner product qualification matrix for manifest-pinned Ubuntu
  24.04 and Debian 12 VMs. It exercises the real appliance, sandbox, degraded recovery,
  encrypted backup/restore, exact rollback, loopback UI, and bounded removal without
  credentials, production Pixel mutation, or provider calls, and emits exact-source-bound
  private evidence under a strict schema.
- Refresh every Incus qualification fingerprint to the exact 2026-08-09 Ubuntu noble and
  Debian bookworm cloud image set after the prior public daily image aged out before launch.
- Exercise the gateway shared-secret boundary from new private client state: a forged token
  must be explicitly denied, while the correct loopback operator token must complete the real
  sandboxed agent turn. This follows OpenClaw's documented runtime contract that shared operator
  authentication may precede device identity, instead of misclassifying intended authority as a
  pairing bypass. Synthetic model context is large enough to carry the real agent/tool prompt.
- Repair the Pixel Doctor panel's corrupted loading placeholders and permanently reject common
  UTF-8 mojibake sequences in every browser asset.
- Make signed release-lifecycle and first-time owner usability evidence explicit, non-waivable
  promotion gates so the readiness index cannot turn green from implementation-only evidence.
- Add a distinct human-authenticated boundary to source, Operations, and ordinary Frontier
  approvals: real-terminal enforcement, pinned system tools, cached-auth invalidation,
  passwordless/root refusal, complete protected-object display, unpredictable hash-bound
  confirmation, terminal-safe JSON rendering, and normal-exit credential-cache
  invalidation. `--confirm` alone can no
  longer reach a broker or actuator.
- Replace obsolete manual-upgrade guidance with the implemented signed activation,
  update-bound rollback, interruption recovery, and exact cleanup workflow, and add a
  release-gate regression that rejects the stale pre-implementation claim.
- Install exact-version OpenClaw plugins through its peer-omitting managed npm path while
  retaining Pixel's independently hashed archive as the installed-file reference, and permit only
  archived `optional:true` npm-shrinkwrap package roots to be absent after npm's legitimate
  platform pruning; metadata-only optional records grant no omission. Npm's rewritten
  shrinkwrap is checked as a constrained semantic extension containing only absent,
  optional peer metadata; all archived code that remains
  installed stays byte-exact. The one OpenClaw-managed peer symlink must resolve to the exact
  package behind Pixel's version-pinned OpenClaw binary. Unbundled packages must match exact
  shrinkwrap registry identities and are sealed into a private whole-tree integrity receipt on
  first verified installation; subsequent preflights reject any drift or other extra payload.

- Add a deployment-owned Frontier live-qualification CLI with explicit short-lived
  ChatGPT-plan/credits or separately billed API consent, a fixed synthetic public capsule,
  no-spend prepare stage, exact second confirmation, one-call and token ceilings, fresh
  metered API worst-case cost authorization, immutable crash recovery, and content-free
  pass/fail/inconclusive receipts. Offline tests never contact a provider and cannot
  substitute for the still-required authorized live pass.
- Add a dependency-free loopback-only local control page with credential-free onboarding,
  content-free status, strict v1 schemas, and expiring SHA-256-bound single-use
  configuration, plan, and verification actions. Browser authority excludes activation,
  provider/source/SSH credentials, approvals, restore, and incident recovery/resume.
- Add owner-only no-follow state, a single-instance lock, immutable confirmed onboarding
  snapshots, bounded runtime/output/queues/logs/retention, DNS-rebinding/CSRF/request-
  framing defenses, accessible static UI checks, and 7,885-case local-control pressure
  coverage without provider calls.
- Add bounded 15-minute custom Frontier budget proposals for an existing private policy.
  The browser cannot apply or activate them; the trusted terminal rechecks the hidden
  policy path and exact bytes, writes a durable claim and exact backup, changes only
  `budgets`, and recovers interrupted edits without a provider call.
- Add a release-bound qualification matrix for both supported hosts, real systemd lanes,
  source capability profiles, and Doctor's advisory model-capacity classes. Add a
  content-free promotion-readiness command that requires two exact-commit passes and
  every host, recovery/security, live-provider, historical-secret, and licensing gate,
  plus 3,002 hostile-claim cases with zero private-evidence leaks or provider calls.
- Add a separate owner-only, disabled-by-default local-control policy for public registry
  update checks, signed encrypted backup creation, and reason-bound one-way emergency
  Operations/Frontier pause. Policy paths and recipients remain browser-invisible;
  update activation, restore, approval, and broker resume remain terminal-only.
- Project content-free Frontier provider mode and rolling job, token, failure, and cost
  budget usage/limits/remaining values into local control. Essential malformed or
  incoherent evidence makes Frontier unavailable, optional bad values fail to null, and
  private fields never enter the browser; exact approval remains outside it.
- Add credential-free Frontier route and local-budget selection. New managed setups default
  to eligible ChatGPT plan access plus Starter limits; separately billed API access remains
  explicit. Credentials stay terminal-only, custom private policies cannot be silently
  overridden, and status distinguishes off, prepared, active, and fail-closed unavailable
  evidence without confusing Pixel limits with provider billing or workspace controls.
- Separate policy-only Frontier preflight validation from broker startup so planning a
  disabled ChatGPT-prepared limb never opens or requires an authentication cache, while
  live broker startup still validates the private cache before serving work.
- Add bounded content-free local-action diagnostics backed by owner-only exact result/log
  receipts. Fixed categories and guidance expose no action identity, evidence hash, log,
  path, prompt, account, or credential; durable same-kind success resolves incidents,
  while missing required receipt coverage or altered, linked, incomplete, or retention-
  inconsistent retained evidence fails the projection closed.
- Add a local-only Pixel Doctor CLI and browser projection with rounded host-support, CPU,
  memory, free-storage, accelerator-vendor, container-readiness, and generated-model context
  tiers plus conservative
  model/context starting guidance. It starts no process, probes no network, contacts no
  provider, exposes no host/device/model identity, provider URL, path, process output, or exact hardware value,
  and never claims that an advisory model class is guaranteed to fit.
- Add an opt-in private-policy-gated local projection of pending exact sanitized Frontier
  capsules, hashes, classification, route, token ceiling, placeholders, and cost mode.
  Reads require a random process-lifetime token delivered in the terminal URL fragment,
  then are no-follow and response/retention bounded; the browser gains no approval,
  provider-call, plan-file, policy, credential, or generic command authority.
- Add a strict signed-release envelope covering the archive, SBOM, provenance,
  source commit/tree, release/compatibility hashes, host support, and upgrade floor;
  Candidate bundles can be signed only under a separate qualification namespace that
  grants no publication, staging, or activation authority. Production release signing
  still requires a Supported compatibility record. Add detached Ed25519 signing from private
  validated copies and no-extract/no-execute intake with path, archive, signature,
  compatibility, SBOM, provenance, and size defenses. Activation remains external.
- Add confirmed deployment-locked staging for eligible forward releases. It privately
  copies only reverified signed bytes under a deterministic full-hash candidate ID,
  revalidates idempotent requests, safely recovers exact interrupted copies, bounds
  retained candidates, and emits a path-free receipt stating no extraction or execution.
- Add deployment-locked compatibility rehearsal with safe private archive extraction,
  deterministic extracted-tree identity, exact host and pinned Node/Python checks, fixed
  JSON/Bash/JavaScript/Python syntax parsing, interrupted-tree recovery, idempotent drift
  detection, no network or candidate-program execution, and no active-deployment change.

- Add an isolated ChatGPT-authenticated Frontier mode for eligible Codex subscription
  access, while preserving explicit separately billed API-key authentication.
- Keep the refreshable ChatGPT CLI auth cache broker-owned and mode `0600`, reject
  symlinks, hard links, malformed/oversized caches, unsafe ownership or permissions,
  and publish only a non-secret authentication-mode receipt.
- Add a versioned roadmap with audited exit criteria for subscription-aware routing,
  customization tooling, and the stable local-agent appliance.
- Add immutable content-free local-versus-Frontier routing receipts, a broker-generated
  rolling token/budget summary, an agent read tool, and an operator `frontier-usage`
  command without projecting prompts, identifiers, job IDs, credentials, or accounts.
- Add the Pixel 3.5 customization-kit foundation: schema-backed offline projection limbs,
  a fixed read-only gateway adapter, signed hardened worker templates, canonical Ed25519
  pack metadata, separate disabled-by-default lifecycle operations, transactional rollback,
  automatic hardened worker/timer provisioning, projection-state recovery,
  dedicated non-login identities, enabled-only gateway ACLs, publisher/migration-bound
  upgrades, live systemd lifecycle qualification, exposure scoring, and hostile-pack pressure tests.
- Add signed Local capability, Operations action, and restrictive Frontier task packs;
  safe scaffolding commands; per-file/provenance-bound configuration; explicit private
  Operations target binding; restrictive Frontier composition; lifecycle cleanup and
  upgrade mapping preservation; and 40-class hostile policy-pack pressure coverage.
- Add Frontier policy/request v2 with broker-owned deterministic local-only, local-retry,
  operator-context, preview, propose, bounded-auto, and reject decisions; fresh
  content-free local receipts; forced safety/security approval; exact sanitized previews;
  and transparent unavailable/subscription/metered cost estimates and ceilings.
- Add private exact-binding Frontier cache and cross-process duplicate-spend prevention,
  including approval-time deduplication, TTL/entry limits, output revalidation, provenance,
  and conservative exclusions for confidential, forced-review, and degraded-quality work.
- Add local Frontier critique/final composition with exact result and finding-partition
  binding, content-free integration receipts, quality-regression downgrade, and aggregate
  provider-call/cache/savings/quality telemetry without task text or identifiers.

## 3.3.0 - 2026-08-08

- Add an optional local-first Frontier Limb for typed Codex plan review and failure
  triage through an isolated privacy compiler, exact-payload approvals, placeholder
  rehydration, budgets, revocable authority leases, tool-disabled ephemeral execution,
  and adversarial pressure tests.
- Add independently installed, checked, and vulnerability-audited Frontier plugin
  dependencies to the required CI gate.
- Add deterministic CycloneDX release SBOMs, SLSA v1 provenance statements, checksums,
  safer atomic artifact publication, and release-artifact regressions.
- Add dependency-update configuration, code ownership, sanitized issue forms,
  contribution guidance, support boundaries, and cross-platform release-file line endings.

## 3.2.2 - 2026-08-07

- Add every enabled Pixel limb tool through OpenClaw's additive `tools.alsoAllow`
  policy so the `coding` profile cannot silently remove Email, Calendar, or Operations.

## 3.2.1 - 2026-08-07

- Apply private no-attendee Calendar creates and ETag-bound time-only reschedules through
  a broker-enforced direct actuator, while keeping deletes, people, content, and recurring
  changes behind exact-proposal approval.
- Reuse immutable sanitized Gmail projections between refreshes, re-check unread state,
  move to a one-minute cadence, and timestamp snapshots on completion instead of start.
- Add reply-quality rules that require reading the original thread before drafting,
  respect owner-reported recent sends when a projection predates them, suppress internal
  tool narration, and forbid monitoring promises without a real monitor.
- Make the versioned release manifest authoritative for OpenClaw, official plugins,
  verified package metadata, runtime requirements, images, and generated defaults.
- Add a schema-backed Pixel/OpenClaw compatibility matrix, readable generated table,
  explicit legacy-manifest migration, and drift regressions.
- Verify the OpenClaw package and official plugin archives against both pinned SHA-256
  and authoritative npm SHA-512 integrity before installation.
- Add read-only `upstream check` plus explicit-channel `upstream prepare`, with
  independent package resolution, private opaque quarantine, stable-release filtering,
  deterministic reports, and no lifecycle execution or production access.
- Preserve numeric OpenClaw stable revisions such as `YYYY.M.P-N` across manifest,
  bootstrap, preflight, fixtures, and compatibility contracts.
- Add safe, tamper-evident npm extraction and a normalized `upstream diff` that compares
  CLI/config/gateway/plugin/session/sandbox/state contracts without running package code
  or copying source lines into evidence.
- Fail closed on new authority, removed denials, credential-path changes, and widened
  network/filesystem signals until each finding receives an explicit reviewed disposition.
- Pin the exact Node qualification runtime and add real supported/candidate OpenClaw
  installation, clean config/plugin/tool validation, gateway authentication, live limb,
  session-tree, sandboxed workspace, confinement, shutdown, and rollback probes.
- Add a disposable Incus matrix for Ubuntu 24.04 and Debian 12 containers plus a hardened
  systemd-capable Ubuntu VM, with private secret-scanned evidence and no production state.
- Select OpenClaw's coding tool profile explicitly and omit disabled custom-plugin entries,
  preserving useful workspace/CLI capability without stale-plugin warnings.
- Separate the active Pixel remote-ref release gate from exact, immutable legacy-history
  reporting so unrelated pre-Pixel artifacts stay visible without blocking Pixel releases;
  unknown, moved, or secret-bearing Pixel refs still fail closed.

## 3.2.0 - 2026-08-05

- Exhaustively paginate the default `in:inbox` and `in:sent` queries, publish page-level
  completeness evidence, and retain a high explicit safety ceiling with fail-honest truncation.
- Add bounded `offset`/`hasMore` tool pagination so Pixel can review every indexed message
  without placing an unbounded folder into a single model context.
- Resolve Gmail metadata before fetching Inbox content so messages carrying both Inbox
  and Sent labels remain metadata-only even when they fall outside the current Sent page.
- Include `pixel_gmail_sent` in configuration rendering, migration, disabled-limb denial,
  workspace migration, and prompt-injection evaluation contracts.

## 3.1.9 - 2026-08-05

- Add a bounded Sent-mail projection that requests Gmail metadata headers only, never
  fetches Sent message bodies, and lets Pixel reconcile leads against actual replies.
- Publish inbox and Sent query, limit, estimate, and truncation coverage so Pixel cannot
  claim a complete mailbox scan when the projection is stale, bounded, or incomplete.
- Add the repeatable OpenClaw upstream intake, compatibility, qualification, canary,
  evidence, and promotion implementation plan.
- Rewrite legacy secret-bearing Git history, remove obsolete branch pointers, preserve only
  signed and age-encrypted emergency evidence, and resynchronize deployment sources.
- Add an isolated remote-ref secret audit covering branches, tags, and GitHub pull-request
  heads, with permanent regressions for secret material reachable only through a PR ref.
- Require issuer-side credential invalidation in addition to access containment, and record
  the GitHub Support cleanup path for provider-managed refs and cached views.
- Wait for a yielded subagent's resumed parent turn before finalizing live evidence, and
  tighten the modular handoff case to verify the child artifact with the read tool only.
- Accept the live agent's semantically equivalent `Action requested: No` inbox wording
  without weakening Gmail-only tool-confinement checks.
- Ignore the launcher's interim CLI wait echo when capturing yielded sessions, and
  normalize presentation-only Markdown before comparing required response facts.
- Make the privileged runner-config read flow through an explicit unprivileged temporary
  file writer, preserving the atomic install while satisfying the release ShellCheck gate.

## 3.1.8 - 2026-08-05

- Bind rendered email-injection cases to a SHA-256 manifest accepted by the shared
  live evidence runner and require that same manifest during evaluation.
- Exercise the deployed `sessions_history` handler deterministically from the
  operator side, retain owner-only synthetic evidence on pass or failure, and keep
  the model-layer refusal/rejection plus same-tree coordination checks.
- Accept equivalent, evidence-rich success wording in Operations live scoring while
  retaining exact required-tool and unexpected-tool confinement gates.

## 3.1.7 - 2026-08-05

- Validate candidate OpenClaw configurations in an isolated, automatically removed
  state directory with explicit reviewed plugin roots, preventing scratch plans from
  polluting production config-audit baselines or emitting false anomaly warnings.

## 3.1.6 - 2026-08-05

- Add `pixel_limb_status`, a no-data capability endpoint that reports enabled modular
  limbs without invoking source, web, or Operations work.
- Apply unavailable-limb and no-cross-substitution guidance through the idempotent
  managed-workspace migration as well as fresh deployment templates.

## 3.1.5 - 2026-08-05

- Require an unavailable limb to fail plainly instead of probing or substituting an
  unrelated tool, and mark Operations inventory as task-scoped rather than a tool
  discovery mechanism.
- Preflight the exact pinned OpenClaw launcher, session-key support, deployment state,
  and Pixel agent before live evidence capture.
- Accept semantically equivalent negative-action wording in modular email evaluation
  while retaining exact facts, required-tool, and unexpected-tool gates.

## 3.1.4 - 2026-08-05

- Publish Operations requests and cancellations through a private temporary inode,
  explicit mode `0640`, and atomic no-overwrite link so the isolated broker can read
  them even under the gateway's restrictive service umask.
- Refresh OpenClaw's persisted plugin registry on release transitions and verify both
  Pixel custom plugins resolve to the active immutable release rather than a cached
  prior target of the moving `current` symlink.
- Retire exact agent-scoped sandbox containers during apply, rollback, and restore, and
  fail verification when a reserved container is duplicated, stopped, mislabeled, or
  still bound to a stale image digest or release.
- Made modular end-to-end prompts hash-bound and directly executable through the
  evidence-preserving live runner, and separated declared capability gaps from hard
  failures such as missing evidence or unexpected tool use.
- Stabilized the authenticated control-boundary probe against formatting-only model
  whitespace while continuing to reject any extra non-whitespace content.

## 3.1.3 - 2026-08-05

- Split Operations SSH transport authority from the non-login hostile-workload identity,
  closed direct managed-sudo and working-directory races, and added disposable plus live
  runner-boundary gates.
- Hardened Operations policy parsing, authority concurrency/recovery, download redirects,
  DNS and credential-query handling, expected hashes, output limits, process groups,
  archive paths, artifact descriptors, and configuration mutation checks.
- Added signed age-encrypted private-state backups, structural archive auditing, isolated
  rehearsal, canonical private onboarding preservation, transactional verified restore
  with automatic rollback, active-release manifest binding, global deployment locking,
  legitimate setgid state-directory preservation, and transactional
  gateway-credential rotation.
- Rebuilt the complete OpenClaw top-level/default-agent configuration from an explicit
  allowlist so ambient hooks, bindings, browser settings, workspaces, provider headers,
  and future keys cannot survive migration.
- Forced all-transitive SHA-256 hash checking for every Web Courier Python wheel during
  download, preflight, and install; restricted browser egress to globally routable,
  canonical destinations and an explicit public-web port allowlist.
- Added control-boundary, deployment-isolation, runner-boundary, recovery, concurrent
  pressure, source-fuzz, incident-response, and production acceptance evidence contracts.

## 3.1.2 - 2026-08-05

- Migrated the OpenClaw gateway from an unrestricted user unit to a hardened system
  service running as the unprivileged deployment owner, with masked home state,
  explicit read/write binds, loopback/token enforcement, and runtime verification.
- Rebuilt agents, providers, plugins, channels, skills, memory, tool policy, and Docker
  sandbox settings from explicit reviewed inputs so hostile legacy configuration cannot
  survive an upgrade.
- Added digest-bound custom gateway extensions, tree-scoped session coordination and a
  live unrelated-session canary test, plus non-root/capability/network/resource/mount
  inspection for running sandbox containers.
- Fixed incremental bootstrap so installing one missing utility does not attempt to
  replace an already working Docker CE/containerd stack with `docker.io`.
- Isolated Discord direct messages per account/channel/person and forced filesystem
  tools to remain workspace-only, closing two legacy-config privacy paths.

## 3.1.1 - 2026-08-05

- Added a bounded Operations-native job wait tool so Pixel can supervise real work
  without generic shell or process timers, and hardened the live evaluator to keep
  generic process tools forbidden throughout Operations workflows.
- Made live authority retries retain exact evidence-bound parameter constraints while
  rotating single-use lease identities, and aligned every generated runtime budget
  with its action policy ceiling.
- Added launcher compatibility preflight, passive inventory-aware evaluation, atomic
  workspace monitoring-policy migration, and regression coverage for each issue found
  during production acceptance.

## 3.1.0 - 2026-08-05

- Added Operations policy schema v2 with explicit environments, action effects,
  default authority, decision receipts, parameter-scoped grants, persistent execution,
  concurrency, runtime, output, artifact, and failure-circuit budgets.
- Added externally issued, expiring, single-use authority leases; production and
  change-tier automation now require a lease. Added operator authority inventory,
  audit, revocation, emergency pause, and resume controls.
- Added reusable host, repository, build/test, artifact, service, immutable deployment,
  exact package, and reboot-proposal action packs with private target mapping.
- Added transactional verification and automatic rollback for managed changes,
  root-owned verified package quarantine, no-follow artifact handling, and safe helper
  refresh for already enrolled runners.
- Expanded migration, policy, fuzz, runner, pressure, and live acceptance coverage and
  documented the complete autonomy, customization, deployment, and incident model.
- Expanded private-state backup to include isolated broker credentials, policy, host
  pins, authority/audit state, source tokens, and private deployment answers.

## 3.0.1 - 2026-08-04

- Bound every Calendar mutation to a broker-owned proposal snapshot and externally
  reviewed SHA-256, closing a review-to-execution file race.
- Made Operations artifact downloads HTTPS-only and added the live Operations harness
  tests to the default release gate.
- Restarted the long-running Operations Broker during upgrades so newly installed
  policy enforcement code takes effect immediately.

## 3.0.0 - 2026-08-04

- Added independently selectable email, Calendar, social, web, and Operations limbs,
  plus minimal, chief-of-staff, research, and engineering-operator capability profiles.
- Added an isolated Operations Broker with private policy/SSH authority, typed named
  actions, concurrent dependency workflows, exact-plan approvals, cancellation,
  identity pinning, output bounding/redaction, and replay/tamper resistance.
- Added dedicated least-privilege Linux runners, safe named hardware/process/I/O test
  suites, public-download SSRF defenses, non-executable quarantine, and hash-verified
  no-overwrite artifact transfer.
- Added aggressive Linux safety/capability tests, modular clean-room coverage, a threat
  model, customization guide, Operations runbook, and expanded client acceptance gates.

## 2.1.0 - 2026-08-04

- Expanded the live source-boundary corpus from four to eleven cases, adding Unicode
  obfuscation, Base64 and ROT13 payloads, link following, memory poisoning, claimed
  standing Calendar authority, and cross-source authority laundering.
- Normalized Unicode before risk analysis and added deterministic detection for encoded,
  indirect, cross-source, memory-writing, Calendar-mutation, and link-following attacks.
- Prevented Calendar proposals and the separate actuator from accepting sanitized
  projection placeholders, after an end-to-end move test exposed a title-overwrite risk.
- Added a safe workspace migration that replaces legacy direct-Calendar and direct-X
  guidance without overwriting unrelated personal instructions, and removed the legacy
  direct `xfeed.sh` path.

## 2.0.0 - 2026-08-04

- Replaced Pixel's direct Gmail/Calendar plugin with an octopus-style Source Broker
  running under a dedicated system identity that exclusively owns the OAuth token.
- Added deterministic prompt-injection detection and redaction, bounded typed email,
  Calendar, and social projections, atomic read-only handoff, provenance hashes, and
  stale-projection reporting; raw bodies, HTML, attachments, and event descriptions are
  never stored for Pixel.
- Replaced direct Calendar mutations with non-executing proposals and a separate,
  operator-confirmed one-shot actuator that disables attendee notifications.
- Added the live email prompt-injection harness and transcript-aware release gate, plus
  Source Broker isolation, sanitization, and clean-room deployment tests.

## 1.1.0 - 2026-08-02

- Added a reproducible Playwright/Chromium Web Courier for text, link, screenshot, and
  rendered-HTML navigation while keeping Pixel's tool sandbox networkless.
- Hardened the host bridge against symlink traversal, private-address SSRF, URL-secret
  logging, oversized queue entries, DNS rebinding, shared browser state, downloads, and
  service workers; the service cannot see unrelated files in the owner's home directory.
- Added multi-query research policy, source-verification guidance, private campaign
  ledgers, expanded SearXNG fallbacks, and explicit web prompt-injection handling.
- Integrated browser dependencies, systemd isolation, service health probes, managed
  workspace files, automatic rollback, release contracts, and clean-room tests.

## 1.0.0 - 2026-07-31

- Recast the repository as the sanitized, reusable Pixel deployment kit.
- Added prepared/reference profiles, guided onboarding, generated systemd configuration,
  pinned OpenClaw dependencies, reproducible sandbox, and optional reference services.
- Added atomic plan/apply/verify/rollback lifecycle and private-state backup tooling.
- Added isolated Gmail read and confirmed Calendar mutation plugin plus OAuth helper.
- Added clean-room end-to-end tests, security gates, client handoff, and release packaging.
