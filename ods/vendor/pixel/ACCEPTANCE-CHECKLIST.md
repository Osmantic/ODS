# Deployment acceptance checklist

- [ ] Release archive checksum and repository tag match the handoff record.
- [ ] Supported Linux host, Node, Docker, OpenClaw, and plugin versions pass bootstrap.
- [ ] `./pixel plan` schema and secret checks pass; operator reviewed its hash/summary.
- [ ] `./pixel ui` listens only on exact IPv4 loopback, rejects hostile Host/origin/body
  framing, exposes no credential or generic command field, and serves only local assets.
- [ ] Local onboarding preserves advanced private fields without returning them; private
  files are owner-only and no-follow; stale revisions, symlinks, unknown fields, and
  credential-shaped fields fail closed.
- [ ] `configure`, `plan`, and `verify` require an expiring exact preview, execute once,
  survive concurrency/restart tests, bound output/runtime/queue/log retention, and expose
  only content-free results. Activation, approval, and recovery/restoration authority
  remain absent.
- [ ] Optional update-check, signed encrypted backup, and one-way Operations/Frontier
  pause are disabled by default, privately policy-bound, serialized, and exact-preview
  tested. Backup path/recipient stay browser-invisible; policy survives a signed recovery
  rehearsal; update activation, restore/decryption, approval, and resume remain absent.
- [ ] Update and migration status is read-only and content-free; strict bounded workspace
  inspection fails closed on links, ownership, permissions, unexpected names, retention
  overflow, and inconsistent lifecycle sets. Paths, hashes, signer identity, receipt
  contents, activation, rollback, recovery, cleanup, and migration authority stay absent.
- [ ] Recovery guidance distinguishes backup creation from signature/decryption validation,
  isolated rehearsal, and restore; it treats recorded pause success as unverified current
  state. Paths, recipients, hashes, reasons, identities, artifacts, private evidence,
  decryption, restore, recovery, and resume authority stay outside the browser.
- [ ] Local-control schema, accessibility, Windows/Linux unit, and 7,885-case pressure
  suites pass with zero provider calls and zero credential-canary projection.
- [ ] Qualification matrix matches both supported hosts, all source capability profiles,
  and Doctor's five advisory memory/model classes; every row refuses to guarantee fit
  and requires synthetic task, local latency/memory, and owner quality validation.
- [ ] Promotion readiness binds a clean exact commit/tree, raw manifest and matrix hashes,
  two consecutive passes, and all required gate evidence. Missing, malformed, duplicated,
  unbound, or blocked host/systemd, recovery/security, signed-release-lifecycle,
  first-time-owner-usability, live-provider, historical-secret, or license evidence keeps
  publication blocked without projecting private paths,
  identities, model/provider content, or credentials; the 3,002-case hostile claims
  pressure suite passes with zero private-evidence leaks and zero provider calls.
- [ ] Signed release intake binds the exact packaged commit/tree and the preceding
  qualified functional commit separately. Signing from a clean exact HEAD proves
  ancestry, exactly three changed ordinary evidence files, one unambiguous Supported
  compatibility record and versioned audit, and a byte/mode/file/directory-exact archive
  of that Git tree plus only the separately bound generated SBOM. Equal, unknown,
  non-ancestor, incomplete, deleted, extra, changed, or post-qualification functional
  source fails before the release key is used.
- [ ] Frontier provider/auth mode and rolling job/token/failure/cost budget totals,
  ceilings, and remaining capacity are content-free; malformed counters and arbitrary
  private telemetry fields fail to null and never enter the browser.
- [ ] Custom Frontier budget drafting is available only for a private custom policy;
  strict range/relation/billing checks, private 15-minute retention, hidden-path hash
  binding, policy/onboarding drift, expiry, replay, tamper, links, broad permissions,
  durable pre-edit claim and pre/post-policy interruption recovery, and exact
  backup/receipt behavior pass. The browser cannot apply or activate the
  proposal, the terminal edits only `budgets`, and neither step makes a provider call.
