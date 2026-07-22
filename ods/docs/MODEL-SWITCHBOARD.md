# ODS Model Switchboard PR Plan

Date: 2026-07-19

Last audited: 2026-07-19; refreshed 2026-07-20 against post-merge `main` `5dd6f72d` (77-PR fix sweep + #1888 + #1766 + #1711 + #1887 + #1724 all merged)

Status: proposed implementation stack. The lifecycle foundation ([#1711](https://github.com/Osmantic/ODS/pull/1711)), swap-safety manifest contract ([#1766](https://github.com/Osmantic/ODS/pull/1766)), transactional swap sync ([#1887](https://github.com/Osmantic/ODS/pull/1887)), and model management UI/actions ([#1724](https://github.com/Osmantic/ODS/pull/1724)) are already merged; the state store, data plane, reconciler, and consumer migrations in this plan are not implemented.

Goal of record (local planning source): `C:\Users\conta\Desktop\ODS-MODEL-SWAP-DESIGN.md`

Portability rule: the absolute paths in this header are evidence pointers for this workstation, not execution dependencies. PR 1 must add the accepted version of this plan at `ods/docs/MODEL-SWITCHBOARD.md`; subsequent PRs reference that repository file and update its decision/status table in place.

Research inputs:

- ODS PR [#1724](https://github.com/Osmantic/ODS/pull/1724): merged into `main` 2026-07-19 (merge commit `5dd6f72d`); its former integration-vehicle role is retired
- ODS GitHub `main` at `5dd6f72d` (contains the merged 77-PR fix sweep, #1888, #1766, #1711, #1887 content, and #1724)
- Fleet harness `main` at `cb84c609fe897c1361967a844521fae7ab830848`
- Lemonade `main` at `16dc27d2f3e249f2d826c97cde3f742df3b9d593`
- Local Lemonade audit: `C:\Users\conta\Documents\Codex\2026-07-06\cl\LEMONADE_ROUTER_AUDIT.md`
- [Lemonade router milestone #2389](https://github.com/lemonade-sdk/lemonade/issues/2389)
- [Lemonade classifier wiring PR #2727](https://github.com/lemonade-sdk/lemonade/pull/2727)
- [Lemonade router LRU isolation PR #2729](https://github.com/lemonade-sdk/lemonade/pull/2729)
- [Lemonade filtered-classifier bug #2748](https://github.com/lemonade-sdk/lemonade/issues/2748)
- [Lemonade v11.0.0 release](https://github.com/lemonade-sdk/lemonade/releases/tag/v11.0.0)
- [Lemonade router same-name re-pull fix #2703](https://github.com/lemonade-sdk/lemonade/pull/2703), commit `5cf1fd76d47a35cc5d413aa7e59bf7e2a74ba61f`
- [ODS swap-safety contract PR #1766](https://github.com/Osmantic/ODS/pull/1766)
- [LiteLLM proxy documentation](https://docs.litellm.ai/)

The SHAs above freeze the research snapshot only. Each implementation PR must branch from the then-current pushed `main`, record its own base SHA in the PR body, and rerun conflict/reference checks.

## 1. Decision

Build an ODS-owned Model Switchboard around one stable public model name:

`ods/current`

Every ODS application sends that model name to the existing LiteLLM gateway. LiteLLM forwards to a small ODS-owned model-router service. The model-router reads one versioned model-state record and forwards each request to the concrete backend and runtime model currently proven healthy.

Lemonade's `collection.router` is adopted as the Lemonade adapter's backend-native implementation, not as the ODS-wide control plane. This preserves one cross-platform ODS contract while still gaining deterministic backend routing and `x_lemonade_route` attestation on Lemonade v11-capable hosts.

This is the target request path:

```text
Hermes / Open WebUI / Perplexica / OpenCode / OpenClaw / ODS Talk
                              |
                      model = ods/current
                              |
                    LiteLLM auth and policy
                              |
                       ODS model-router
                              |
                 active route from model-state.json
                    /         |          \
          llama.cpp       Lemonade       HipFire
          concrete ID     ODS-Route-N     concrete route
                          collection.router
```

The host agent remains the mutation authority. A model swap becomes:

1. Validate the requested model and every enabled consumer's capability floor.
2. Record the desired model while leaving the old active route intact.
3. Ask the selected runtime adapter to stage/load the concrete model.
4. Prove runtime identity and a real completion.
5. Publish any backend-native alias, including Lemonade `collection.router`.
6. Atomically flip the ODS active route.
7. Emit completion evidence and update legacy compatibility projections.
8. Drain/unload the old model when safe.

On failure, the active route never changes, or is atomically restored to the previously proven route.

### 1.1 Scope boundary

In scope:

- Local chat/instruction model download, activation, routing, rollback, and deletion.
- Container and native llama.cpp, Lemonade, and HipFire text-generation backends.
- Every installed ODS application that consumes the local text model.
- Existing local, Lemonade, and hybrid installs. In hybrid mode, `ods/current` means the active local model; named cloud models stay separate.

Out of scope for this series:

- Switching `ods/current` between local and cloud providers. Cloud-only mode keeps its existing static LiteLLM provider routes and must pass no-regression tests.
- Embedding, reranker, speech, image-generation, and ComfyUI model lifecycle.
- Automatic model selection, classifier routing, LLM-as-router, and policy-driven smart routing.
- New role aliases such as `ods/agent` or `ods/vision` until `ods/current` is stamped green.

## 2. Why not use Lemonade or LiteLLM alone?

### Lemonade alone

Lemonade's deterministic `collection.router` behavior is a strong fit for AMD/Lemonade hosts. It accepts a collection name, rewrites the request to a concrete candidate, and returns route metadata. Safe same-name re-pull is release-dependent and is not present in v11.0.0, so PR 6 uses immutable per-route collections there. ODS also runs llama.cpp natively and in containers, macOS Metal, HipFire, and cloud routes. A Lemonade-only control plane would preserve the current platform asymmetry.

### LiteLLM alone

LiteLLM already provides the public gateway and model aliases, but ODS v1.81.3 is configured from a read-only YAML mount with no database-backed management plane. Today that YAML contains the concrete model ID, so changing the alias still requires rewriting the file and restarting LiteLLM. Enabling LiteLLM's database management stack solely for one mutable local alias would add a database, migration, and admin surface to every ODS installation.

Keep LiteLLM for auth, OpenAI compatibility, policy, and provider translation. Put ODS's small mutable routing decision behind it, where ODS can test and version the behavior independently.

## 3. Public contracts

### 3.1 Stable names

- Canonical public alias: `ods/current`
- Compatibility aliases for one deprecation cycle: `default` and the existing wildcard route
- Lemonade v11.0-safe registry convention: immutable `user.ODS-Route-<routeSeq>` collections, requested as `ODS-Route-<routeSeq>`
- Optional future Lemonade stable registry name: `user.ODS-Current`, only after the installed release proves safe same-name router re-pull/overwrite
- Concrete backend IDs remain internal and are shown in ODS UI/API as route details.

Role aliases such as `ods/chat`, `ods/agent`, and `ods/vision` are deferred until `ods/current` is fleet-proven. The state schema should allow roles without exposing them in the MVP.

### 3.2 State record

Path: `data/model-state.json`

```json
{
  "schema": "ods.model-state.v1",
  "seq": 42,
  "routeSeq": 17,
  "operation": {
    "id": "uuid",
    "phase": "serving",
    "requestedModelId": "qwen3.5-9b",
    "startedAt": "2026-07-19T00:00:00Z",
    "error": null
  },
  "desired": {
    "catalogId": "qwen3.5-9b"
  },
  "active": {
    "routeSeq": 17,
    "catalogId": "qwen3.5-9b",
    "runtimeModelId": "Qwen3.5-9B-Q4_K_M",
    "publicModel": "ods/current",
    "backend": {
      "kind": "lemonade",
      "endpointId": "lemonade-container",
      "nativeRoute": "ODS-Route-17"
    },
    "contextLength": 131072,
    "capabilities": {
      "chat": true,
      "tools": true,
      "vision": false,
      "agentViable": true
    },
    "verifiedAt": "2026-07-19T00:00:00Z",
    "proof": {
      "identity": "Qwen3.5-9B-Q4_K_M",
      "completion": true
    }
  },
  "history": [
    {
      "routeSeq": 16,
      "catalogId": "phi-4-mini",
      "runtimeModelId": "Phi-4-mini-Q4_K_M",
      "verifiedAt": "2026-07-18T23:00:00Z"
    }
  ],
  "availability": {
    "mode": "serve_active",
    "queueDeadline": null
  }
}
```

Rules:

- The host agent is the only writer.
- Writes use temp file, flush, fsync where available, and atomic replace.
- `seq` increments for every state/event mutation. `routeSeq` increments only when the active route changes. UI freshness uses `seq`; route attestation uses `active.routeSeq`.
- `active` remains the last proven route while `desired` is loading.
- `backend.endpointId` must resolve through the model-router's static allowlist. State never contains an arbitrary upstream URL, credential, or header.
- `history` retains the last 10 verified active-route snapshots. Rollback selects a history entry and runs it through the normal reconciler; it never edits `active` directly.
- `.env`, `models.ini`, Hermes YAML, and other legacy values are derived compatibility outputs during migration, never competing sources of truth.
- In `observe` mode, the existing activation transaction stays authoritative and state is written only after that transaction proves success. In `enabled` mode, the reconciler and atomic route flip are authoritative.
- A reader keeps one last-known-good verified snapshot in memory. Malformed or missing state never promotes `desired`; startup reconstruction from runtime plus `.env` is allowed only when no v1 state has ever been committed.

### 3.3 API and events

Product API:

- `GET /api/models/state`: sanitized desired/active/history summary and capability impact
- `GET /api/models/events`: SSE stream keyed by `seq` and operation ID; retains at most 1,024 events for 15 minutes, emits a heartbeat every 15 seconds, supports `Last-Event-ID`, and sends a `model.state.snapshot` event before live events when the requested ID is absent or expired
- Existing `POST /api/models/{id}/load`: unchanged user contract; backed by the reconciler
- `POST /api/models/rollback`: activate a requested verified history entry, defaulting to the newest, through the same reconciler
- `GET /api/models/routes/{probeId}`: authenticated dashboard-api proxy to bounded in-memory route evidence for fleet validation

Host-agent API:

- Existing `POST /v1/model/activate`: sole activation mutation endpoint
- New `POST /v1/model/rollback`: validates a history `routeSeq` and invokes the same reconciler
- New `GET /v1/model/state`: authenticated host-side diagnostic view

Event sequence:

```text
model.swap.started
model.swap.staging
model.swap.verified
model.route.flipped
model.swap.completed
```

Failure sequence:

```text
model.swap.failed
model.rollback.started
model.rollback.verified
model.rollback.completed
```

### 3.4 Route evidence

The model-router adds these response headers where the protocol permits:

- `X-ODS-Request-Id`
- `X-ODS-Requested-Model`
- `X-ODS-Routed-Model`
- `X-ODS-Backend`
- `X-ODS-Route-Seq`

Consumer-visible model identity is stable by contract: the router rewrites the top-level `model` field in non-streaming responses and every SSE chunk to the requested public alias (`ods/current`, or the compatibility alias the client used). Concrete identity is available only through the authenticated state API, ODS route headers, and bounded route-evidence records. The raw upstream body is retained only inside the request lifetime for parsing/forwarding and is never logged.

For Lemonade, capture and preserve `x_lemonade_route` and the `X-Lemonade-Route` header.

For end-to-end app probes that do not expose upstream headers, the fleet prompt carries a signed marker: `[ODS_PROBE id=<lowercase-uuid> sig=<base64url>]`. The signature is unpadded base64url HMAC-SHA256 over the lowercase UUID bytes using the run-scoped `ODS_FLEET_PROBE_KEY`. The model-router records evidence only when that key is configured, the marker has exactly one canonical UUID/signature pair, and constant-time verification succeeds. Production installs without that test-only key ignore markers and expose no probe lookup data.

The model-router keeps at most 2,048 evidence records for 15 minutes in memory. Each record contains only probe UUID, timestamp, consumer-visible requested alias, concrete route, backend, `routeSeq`, status, and response model. It never stores prompt text, messages, generated text, tokens, API keys, cookies, or authorization headers. `/internal/route-evidence/{probeId}` is reachable only on the Compose network and requires `Authorization: Bearer <ODS_ROUTER_INTERNAL_KEY>`; dashboard-api exposes the authenticated product proxy above.

### 3.5 Reload behavior

- If the old runtime can remain serving while the new model stages, `availability.mode=serve_active` and requests continue on `active.routeSeq` until the atomic flip.
- If a single-model runtime must stop serving, set `availability.mode=queue` before stopping it and queue new requests for a bounded default of 60 seconds.
- On queue timeout, return HTTP 503 with `code=model_swap_in_progress`, `Retry-After`, operation ID, and current phase.
- Existing in-flight requests are allowed to finish where the backend supports it.
- Do not silently retry a request against a different model after generation has begun.

### 3.6 Router security boundary

- The model-router is an internal core service with `expose`, no host `ports` mapping, non-root execution, `no-new-privileges`, dropped Linux capabilities, read-only root filesystem, and a read-only state mount.
- Only explicit OpenAI-compatible paths and methods are forwarded. Unknown paths, absolute-form URLs, WebSocket upgrades, and arbitrary CONNECT/forward-proxy behavior are rejected.
- Hop-by-hop headers and client-supplied upstream authorization are stripped. The router injects backend credentials from its own environment/config.
- `endpointId` resolves to a startup-validated allowlist. Local endpoint IDs may resolve to Compose DNS or the installer-created native host bridge; user-controlled state cannot select an arbitrary network destination.
- Request body, header, queue depth, connection, and timeout limits are explicit and covered by negative tests.

### 3.7 Model viability contract

"Viable" is product data, not a harness exception. Each catalog model exposed for local activation must publish:

- Backend/runtime compatibility and downloadable artifact identity
- Estimated disk, RAM, and VRAM requirements
- Context length and required capability flags (`chat`, `tools`, `vision` where applicable)
- `agentViable`, a reason code, evidence timestamp, tested runtime family/version, and catalog evidence revision

A release-campaign candidate is viable on a host only when the product API reports that it fits the host, can be acquired by that runtime, and satisfies every enabled consumer's declared capability floor. If Hermes/ODS Talk is enabled, that includes `agentViable=true` and the 65,536-token context floor. A model that cannot reliably follow the Hermes instruction contract is marked non-agent-viable in catalog metadata and visibly blocked for that consumer; it is not added to a harness exclusion list. A tool-capable model receives a tool-aware probe that accepts a valid tool-call-then-answer sequence. Other consumers may still use a non-agent-viable model only when their manifests do not require agent viability.

The harness obtains the candidate list, compatibility decision, reason codes, and evidence revision from the authenticated product API. It may select among product-approved candidates for host variety, but it may not override a product rejection or maintain model-name allow/deny lists. If fewer than six distinct models are viable for an enabled host, the release campaign is blocked until product catalog entries or product capability metadata are corrected and tested.

## 4. Existing PR disposition

| PR | Decision | Reason |
|---|---|---|
| [#1711](https://github.com/Osmantic/ODS/pull/1711) | Landed prerequisite | Its lifecycle lock, host-agent hardening, and model-management foundations are reused. |
| [#1766](https://github.com/Osmantic/ODS/pull/1766) | Landed prerequisite | It already defines `ods/current`, manifest LLM consumption, `route`, `pinning`, capability floors, and dynamic probe discovery. Keep `pinning: none` for stable-alias consumers; do not introduce a conflicting `stable_alias` enum. |
| [#1724](https://github.com/Osmantic/ODS/pull/1724) | Merged into `main` 2026-07-19 | Its model run/delete actions, bootstrap-download activation conflict guard, and Windows Lemonade runtime-ensure endpoint are on `main`. New slices target `main` directly and fleet-test from their own pushed branches. |
| [#1887](https://github.com/Osmantic/ODS/pull/1887) | Landed prerequisite; its consumer stages are superseded by this series | Its activation transaction, lifecycle lock, snapshot/rollback proof machinery, and container-state tracking are reused by PRs 1-2. Its per-consumer rewrite stages (Hermes patch, Perplexica sync, OpenCode rewrite, OpenClaw recreate, LiteLLM refresh) are exactly what PRs 4A-5C migrate away from and PR 7 deletes; no new hardening of those stages should merge while this series is active. |
| [#1782](https://github.com/Osmantic/ODS/pull/1782) | Closed; absorb only its regression test | Its proposed implementation regenerated YAML and restarted LiteLLM. PR 3 removes the need for that behavior; port the failing CLI-to-LiteLLM test into PR 7 and do not reopen the implementation. |
| [#1787](https://github.com/Osmantic/ODS/pull/1787) | Existing behavior prerequisite for HipFire adapter work | It has been reduced to 22 files/+1.3k lines. PRs 1 and 3 do not wait for it. Before PR 2C starts, either #1787 is merged and HipFire becomes a required adapter, or PR 2C explicitly excludes HipFire and records #1787 as its blocking dependency. No duplicate HipFire route implementation. |
| [#1896](https://github.com/Osmantic/ODS/pull/1896) | Land independently after its current dependencies are cleaned up | HF acquisition is complementary. PR 8 consumes pull receipts and delete semantics when available, but the switchboard must not depend on its current 55-file branch. |
| [#1751](https://github.com/Osmantic/ODS/pull/1751) / [#1752](https://github.com/Osmantic/ODS/pull/1752) | Landed prerequisites | Brave compose and Lemonade stem-alias fixes remain valid. |

### 4.1 Requirement ownership

| User objective | Owning slices | Release proof |
|---|---|---|
| One stable model name across local runtimes | PR 3, PR 6 | Direct and app requests use `ods/current`; route evidence names the expected concrete model |
| Atomic swap with truthful rollback | PR 1, PR 2A-2C | Failure-injection matrix never commits an unverified route; rollback is itself verified |
| No per-app model rewrites/restarts | PR 4A-4C, PR 5A-5C, PR 7 | Consumer configs retain `ods/current` through swaps and restarts; governed-write guard passes |
| Windows, Linux, macOS, llama.cpp, Lemonade, and HipFire parity | PR 2A-2C, PR 6, PR 7 | Affected-host gates plus the frozen all-host campaign |
| User-visible download, activate, progress, rollback, and delete | Existing acquisition APIs, PR 7, PR 8 | Browser-only lifecycle on every release-required host; active delete is blocked and inactive delete is verified |
| Honest compatibility/agent-viability gates | PR 5A, PR 8 | Product API/UI owns the decision and harness has no model-name exclusion list |
| Every discovered application follows every swap | PR 4A-4C, PR 5A-5C, Harness PR A | Functional round trip plus matching route evidence after each swap and restore |
| New-install, upgrade, restart, repair, and emergency rollback safety | PR 7, PR 9, Harness PR B | One immutable product/harness SHA passes section 7 and produces the release stamp |

## 5. PR series

All product PRs target `main`. #1724 is merged; slices fleet-test from their own pushed branches. If several unmerged slices must be fleet-tested together, create a disposable integration branch for that run and delete it afterward.

### 5.1 Execution preflight

Before each slice:

```powershell
git fetch origin main:refs/remotes/origin/main
git status --short --branch
git worktree add ..\ods-ms-prN -b <branch-name> origin/main
```

The new worktree must start clean. Replace `<branch-name>` with the table value below. If the branch already exists, inspect and reuse its existing worktree rather than deleting or overwriting it.

PR 1's first documentation commit copies this audited plan to `ods/docs/MODEL-SWITCHBOARD.md` and adds it to `ods/docs/README.md`. Every later PR updates the repository copy's status/deviation log; the workstation copy stops being authoritative once PR 1 is open.

Review-size rule: split a slice before review if it changes more than 20 production files or 1,500 net production lines, excluding tests, fixtures, generated lockfiles, and documentation. A larger slice requires a written exception in the PR body explaining why a smaller independently testable boundary is impossible.

Every PR body contains:

- Base and head SHAs
- Dependency PRs and feature-flag mode
- Exact changed contracts and rollback command
- Local/CI commands with results
- Affected host IDs, product SHA, harness SHA, and evidence paths
- Known deferrals with owner; no unowned TODOs

### 5.2 Dependency and branch map

| Slice | Branch | Depends on | Mode after merge | Required exit artifact |
|---|---|---|---|---|
| PR 1 | `feat/model-switchboard-state` | #1711, #1766 | `observe` | v1 state schema/fixtures and read-only API |
| PR 2A | `refactor/model-reconciler-core` | PR 1 | `observe` | reconciler contract + container llama.cpp adapter |
| PR 2B | `refactor/model-adapters-native` | PR 2A | `observe` | Windows and macOS native adapters |
| PR 2C | `refactor/model-adapters-lemonade` | PR 2A and #1787 decision | `observe` | Lemonade adapter and HipFire adapter if #1787 landed |
| PR 3 | `feat/model-router` | PR 2A (2B/2C may proceed in parallel; observe-mode router needs only the reconciler contract and container adapter) | `observe`, canary override | internal router + static LiteLLM config |
| PR 4A | `feat/open-webui-stable-model-route` | PR 3 | enabled only on selected canaries | Open WebUI migrated and probed |
| PR 4B | `feat/perplexica-stable-model-route` | PR 3 | enabled only on selected canaries | Perplexica migrated and probed |
| PR 4C | `feat/opencode-stable-model-route` | PR 3 | enabled only on selected canaries | OpenCode migrated and probed |
| PR 5A | `feat/hermes-talk-stable-model-route` | PR 3 | enabled only on selected canaries | Hermes/ODS Talk text migrated and probed |
| PR 5B | `feat/openclaw-stable-model-route` | PR 3 | enabled only on selected canaries | OpenClaw migrated and probed |
| PR 5C | `fix/local-llm-direct-route-bypasses` | PR 4A-5B | enabled only on selected canaries | zero undeclared local text-model bypasses |
| PR 6 | `feat/lemonade-virtual-route-adapter` | PR 2C, PR 3 | enabled on AMD canaries | v11 route attestation or explicit legacy fallback |
| PR 7 | `refactor/model-mutation-single-path` | PR 4A-6 | enabled on validated hosts | UI/CLI/bootstrap/install all use reconciler |
| PR 8 | `feat/model-switchboard-ui` | PR 7, Harness PR A | enabled on validated hosts | capability/preflight/events/rollback UI |
| PR 9 | `chore/model-switchboard-default-on` | PR 8, Harness PR B, stamp candidate | default-on | migration cleanup, docs, and release stamp |

PRs at the same lettered level may proceed in parallel, with no more than two active sidecars. PR 7 is the convergence point and cannot start deleting legacy mutation paths until all consumer slices and PR 6 have affected-host evidence.

### 5.3 File ownership map

Existing ownership points that implementations extend:

- Host mutation authority: `ods/bin/ods-host-agent.py`
- LiteLLM configuration renderer: `ods/scripts/render-runtime-configs.py`
- Dashboard model API: `ods/extensions/services/dashboard-api/routers/models.py`
- Models UI: `ods/extensions/services/dashboard/src/pages/Models.jsx`
- Extension LLM-consumer contract source: `ods/extensions/schema/service-manifest.v1.json` (with `ods/extensions/library/schema/service-manifest.v1.json` kept in sync by the existing schema/library workflow)
- Fleet model lifecycle: `lib/model-ui-host.sh`, `lib/model-ui-ods-adapter.sh`, and `playwright/model-management.mjs` in the harness repository

New ownership points created by this series:

- PR 1: `ods/config/model-state.schema.v1.json` and `ods/bin/model_switchboard/`
- PR 1: `ods/extensions/services/dashboard-api/model_state.py`
- PR 3: `ods/extensions/services/model-router/`
- PR 3: one static model-router entry emitted by `ods/scripts/render-runtime-configs.py`

Paths in the second list are planned files and do not exist at the audited base SHA. A slice that moves one of these responsibilities must update this map and name the new single owner in its PR body.

### PR 1: `feat(models): add versioned switchboard state`

Purpose: establish one observable source of truth without changing routing.

Primary changes:

- Add `ods/config/model-state.schema.v1.json` and `ods/bin/model_switchboard/{__init__.py,state.py}` with schema validation, atomic storage, bounded history, separate `seq`/`routeSeq`, and migration helpers.
- In `observe` mode, write state from `ods/bin/ods-host-agent.py` only after the existing activation or rollback path has proved success; initialize once at host-agent startup from runtime identity plus `.env` when no v1 state exists.
- Add `ods/extensions/services/dashboard-api/model_state.py`, register static routes before the existing dynamic model-ID routes, and expose read-only `GET /api/models/state`.
- Reuse dashboard-api's existing `/data` mount; do not add a second writable state path.
- Add the repository plan at `ods/docs/MODEL-SWITCHBOARD.md` and link it from `ods/docs/README.md`.
- Keep all current `.env` and config propagation behavior.

Tests:

- Atomic write and interrupted-write recovery on Windows and POSIX.
- Concurrent reader/writer test with no partial JSON reads.
- Monotonic sequence and stale-state rejection.
- Migration from `.env` with Lemonade stem, `extra.*`, GGUF, and native model IDs.
- Cloud-only mode creates no local active route and retains its current static provider configuration.
- Activation failure leaves `active` on the previous verified model.
- Host-agent restart does not overwrite an existing valid v1 state with stale `.env`.
- Fresh install creates valid state before dashboard readiness; malformed-state fixtures return a diagnostic failure without promoting `desired`.

Exit criteria:

- `GET /api/models/state` matches the JSON Schema and runtime identity on all four runtime families.
- No request-routing or consumer config diff is present.
- Feature rollback is removal of the additive state/API files; existing activation remains untouched.

Fleet gate: one Windows Lemonade host, one Linux Lemonade host, one Linux llama.cpp host, and one macOS native host in observe-only mode. Existing behavior must remain green.

### PR 2A-2C: `refactor(models): introduce runtime adapters and one reconciler`

Purpose: move platform/runtime behavior out of the 8k-line host-agent activation method without changing externally visible behavior.

Shared contract:

- Extend the PR 1 sibling package under `ods/bin/model_switchboard/`; installer/package tests must prove the standalone host-agent imports it from the installed tree.
- Define typed `stage`, `verify_identity`, `verify_completion`, `publish_native_alias`, `unload`, `delete`, and `rollback` results. Every successful verification returns concrete identity, context, capabilities, and proof timestamp.
- Make `_do_model_activate` call one reconciler while retaining the existing lifecycle lock, HTTP contract, and snapshot rollback.
- Do not add a cloud adapter. Cloud-only routes remain LiteLLM-owned and hybrid cloud models retain their explicit names.

Slice boundaries:

- PR 2A: reconciler state machine plus container llama.cpp adapter. It establishes the shared fake adapter and transaction-boundary test matrix.
- PR 2B: native Windows and macOS llama.cpp process/bridge adapters, with no Linux changes beyond shared contracts.
- PR 2C: Lemonade version/path/model-ID adapter and, if #1787 is merged, HipFire. The Lemonade native virtual collection remains PR 6.

Each slice moves existing behavior and tests before deleting the old inline block. A source-level guard must prove there is one implementation of each migrated operation.

Compatibility:

- Preserve existing snapshot rollback until PR 7 removes duplicated projections.

Tests:

- Adapter contract suite shared by every implementation.
- Exact command/env assertions for each OS path.
- Identity mismatch, completion failure, process failure, timeout, and rollback proof.
- Negative test: health-only success cannot commit a route.
- Host-agent packaging/start tests on Linux systemd, Windows task/process, and macOS launchd.

Exit criteria:

- Old and new paths produce byte-equivalent compatibility projections and the same externally visible API responses for success and failure fixtures.
- The adapter returns the concrete model carried by the proof request; configured identity alone cannot satisfy verification.
- Reverting any PR 2 slice restores its previous inline runtime path without changing state format.

Fleet gate: affected-host-only activation/rollback runs on every runtime family.

### PR 3: `feat(router): add the ODS stable-alias data plane`

Purpose: stop model swaps from requiring LiteLLM config rewrites and restarts.

Primary changes:

- Add `ods/extensions/services/model-router/` with `app/main.py`, pinned `requirements.txt`, Dockerfile, `compose.yaml`, manifest, health check, metrics, and focused tests. Reuse FastAPI/httpx versions already accepted by dashboard-api unless a documented incompatibility requires otherwise.
- Implement OpenAI-compatible `/v1/chat/completions`, `/v1/completions`, `/v1/responses`, and `/v1/models` forwarding.
- Rewrite only the request model and backend base path; preserve tools, images, stream settings, request cancellation, and safe headers.
- Read the active route from `data/model-state.json`, with sequence-aware caching.
- Implement bounded queue-during-reload and structured 503 behavior.
- Add route evidence and probe correlation without prompt logging.
- Add `ods/config/model-router/endpoints.json`, generated at install from known runtime topology and mounted read-only. State selects only an `endpointId` from this file.
- Include model-router in local, Lemonade, and hybrid Compose stacks; exclude it from cloud-only routing. It has no host port.
- Change LiteLLM local/Lemonade templates to map `ods/current`, `default`, and the compatibility wildcard to model-router permanently when enabled.
- Add `ODS_MODEL_SWITCHBOARD=legacy|observe|enabled`; ship `observe` by default in this PR. `legacy` renders the pre-switchboard LiteLLM config, `observe` runs router/state checks without consumer traffic, and `enabled` sends the stable aliases through model-router.

Required LiteLLM shape when enabled:

```yaml
model_list:
  - model_name: ods/current
    litellm_params:
      model: openai/ods/current
      api_base: http://model-router:9099/v1
      api_key: no-key
  - model_name: default
    litellm_params:
      model: openai/ods/current
      api_base: http://model-router:9099/v1
      api_key: no-key
  - model_name: "*"
    litellm_params:
      model: openai/ods/current
      api_base: http://model-router:9099/v1
      api_key: no-key
```

The renderer owns this YAML. No installer, CLI, or host-agent heredoc may maintain a second enabled-mode copy.

Tests:

- Byte/semantic parity for non-streaming and SSE streaming responses.
- Consumer-visible response/chunk `model` stays on the requested public alias while authenticated route evidence records the raw concrete upstream identity.
- Tool-call, reasoning-content, image, malformed request, large body, cancellation, disconnect, timeout, and upstream error cases.
- `/v1/models` advertises stable aliases and reports concrete identity only as ODS metadata.
- Router restart and stale-state recovery.
- No model-router or LiteLLM restart across repeated route flips.
- Negative self-tests for stale sequence, malformed state, wrong concrete ID, and false route evidence.
- Hybrid-mode precedence: named cloud models resolve to their static provider routes and never fall through the `"*"` wildcard into model-router.
- Steady-state footprint: model-router RSS stays within a documented budget (target: under 150 MB) at the configured connection/queue limits on an 8 GB tier-1 host profile, measured and recorded rather than assumed.
- Local p95 added first-byte overhead target: below 10 ms on the same host, with measurements recorded rather than hidden by retries.

Exit criteria:

- Twenty alternating route flips complete without restarting LiteLLM or model-router and without one request reaching the wrong `routeSeq`.
- A direct request for an arbitrary URL/model cannot turn model-router into an SSRF or forward proxy.
- `legacy` and `observe` are behaviorally identical to pre-PR routing; `enabled` is exercised only on named canaries.
- Cloud-only mode's resolved Compose/LiteLLM configuration is byte-equivalent except for documented formatting.

Fleet gate: Tower2 runs the local NVIDIA canary and remains the orchestrator. Add one representative host for each other backend family: `strix-halo` (Linux Lemonade), `strixy` (Windows Lemonade), `windows-laptop` (Windows native llama.cpp), and `m5-mbp` (macOS native). Run repeated direct gateway swaps before migrating applications.

### PR 4A-4C: migrate Open WebUI, Perplexica, and OpenCode

Purpose: migrate the three consumers most likely to cache or persist model IDs.

Shared rule:

- Render each consumer once with LiteLLM URL plus `ods/current`.
- Keep the merged #1766 contract: `llm.route: gateway` and `llm.pinning: none` mean the app stores no concrete model ID. Update capability floors and probe descriptors without inventing a new pinning enum.
- Keep install-time rendering and a one-shot upgrade repair from concrete IDs.
- Remove a consumer's per-swap rewrite only in that consumer's PR, after its focused live canary passes on the same branch. Re-run after removal before merge.

Slice-specific changes:

- PR 4A, Open WebUI: update its Compose/provider bootstrap to LiteLLM + `ods/current`; add deterministic fleet-admin provisioning and authenticated chat proof. No login/setup state may be hand-created during a release run.
- PR 4B, Perplexica: make `render-runtime-configs.py` and `settings.seed.json` install/repair-only, migrate live settings once, then remove `_update_perplexica_model` from the swap path.
- PR 4C, OpenCode: render both supported config filenames with `ods/current`, migrate once, then remove `_update_opencode_config` from the swap path. A fresh session is created for each probe.

Tests:

- Fresh config, upgrade from concrete ID, app restart, ODS restart, and host restart retain `ods/current`.
- Each application performs a real user-facing round trip after swap and restore.
- Route evidence proves the app request reached the expected concrete model.
- Negative test: a concrete stale model in app config is detected and repaired once, not accepted as green.

Per-slice exit criteria:

- The app config contains `ods/current` before and after two model swaps, app restart, ODS restart, and host restart.
- No activation code writes that app's model field.
- The app's real functional probe and route evidence pass after swap and restore on every OS where it is enabled.
- Reverting the slice restores only that app's legacy update path.

Fleet gate: browser-driven app probes on every host where each app is installed, first affected-host-only, then one four-host mixed-OS pass.

### PR 5A-5C: migrate Hermes/ODS Talk, OpenClaw, and remaining local text consumers

Purpose: complete the consumer migration and remove the remaining direct backend bypasses.

Slice-specific changes:

- PR 5A, Hermes/ODS Talk text: Hermes uses LiteLLM + `ods/current`. Configure a static 65,536-token agent context only for models that pass the product's 65,536-token gate; do not claim dynamic per-model context support. Remove concrete model patching/restart from swap after proof. ODS Talk text is covered by a fresh Hermes session.
- PR 5B, OpenClaw: replace direct `OLLAMA_URL`/concrete pinning with the gateway alias and remove its swap-time recreation after proof.
- PR 5C, bypass closure: inventory enabled manifests and resolved Compose configs for local LLM endpoint/model variables. Migrate or explicitly declare every remaining local text consumer; fail CI on undeclared direct local text routes.
- Vision remains an explicit existing route in this series and receives no-regression tests. A future `ods/vision` role is a separate design after `ods/current` is stamped green.
- Keep install/repair logic able to convert old concrete text-model settings once.

Tests:

- Fresh Hermes session per probe; no looser answer matching.
- Tool-call-then-answer is accepted for tool-capable models; non-agent-viable models are blocked by product metadata, not harness exclusions.
- ODS Talk text, OpenClaw, direct LiteLLM, and existing vision no-regression round trips.
- App session started before a swap and a new session after a swap both use the correct route.
- No old model announcement after restore.

Per-slice exit criteria:

- No migrated consumer stores or announces a concrete active text-model ID.
- Fresh sessions after every swap carry `ods/current` and route to the expected concrete model.
- PR 5C's generated inventory count equals the dashboard/harness discovered-consumer count.
- Models below the 65,536-token agent floor are visibly blocked for Hermes/ODS Talk rather than waived by the harness.

Fleet gate: all discovered consumers on each affected host, with route evidence for every swap and restore.

### PR 6: `feat(lemonade): add deterministic virtual-model adapter`

Purpose: use Lemonade's native virtual-model primitive where available while preserving the ODS contract everywhere.

Primary changes:

- Start from current `main`'s actual pins (`v10.2.0` container and Windows `10.0.0`) and reconcile any newer #1724-only runtime work explicitly. Do not assume the integration branch's runtime is on main.
- Candidate v11 baseline is Lemonade `v11.0.0`, which contains deterministic `collection.router`. Pin the container by tag plus resolved image digest. Pin `lemonade-server-minimal.msi` to release SHA-256 `771b9df062017b30af1bf2b804afc1f0c80a3499bbe7d60ea51393b73e38f521`.
- Handle v11's cache ownership/path migration: the container runs as UID 10001 and uses `/opt/lemonade/.cache` instead of `/root/.cache`. Upgrade tests must prove existing model data is retained, permissions are repaired deliberately, and a downgrade does not delete it.
- Add a strict runtime feature probe rather than relying only on version strings.
- On v11.0.0, register one immutable `user.ODS-Route-<routeSeq>` collection per verified candidate. Load and verify the concrete candidate, register the collection, prove a request to `ODS-Route-<routeSeq>` routes to it, then permit the ODS route flip. Keep the prior collection through the rollback/drain window and delete it afterward.
- Do not re-pull `user.ODS-Current` on v11.0.0. [Lemonade fix #2703](https://github.com/lemonade-sdk/lemonade/pull/2703), commit `5cf1fd76d47a35cc5d413aa7e59bf7e2a74ba61f`, fixes router-specific same-name re-pull but landed after the v11.0.0 tag. A future release may use the stable internal name only if `git merge-base --is-ancestor 5cf1fd76 <release-tag>` succeeds and the runtime feature probe passes.
- Capture `x_lemonade_route` in ODS route evidence.
- Preserve a legacy v10 adapter that sends the concrete Lemonade model ID through model-router.
- Add registry cleanup for expired, non-active `user.ODS-Route-*` collections and deleted, non-active concrete models. Never delete the active or retained rollback route.

Feature probe:

1. Register a temporary one-candidate `collection.router` with a unique name.
2. Send a real completion through that collection.
3. Require response model identity plus matching `x_lemonade_route.route_to` and `x-lemonade-route` header.
4. Delete the temporary collection and verify registry cleanup.
5. If any step fails, report `nativeRouter=false` and use the concrete-ID adapter; do not partially enable native routing.

Explicitly out of scope:

- Classifier routing
- LLM-as-router policies
- Automatic smart model choice
- Multiple candidates in the ODS-Current collection

Those depend on unresolved or still-reviewing upstream work in Lemonade #2727, #2729, and #2748 and are not needed for reliable manual swapping.

Tests:

- Lemonade upstream endpoint tests mirrored as ODS integration contracts.
- Register and route six immutable per-`routeSeq` collections across six sequential models.
- Server restart/cache rebuild preserves the virtual model.
- Streaming, completions, responses, and tool calls preserve route evidence.
- Missing candidate, filtered candidate, failed re-pull, registry corruption, and rollback.
- Legacy v10 fallback remains functional and cannot be mistaken for native-router attestation.

Exit criteria:

- The runtime package/image and checksum/digest are present in generated install metadata and run attestation.
- Six swaps leave exactly one active collection plus the bounded rollback collection; no unbounded registry growth.
- A failed new collection proof leaves the previous ODS and Lemonade routes active.
- Windows and Linux AMD produce equivalent route evidence despite different installation mechanisms.

Fleet gate: Strixy/Windows AMD first, then Strix-Halo/Linux AMD. Six sequential models, restart between repetition cycles, and registry inventory before/after deletion.

### PR 7: `refactor(models): make every mutation use the reconciler`

Purpose: remove the duplicated swap implementations and competing state writers.

Primary changes:

- Bash `ods model swap`, Windows `ods.ps1 model swap`, and macOS CLI routing call the authenticated host-agent activation contract and wait for its terminal operation result. They fail clearly if the agent is unavailable; they do not fall back to local config mutation.
- `bootstrap-upgrade.sh` owns download and checksum verification only, then calls the running host agent for activation.
- Fresh installers may create the bootstrap runtime/env before the agent exists, but installation is not complete until the host agent starts, reconstructs/proves initial state, and returns a matching `GET /v1/model/state`. Full-model promotion uses the normal activation endpoint.
- Remove per-swap LiteLLM regeneration/restart, Hermes patching, Perplexica sync, OpenCode rewrite, and OpenClaw recreation code already made obsolete by PRs 3-5.
- Keep `.env` and `models.ini` projections for external compatibility for one release, generated from state after route commit.
- Port [#1782](https://github.com/Osmantic/ODS/pull/1782)'s regression test. That PR closed without landing; remove equivalent restart behavior only if it entered `main` through another PR.

Ownership rule:

- Only the running host-agent process writes `model-state.json`, active-model `.env` keys, or `models.ini` after initial installer bootstrap.
- Dashboard-api, CLIs, bootstrap-upgrade, and repair tools are clients.
- The compatibility projector has one module/function and a declared governed-key list. CI rejects writes to those keys elsewhere.

Tests:

- CLI, UI, bootstrap promotion, installer, API, and rollback all produce the same state/event sequence.
- Failure at every transaction boundary leaves one coherent active route.
- Restart during each phase recovers or rolls back deterministically.
- No code path writes a consumer's concrete model ID during a swap.
- Static analysis contract rejects new writes to governed model keys outside the state projector.

Exit criteria:

- UI, Bash CLI, PowerShell CLI, bootstrap promotion, rollback, and repair all generate the same operation/event/state sequence.
- Killing any client after request submission cannot leave an untracked background mutation; the host agent operation remains queryable.
- Host-agent unavailability produces a terminal user-facing error without changing files.
- The #1782 regression reproducer passes without a LiteLLM restart.

Fleet gate: clean install plus upgrade on Windows, macOS, Linux NVIDIA, Linux AMD, and any active HipFire host.

### PR 8: `feat(models): capability gates, live progress, and verified rollback UI`

Purpose: expose the new architecture honestly to users.

Primary changes:

- Dashboard API serves one capability contract and the 65,536-token agent context floor. Product API, UI, model selection, and harness consume this value; no separate approximate context-floor constants remain.
- Models UI shows public alias, concrete routed model, backend, context, capability compatibility, operation phase, and last proof time.
- Implement `GET /api/models/events` with a bounded event buffer, SSE `id: <seq>`, `Last-Event-ID`, heartbeat, disconnect cleanup, and full-state resync when history is unavailable.
- Replace ambiguous polling with sequence-aware SSE while retaining one bounded polling fallback. UI reducers reject older `seq`; displayed route identity changes only on a newer `routeSeq`.
- Add preflight impact dialog and visible per-app compatibility gates.
- Add one-click verified rollback.
- Enforce active-model delete blocking from state; integrate HF pull receipts from #1896 when present.
- Surface agent viability as catalog/product metadata. No harness-only model exclusion lists.

Tests:

- Frontend reducer rejects older sequences.
- Accessibility and responsive action controls.
- Capability floor and app-impact contract tests.
- User-comprehensible load, queue timeout, rollback, and delete-active errors.
- Browser tests use only visible controls for final approval.

Exit criteria:

- Disconnect/reconnect, delayed poll, and out-of-order event fixtures cannot flip the UI back to an older model.
- Preflight names every incompatible enabled consumer and the reason before the Run action is accepted.
- Rollback is a normal reconciler operation with route proof, not a file restore shortcut.
- The harness reads the capability floor and viability metadata from product APIs only.

Fleet gate: UI-only download, run, use, rollback, and delete on all release-required hosts.

### PR 9: `chore(models): default-on migration, cleanup, and documentation`

Purpose: promote the switchboard and remove obsolete compatibility code only after stamped evidence exists.

Primary changes:

- Default `ODS_MODEL_SWITCHBOARD=enabled` for fresh installs.
- Upgrade migration preserves the current proven model and generates state before moving consumers.
- Retain `ODS_MODEL_SWITCHBOARD=legacy` as the explicit emergency rollback mode for one release.
- Remove dead concrete consumer renderers and restart paths.
- Add user guide, extension-author guide, runtime-adapter guide, route-evidence/privacy guide, and operator recovery runbook.
- Document stable alias requirements in the service manifest schema and template.

Tests:

- Fresh install on every supported OS/backend.
- Upgrade from the last non-switchboard release.
- Reinstall/idempotence, restart, update, doctor, repair, uninstall/reinstall.
- Emergency rollback flag restores the legacy path without data loss.

Exit criteria:

- The PR 9 candidate SHA is tested with no feature override, proving fresh installs really default to `enabled`.
- Upgrade and emergency-legacy tests preserve the active concrete model and downloaded inventory.
- The repository plan's decision/status table, user guide, extension guide, and operator runbook all match shipped commands and API paths.

Merge gate: freeze the PR 9 head SHA, run section 7 with default settings, and stamp that exact candidate. Merge only that stamped SHA. After merge, run a main-branch smoke/identity check; any merge commit that changes the tree requires a new affected-host proof.

## 6. Harness PRs

Harness changes live in the private fleet harness repository, are committed and pushed before deployment, and record both product and harness SHAs in every run directory and result file.

### 6.1 Baseline verification commands

Run focused tests while developing, then the complete gates before pushing a PR head. Commands assume a Linux/WSL shell for shell/Compose tests and the repository paths shown.

Product backend/router:

```bash
cd ods
python3 -m pytest \
  extensions/services/dashboard-api/tests/test_model_state.py \
  extensions/services/dashboard-api/tests/test_model_activate.py \
  extensions/services/dashboard-api/tests/test_models.py -q
python3 -m pytest extensions/services/model-router/tests -q
make lint
make test
make smoke
```

Dashboard:

```bash
cd ods/extensions/services/dashboard
npm ci
npm test
npm run lint
npm run build
```

Full product gate before push:

```bash
cd ods
make gate
git diff --check
```

Harness:

```bash
cd "$FLEET_HARNESS_DIR"
bash tests/run.sh
git diff --check
```

Windows-specific state/import tests also run with the installed interpreter (`py -3 -m pytest ...`) and the existing PowerShell contract jobs. A PR may not substitute mocked Linux results for Windows process/task or MSI behavior.

### Harness PR A: route-aware consumer proof

Change:

- Extend `playwright/model-management.mjs` to generate a probe UUID and HMAC for every app round trip, using the run-scoped `ODS_FLEET_PROBE_KEY` provisioned by setup and removed by teardown.
- Query authenticated route evidence after Hermes, Open WebUI, Perplexica, OpenCode, OpenClaw, LiteLLM, and any newly discovered LLM consumer.
- Require requested alias `ods/current`, expected concrete model, expected backend, current `routeSeq`, successful response status, and Lemonade native route evidence where supported.
- Add checks that discovered-consumer count equals functional-probe result count.
- Preserve and assert the existing run naming/metadata contract: product SHA, harness SHA, host set, phase/tier, and clean harness status.

Negative self-tests:

- Correct answer from the wrong model fails.
- Correct model with stale sequence fails.
- Discovery-only evidence cannot satisfy a functional probe.
- Missing app result fails rather than skips.
- Legacy Lemonade evidence cannot claim native router coverage.
- A forged or expired probe ID fails.
- A validator retry cannot convert a final mismatched route into green.
- An unsigned marker, wrong HMAC, or marker observed only in an app response without model-router evidence fails.

### Harness PR B: permanent lifecycle and release gate

Change:

- Add switchboard setup/teardown to `lib/model-ui-host.sh` and `lib/model-ui-ods-adapter.sh`.
- Add in-flight and during-swap cells to the release tier.
- Add six-model campaign planning with host-specific, viable, preferably non-overlapping model sets.
- Extend and validate `targets.json` with the release-scope fields defined in section 7.2; `enabled` never removes a release-required host from stamp accounting.
- Encode eight release cycles per host: six distinct viable models, then repeat the baseline and one non-Phi model after restarts. `MODEL_UI_RELEASE_REQUIRED_MODELS` remains six; repeated models are repetition proof, not extra distinct coverage.
- Make User Green require the full app/route matrix at the stamped product SHA.
- Make confidence regeneration after a failed orchestrator verdict self-disclose.
- Keep the drift ledger and prohibit undisclosed result-file repair.

Harness files likely touched:

- `playwright/model-management.mjs`
- `lib/model-ui-host.sh`
- `lib/model-ui-ods-adapter.sh`
- `run-model-ui-fleet.sh`
- `lib/report.sh`
- `tests/test-model-ui-llm-consumer-coverage.sh`
- `tests/test-model-ui-release-plan.sh`
- `tests/test-release-confidence-report.sh`
- New route-attestation and negative-self-test files

## 7. Release validation program

### 7.1 Iteration gates

For PR 1 through PR 8, including every lettered slice:

1. Unit and contract suites green.
2. Compose/config/build checks green.
3. Affected-host-only fleet run from pushed product and harness SHAs.
4. Actual browser UI actions for any changed user workflow.
5. Actual functional app probes for every discovered consumer on affected hosts.
6. Record result and any hand repair in the coverage/drift ledgers.

A single passing cycle is iteration-green only. It is not release evidence.

### 7.2 Stamp candidate

Freeze one immutable product SHA and one immutable harness SHA. No feature commits after the freeze; a product fix creates a new candidate and invalidates affected-host evidence at the old candidate.

Per release-required host:

1. Fresh install from the public bootstrap pinned to the exact product SHA.
2. Verify model-state creation, stable alias, router health, and all discovered consumers.
3. Through the actual Models UI, download, load, use, restore, and delete six distinct viable models in cycles 1-6.
4. Prefer non-overlapping model sets across hosts to cover more labs, architectures, quants, sizes, and tool behaviors.
5. After every swap and restore, run real functional round trips through every discovered app.
6. Require route attestation for every app result.
7. Restart between every cycle.
8. Repeat the baseline and one non-Phi model in cycles 7-8. These repeats do not count toward the six-distinct-model requirement.
9. Exercise one load failure, verified rollback, delete-active block, interrupted download/resume, and queue timeout path per runtime family.
10. Run the full ordinary fleet test after the model campaign.

Current canonical release-required hosts from `targets.json` at this audit:

- `tower2`: Linux x86_64 NVIDIA llama.cpp and fleet orchestrator
- `strix-halo`: Linux x86_64 AMD Lemonade
- `spark`: Linux aarch64 NVIDIA unified llama.cpp
- `dgx-gpu01`: Linux aarch64 DGX/GB300 llama.cpp, currently blocked: Launchpad closes SSH pre-auth (observed 2026-07-19)
- `m5-mbp`: macOS arm64 native Metal
- `windows-laptop`: Windows x86_64 NVIDIA native llama.cpp
- `strixy`: Windows x86_64 AMD Lemonade, currently blocked for browser phases: no interactive console session (observed 2026-07-19)
- `mac-mini`: macOS, currently blocked because its access/key repair is still outstanding

`enabled: false` is an execution state, not a release-scope waiver. Harness PR B adds optional `release_required` to each `targets.json` host, defaulting to `true`; `release_required: false` requires non-empty `scope_owner`, `scope_reason`, and `scope_decided_at` fields. At freeze time, generate the expected set where `release_required != false`. A disabled or unreachable release-required host remains red/blocked and prevents the stamp. Removing a host requires an explicit owner-approved retirement/scope decision recorded in those fields, the coverage ledger, and the PR 9 release note; do not maintain a second hardcoded release list in the harness.

Unavailable hosts are explicitly blocked with owner and reason; they are never counted as pass. On the audited fleet, three hosts are currently blocked, so restoring `mac-mini` access, `dgx-gpu01` SSH (external Launchpad pre-auth failure), and an interactive `strixy` console session are all release-stamp preconditions rather than optional extra coverage.

Catalog readiness is a fourth precondition: before the stamp campaign is scheduled, the product viability API must list at least six viable models for every release-required host, including the aarch64 lanes (`spark`, `dgx-gpu01`). If it does not, catalog metadata work is on the stamp's critical path and must be finished and tested first.

Release model plan shape:

```json
{
  "hosts": {
    "tower2": ["model-1", "model-2", "model-3", "model-4", "model-5", "model-6", "model-1", "model-2"],
    "strix-halo": ["model-7", "model-8", "model-9", "model-10", "model-11", "model-12", "model-7", "model-8"]
  }
}
```

Harness PR B validates exactly eight entries per expected host, six distinct viable entries in positions 1-6, a baseline repeat in position 7, and a non-Phi repeat in position 8. The real plan contains every expected host and is committed with the harness or stored in immutable run metadata.

Canonical release invocation on Tower2 after the normal identity/lock preflight:

```bash
cd "$FLEET_HARNESS_DIR"
export MODEL_UI_PRODUCT_SHA=<40-character-pushed-product-sha>
export MODEL_UI_MODEL_PLAN_FILE=/absolute/path/to/switchboard-release-model-plan.json
export FLEET_MODEL_UI_TIER=release   # harness tier variable; exact name in the harness repo docs
export FLEET_MODEL_UI_CYCLES=8       # harness cycle variable; exact name in the harness repo docs
./run.sh --phase release \
  --hosts tower2,strix-halo,spark,dgx-gpu01,m5-mbp,mac-mini,windows-laptop,strixy \
  --skip-smoke
```

Before launch, replace the host list with the generated expected set and record it in `run-meta.json`. The run is invalid if the deployed product SHA or clean harness SHA differs from the exported/recorded values.

### 7.3 App acceptance

For every installed/enabled LLM consumer:

- The manifest declares LLM consumption and a functional probe.
- The app is exercised through its real user-facing API/UI, not service discovery alone.
- Its request addresses `ods/current` or an approved switchboard role.
- The answer contains the strict fresh-session probe result or valid tool-call-then-answer sequence.
- Route evidence names the expected concrete model and current sequence.
- Restarting the app or ODS does not restore a stale concrete ID.

Known named consumers are Hermes/ODS Talk, Open WebUI, Perplexica, OpenCode, OpenClaw, and LiteLLM. The gate is discovery-driven so a newly installed consumer cannot be silently omitted.

### 7.4 New-install and upgrade safety

The release is not green until all of these pass:

- Clean install with no prior `model-state.json`
- Upgrade from pre-switchboard `.env` and concrete consumer configs
- Reinstall/idempotent install on an enabled switchboard
- Host reboot and ODS restart
- Runtime upgrade, including Lemonade legacy-to-router-capable migration
- Rollback to the previous ODS release without deleting downloaded models
- Uninstall/reinstall with no stale router container, registry alias, task, process, or port

## 8. Rollout and rollback

Rollout states:

1. `legacy`: pre-switchboard routing and compatibility projection; emergency fallback only after PR 3.
2. `observe`: write and verify state while the old route remains authoritative.
3. `enabled`: LiteLLM and migrated consumers use model-router.
4. `default-on`: fresh installs select `enabled` after a stamped fleet run.
5. `legacy-removed`: old per-consumer mutation code is deleted after one release with no rollback use.

Emergency rollback:

- Set `ODS_MODEL_SWITCHBOARD=legacy`, run the documented renderer, and recreate LiteLLM; the command and expected route proof are in the operator runbook.
- The current active concrete model is projected from state before rollback.
- Downloaded models and HF receipts are never deleted by route rollback.
- Retained `user.ODS-Route-*` rollback collections may remain registered but are not used when legacy mode is active. A future stable internal collection may remain only when its release passed PR 6's ancestry and feature probes.

## 9. Merge discipline

- Main-targeted, review-sized PRs only.
- #1724 is merged; there is no standing integration branch. A combined fleet test uses a disposable integration branch that is deleted after the run.
- Product PR equals local branch at every pause.
- Harness files are never deployed from an uncommitted state.
- Every fleet run records product SHA, harness SHA, host, runtime version, and feature mode.
- No validator leniency without a one-line false-red reproduction and a negative-self-test audit.
- Use affected-host-only reruns until a fully proven stamp candidate exists.
- At most two parallel sidecars; use them for independent docs/tests or separate host families.
- Do not add smart routing until deterministic manual switching is stamped green.

## 10. Definition of done

The Model Switchboard series is complete only when:

- Every release-required fleet host passes the six-model browser campaign at one frozen product/harness SHA pair; disabled/unreachable hosts block the stamp unless explicitly retired from scope.
- Every discovered live application passes after every swap and restore with concrete route attestation.
- New installs, upgrades, restarts, reinstall, rollback, and deletion lifecycle are green.
- The release confidence gate includes the full model/app matrix and contains no undisclosed hand-repaired input.
- All implementation is in CI-green main-targeted PRs, with the switchboard default-on PR merged only after the stamp.
- The permanent fleet harness runs download, swap, use-through-all-apps, restore, delete, failure rollback, and during-swap behavior for future releases.
