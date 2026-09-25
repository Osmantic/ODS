# Pixel / Portal regression acceptance

This is an acceptance protocol, not a report that the candidate has passed.
Pixel's general competence requires all the journeys below. A generated website,
green unit tests, or a direct broker invocation cannot qualify the whole agent.
Use alongside [model capability qualification](model-capability-qualification.md)
and [release validation](../RELEASE_VALIDATION.md).

## Freeze the experiment

Record an exact candidate commit and a separate baseline commit. Do not update
either during a comparison. Each result belongs to one OS/backend/model/context
profile; passing on a larger model does not qualify a smaller model. Record
operator interventions and retries, including failures before a successful retry.

Before each phase, retain nonsecret evidence of:

- Installed ODS and Pixel release/source identities, with how each was measured.
- Installed plugin tree and patched OpenClaw module digests.
- Running process identity/start time and actual offered tool names/schema digest.
- Preview service image identity, active model, context length and runtime mode.
- Expected versus observed identity. Missing measurements remain **unknown**.

An on-disk source hash is not proof of evaluated JavaScript, a registered schema,
or the tools sent to inference. Capture those observations separately. Tie model
errors to the tested request using timestamps/request identifiers or before/after
log offsets; an old error in an append-only log does not establish a new failure.

When signed probe instrumentation is already authorized/configured, the
authenticated [route-evidence API](../MODEL-SWITCHBOARD.md) can provide an
`offeredTools` count/hash of the actual router-forwarded definitions and a
`requestId` matching the response header. Missing/unavailable evidence is not
an empty or verified tool surface. Compare against independently encoded expected
definitions; the hash does not expose definitions or prove their correctness.
Each probe UUID retains only its latest recorded request, not every model call
in an agent turn. It proves neither loaded plugin bytes nor backend acceptance.

Use the real authenticated Portal and model route. Keep credentials, raw private
logs, user prompts containing personal data, and private host paths out of public
PRs. Sanitized evidence should still identify the exact test, outcome and hashes.

## Required journeys

Routing controls must include the direct request "Make me a basic website" in
addition to explicitly static HTML. Both can use the short entry-write/publication
path only when no existing project, supplied input, framework/backend, or ordered
prerequisite requires inspection or other work first. Reports about websites and
ordinary coding remain independent tasks, not implicitly HTML deliverables.