- [ ] Frontier review display is private-policy-gated and disabled by default; only exact
  awaiting-approval sanitized capsules and allowlisted metadata project through bounded
  no-follow reads and text-only rendering. Tamper, links, excess retention, capsule/hash
  mismatch, unknown fields, and malformed cost/counter cases fail closed; browser
  approval remains false and terminal approval rechecks the private broker plan. A
  session cookie, missing/wrong/stale/duplicate review token, and token from another
  process cannot read capsules; the process token never appears in HTTP output.
- [ ] Source, Operations, and ordinary Frontier approval fail outside a controlling
  terminal and under root/passwordless sudo; use the pinned system path and
  `/usr/bin/sudo`; invalidate cached authentication before and after; require fresh
  password-backed administrator authentication before displaying the exact protected
  object and requiring an unpredictable hash-bound phrase; and never reach an actuator or broker after a
  wrong/interrupted phrase. The broker still rejects hash, policy, expiry, cancellation,
  replay, cache, or budget drift after successful human authentication; protected JSON
  renders control and directionality characters as visible ASCII escapes.
- [ ] `./pixel apply --confirm` and `./pixel verify` pass.
- [ ] Gateway is a hardened system service running as the unprivileged client identity and survives a controlled restart.
- [ ] Gateway listens only on loopback, rejects missing/invalid API tokens, and cannot see unrelated home files.
- [ ] Only onboarding-approved `gatewayExtensions` survive configuration rendering.
- [ ] Source, Operations, and Frontier plugins resolve to the active immutable release directory
  and version after the persisted OpenClaw plugin registry is refreshed.
- [ ] Canonical private onboarding is mode `0600`, matches the reviewed input, and appears in the signed recovery rehearsal.
- [ ] Session list/history/send are tree-scoped, spawned-session coordination works, and unrelated exact session keys are forbidden.
- [ ] Web Courier is a hardened system service running as the deployment owner and cannot read unrelated home files.
- [ ] Source Broker runs as its dedicated system user and its OAuth file is unreadable by the gateway owner.
- [ ] Gateway environment contains projection paths but no Google token path.
- [ ] Email, Calendar, and social projection files declare `rawContentStored: false` and are not gateway-writable.
- [ ] A live Pixel sandbox tool turn runs in the exact agent-scoped container; its image
  digest/release match the active deployment and it is network-none, read-only-root,
  non-root, capability-free, and limited to its workspace.
- [ ] Search and model endpoints work without exposing host credentials to the sandbox.
- [ ] Web Courier renders a harmless public JavaScript page and refuses loopback/private URLs.
- [ ] Browser results are presented as untrusted content and courier logs omit URL queries.
- [ ] Client owns the dedicated Google project and has approved only the documented scopes.
- [ ] Gmail Inbox and Sent listings exhaust every API page for the configured folder queries;
  bounded tool pagination reaches `hasMore=false` with fresh `completeWithinQuery=true` evidence.
- [ ] Gmail projection inbox/search/read work and expose no raw body, HTML, or attachment content.
- [ ] Sent reconciliation uses metadata-only Gmail fetches even for dual Inbox/Sent labels,
  exposes no Sent body, and reports query, pages, estimate, and truncation evidence.
- [ ] Calendar projection list works; a private no-attendee create and one time-only reschedule apply directly only when enabled and return an actuator result. Consequential proposals make no change until a protected snapshot's exact SHA-256 is approved. Update/delete use the projected ETag, and concurrent or uncertain actions cannot replay.
- [ ] Live email prompt-injection suite passes with no non-projection tool calls or side effects.
- [ ] Messaging/channel tools remain denied unless separately approved.
- [ ] `./pixel limbs` matches the approved capability profile and every disabled limb's tools are denied.
- [ ] Every custom limb verifies against an owner-maintained Ed25519 allowed-signers file,
  installs disabled, revalidates before activation, and leaves no pack, unit, identity,
  ACL, onboarding, or projection residue after the tested disable/remove lifecycle.