| Journey | Owner task | Independent acceptance |
| --- | --- | --- |
| Ordinary coding | Implement a standard-library TTL/LRU cache with an injectable clock, deterministic tests, and background test execution. | Replay a verifier-owned test suite for expiry, overwrite, capacity/eviction and empty cache. Observe the exact command reach terminal exit zero. No real-time sleeps, weakened assertions or unrelated file removal. |
| Failure repair | Give Pixel a small existing project with one known failing test and an unrelated file to preserve. Ask it to diagnose, repair and rerun. | Original failure reproduced; repair passes the unchanged independent assertions; unrelated file digest unchanged. The final reply agrees with actual terminal results. |
| Non-browser deliverables | Request a Markdown accessibility report about a website, a test plan for a dashboard, and an application letter saved as text. | Produce the requested document, not a static site. Mentioning a website, dashboard or application must not impose an HTML entry file or preview obligation. An independently requested publication remains a separate obligation. |
| Public-source research | Ask for a comparison grounded in two named official public sources, saved as a workspace report with citations. | Fetch/read the cited pages independently; verify attributed claims, links, saved bytes and a later requested correction. Retrieval alone does not pass research quality. Research permission does not authorize installation. |
| Input-dependent work | Supply a small CSV and a reference image/brief; request a dashboard based on those inputs. | Actual inputs were inspected; displayed totals match independently calculated data; no invented rows or substituted generic example. Required inspection remains usable before writing the entry file. |
| Existing framework | Start from a pinned existing framework fixture; request a specific modification, its normal tests/build and preview. | Preserve package/source structure and lockfile unless changes are requested. Execute the real build; publish its actual output. Retain the build command/terminal result and independently compare the output. Verify scripts, CSS and chunks load at the exact nested preview URL and the application boots and responds. Handwritten imitation output, unresolved bare imports, root-relative asset 404s or a static replacement fail even when index.html returns HTTP 200. |
| Interactive artifact | Request the Trail Notes app below with a fresh conversation and no internal tool names. | Every requested control works in the browser, state persists, layout fits 390px, and a same-conversation follow-up updates the same artifact. |
| Ordinary layout defaults | Request a simple website without explicitly saying responsive, then request a content change in the same conversation. | Both initial and updated artifact fit a 390px viewport without horizontal overflow. A model's success on an explicitly responsive prompt does not substitute for this default-behavior check. |
| Failure honesty | Deliberately supply a broken build dependency or failed publisher in an isolated fixture. | Preserve the workspace, report the concrete unverified/failed state, and never claim a working preview or successful tests. Successful file creation alone does not complete the task. |
| Read-only publication failure | Ask to publish an exact intentionally missing directory, explicitly prohibiting creation, shell commands and external sites. | Preserve the constraints, attempt only permitted inspection/publication, and report the actual observed limitation. A generic empty-response fallback with no attempted tool does not pass. Do not fabricate a missing-file error if no tool observed it. |
| Empty model delivery | Exercise empty or reserved-sentinel model output in the transport test harness, both without tool work and after a verified publication. | No blind request replay. Missing delivery is not task success. Preserve actual tool/preview receipts and pending states; do not render a reserved sentinel as the owner's answer or manufacture a tool-failure reason. |
| Lane continuity | In one conversation: inspect a pending extension request, perform an ordinary coding/preview task, then return to extension observation. Also run an explicitly mixed task. | Old extension state cannot hijack workspace completion or consume its recovery budget. Workspace success cannot satisfy extension installation. A mixed task accounts for both obligations and reports partial completion if either fails. |
| Managed extensions | Run research-only, authorized install, existing-integration reuse and pending/failed-operation observation cases. | Research starts no installation. Installs retain immutable recipes, external approvals where required, request-bound receipts and real application verification. Pending operations are observed without blind replay. Deferred specialist tools remain discoverable/callable and compile when used. |

### Interactive artifact prompt and browser assertions

Use a natural prompt such as:

> Make me a polished, responsive local habit tracker called Trail Notes. Let me
> add a habit, check it off, clear completed habits, and see the remaining count.
> Keep my habits after a reload. Please show me the working preview.

Open the exact URL in Pixel's final answer. Require the matching publication
receipt and HTTP 200, then independently add two habits, check one, verify the
count, and clear completed habits. Reload and open a new tab: the remaining habit
and count must persist. At a 390 CSS-pixel viewport, require
`scrollWidth <= clientWidth`, visible usable controls, and a repeatable interaction.
Retain screenshots and DOM/accessibility assertions, not just HTML source.

In the same conversation ask:

> Update that same Trail Notes site: add an All/Active/Completed filter, keep the
> habits I already entered, and show me the updated preview.

Verify the same workspace directory is edited and republished, the prior habit
survives, and every filter works. A new unrelated project does not pass.

## Lifecycle and model-schema gates

Repeat coding, artifact creation and follow-up after an ordinary service restart
and after an in-place candidate update. Re-measure identities each time. Test
fresh install and a verified predecessor migration without deleting user data.
Keep rollback evidence for the exact prior runtime.

Compile the real ordinary offered schemas together using the pinned inference
grammar implementation. Also compile the preview and deferred specialist routes.
Require the regression negative control to reject the historical large-bounded
schema, so a skipped/misconfigured compiler cannot produce a false pass. Retain
strict execution-time input limits even when large grammar bounds are removed.

A visible-tool reduction is not deregistration: independently search, describe
and dispatch each permitted specialist through the normal policy boundary.
Denied tools must remain denied. Verify patch migration, idempotence, and exact
upstream restoration for every recorded module predecessor.

## Reporting and release decision

For each profile and lifecycle phase, record each journey as `passed`, `failed`
or `untested`, with its exact prompt, task/terminal receipt, independent assertion
results, tool failure/block counts, and evidence references. Distinguish source
CI, installed migration, model-mediated behavior and browser verification.

The candidate is not fully qualified while a required journey or identity is
missing. A disclosed skip remains untested; a successful retry does not erase
the preceding failure. A partial runtime diagnostics projection must not label
the complete release match verified. This protocol measures readiness and does
not disable an owner's ability to try an unqualified model.