- [ ] Signed Local policy packs name only their containing limb's observe-only tools;
  signed Operations packs remain absent until explicitly mapped to base-policy targets;
  signed Frontier packs only reduce an existing typed task's authority and budgets.
- [ ] Policy-pack path escape, file-hash drift, tree/version provenance drift, shell helper,
  foreign target/tool, production standing grant, generic Frontier task, and base-policy
  widening fixtures all fail closed; the 40-class limb-kit pressure evaluation passes.
- [ ] Frontier mode, when enabled, refuses a public primary-model endpoint; its dedicated
  service owns the provider key/policy/plans/approvals/archive/authority/runtime and the
  gateway can access only request/cancel and result/event projections.
- [ ] The Frontier provider-key directory and key are root-owned; the broker can read the
  key through its group but neither the broker nor gateway can replace/read it beyond
  its intended direction.
- [ ] The selected Frontier auth mode is explicit: `chatgpt` uses only a broker-owned,
  single-link, mode-`0600` saved CLI auth cache in a mode-`0700` directory; `api-key`
  retains root-owned input and per-job ephemeral login. The gateway can read neither.
- [ ] ChatGPT auth-cache symlink, hard-link, broad-mode, wrong-owner, malformed, empty,
  oversized, and post-refresh permission cases fail closed without credential output.
- [ ] Frontier request/cancel projections have an enforced storage quota or dedicated
  quota-limited filesystem plus disk-usage alerting, and exhaustion leaves private state
  and authority ownership intact.
- [ ] Frontier exposes only typed plan-review and failure-triage submissions; generic
  prompt/file/repository/URL/message/log forwarding is absent and source content cannot
  authorize a submission.
- [ ] Restricted and declared mandatory never-egress categories fail; credential,
  quarantine, reserved-placeholder, duplicate-JSON, malformed, oversized, and
  zero-width-obfuscation cases fail before a provider call.
- [ ] Supported email, phone, private-IP, URL, and local-path identifiers are absent from the
  exact provider capsule and only known placeholders rehydrate after output validation.
- [ ] Preview invokes no provider; confidential always awaits exact-plan approval;
  expiry, cancellation, policy/request drift, replay, duplicate/concurrent approval,
  exhausted budgets, and failure circuits fail closed.
- [ ] Every v2 Frontier request has a fresh, unique, content-free receipt with coherent
  attempt/outcome/reason fields. The broker produces exactly one of `local-only`,
  `local-retry`, `operator-context`, `preview`, `propose`, `bounded-auto`, or `reject`;
  the first three create no plan or provider usage, and safety/security always proposes.
- [ ] Preview/proposal results expose the exact sanitized capsule. Metered estimates use
  only dated operator-supplied rates and fail the rolling cost ceiling before execution;
  subscription/unavailable modes never fabricate currency amounts.
- [ ] Eligible cache keys bind capsule, policy, provider, auth mode, model, output cap,
  and output contract. Expiry, policy/model change, tamper, approval-required work,
  safety/security review, and quality-circuit state cannot reuse a cache entry; exact
  simultaneous and approval-time duplicates produce at most one provider call.
- [ ] A successful result is locally verified and finalized with an exact result hash and
  complete finding partition. No conclusion or verification prose enters the receipt,
  archive, events, or usage projection; tamper/replay/missing-result cases fail closed,
  and regression evidence downgrades bounded automation and cache reuse.
- [ ] Content-free usage distinguishes provider calls, cache hits, local attempts,
  routing decisions, actual tokens, estimated costs, avoided calls/tokens/cost, quality,
  and open circuits without exposing task identifiers or text.
- [ ] Ephemeral Codex execution ignores user config/rules and has shell, web, browser,
  computer, image-generation, apps, plugins, hooks, goals, and multi-agent facilities
  disabled; the offline config/tool-manifest probe and structured-output/usage receipts
  pass using synthetic low-sensitivity data.
- [ ] `security-evals/frontier-pressure/` passes and a disabled Frontier limb has no
  plugin entry, tools, service requirement, or credential path in the gateway environment.
- [ ] Operations Broker, when enabled, runs as a separate identity; gateway cannot read policy, key, pins, approvals, or private state.
- [ ] Operations policy is schema v2; every target has a reviewed environment and every action accurately declares effect, default authority, idempotence, reversibility, verification, and rollback metadata.
- [ ] Every Operations target has a verified expected hostname and pre-trusted host-key pin; changed identities fail closed.
- [ ] Reusable action-pack placeholders resolve only to intended private targets; runner action/managed configuration is root-owned and validates through the dedicated identity.
- [ ] The broker SSH key is bound to the root-owned `pixel-ops-transport` forced-command dispatcher; arbitrary shell, option injection, and cwd escape probes are rejected.
- [ ] Hostile work runs as non-login `pixel-runner`; it cannot use sudo or the broker key, while transport-routed unprivileged and approved managed actions still succeed.
- [ ] Authority inventory and audit work; decision receipts identify action, target, source, grant/lease, constraints hash, and applied resource limits.
- [ ] Standing grants are action/target/environment scoped. Elevated grants have exact parameters and execution, concurrency, runtime, output, artifact, and failure budgets.
- [ ] Production and `change` automation fail without a temporary lease; lease expiry, revocation, ID reuse, parameter mismatch, and exhausted budgets fail closed.
- [ ] Emergency pause blocks new jobs and cancels read/staging work; resume requires an external reason. A managed transaction still verifies or rolls back safely.
- [ ] Named identity, health, I/O, and process tests succeed on disposable runner state.
- [ ] A multi-target dependency workflow runs independent steps concurrently and preserves bounded progress evidence.
- [ ] Cancellation terminates a long-running process group; a failed sibling stops dependent/running work.
- [ ] Hostile machine output is flagged/redacted and causes no follow-on tool or approval.
- [ ] Public download refuses plaintext HTTP, fragments, credential queries, private/loopback resolution, and redirects outside the reviewed source scope; a correct expected SHA-256 stages non-executable content and an incorrect hash leaves no artifact.
- [ ] Verified artifact transfer refuses overwrite, symlink, hash mismatch, and oversize input and never executes content.
- [ ] Repository status/fetch/allowed-ref/fixed recipe and artifact checksum/compare/collect/no-symlink archive work only inside configured roots.
- [ ] Disposable service restart and immutable deployment activation verify success; deliberately failed verification automatically restores prior state.
- [ ] Exact staged package hash is copied into root-owned quarantine before a fixed install; a disposable install and rollback pass, while symlink/replacement/hash mismatch fail.
- [ ] Reboot and arbitrary shell remain proposals and are not executed merely for acceptance coverage.
- [ ] Break-glass shell remains pending until an operator approves the exact immutable plan hash; replay/tamper fail.
- [ ] Rendered `security-evals/operations-live/` prompts pass through the installed Pixel agent, with transcripts retained outside Git.
- [ ] Encrypted private-state backup, checksum, and detached signature exist; correct-key/trusted-signer validation and an isolated rehearsal pass, while wrong-key, absent/forged/non-canonical signature, corruption, special member, and path-escape cases fail; forced verification failure restores exact prior state and the restoration owner is named.
- [ ] A knowledge-bearing backup quiesces the exact active Deep Work units and excludes the external vault key; restoring an older generation propagates live tombstones, removes deleted ciphertext, rotates to the current key, deep-audits before activation, and rolls back on any wrong key, conflict, tamper, or verification failure.
- [ ] First-time knowledge setup requires a fresh exact review, keeps its generated key outside the vault and backup roots, never prints or overwrites the key, rejects substituted parents or unexpected staged files, and resumes only its exact private durable stage.
- [ ] Gateway credential rotation succeeds without disclosure; a forced verification failure retains the preceding credential, and a concurrent deployment mutation is rejected by the global lock.
- [ ] Client received operations, incident, upgrade, rollback, and support instructions.
- [ ] No previous client's token, memory, session, identity, or model credential is present.
