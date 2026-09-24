# Portal harness migration

Status: historical design and acceptance requirements, summarized after the
September 2026 main promotion. This page is not a current deployment diary.
Use [the promotion record](PUBLIC_BETA_PROMOTION_2026-09.md) for source identity
and missing acceptance, and [Portal regression acceptance](pixel/PORTAL-REGRESSION-ACCEPTANCE.md)
for the current user-journey requirements.

## Observed starting point

PR #6156, baseline `7fcfc531`, contains the existing catalog, source recipe
compiler, scoped proposal channel and host installation coordinator. Preserve
these components, the cloud mascot and current UI during migration.

Earlier development probes exposed distinct
failures: the model selected an unavailable installation tool, a follow-up used
ordinary sandbox execution instead of the managed extension flow, and system
Python rejected pip with `externally-managed-environment`. These are model,
integration and environment failures respectively. None establishes a successful
ODS installation. A virtual environment alone would address only the last one.

## Ownership

The model chooses investigative steps, writes integration files, interprets
build errors and revises its implementation. ODS owns execution boundaries,
operation identities, process handles, cancellation, persistence and promotion
into managed Extensions. Neither assistant prose nor a saved recipe is a runtime
receipt. An uncertain operation must be observed before another is dispatched.

Catalog requests reuse existing installation recipes. GitHub requests use an
isolated preparation workspace followed by managed promotion. Preparation may
clone, read, edit, build and test within the granted environment. It must not
silently become an unmanaged installation on the host.

## Migration stages and acceptance evidence

1. **Durable continuation.** Project the authenticated request, bound proposal
   and immutable revision into subsequent turns. Keep user intent distinct from
   persisted facts. A missing draft is an explicit recovery condition, never a
   reason to install another copy. Query actual installation state separately
   from proposal acceptance. Verify owner isolation, cancellation, expiry,
   changed upstream HEAD, missing records and retries after publication.
2. **Preparation workspace.** Bind each preparation workspace to its request and
   expose ordinary file/process tools there. Detect the actual execution host,
   sandbox capability, architecture, available resources and network policy.
   Any change to tool access requires this boundary
   to be implemented and independently verified. Preserve normal tools for unrelated user work.
3. **Managed promotion and repair.** Give the agent direct, scoped operations to
   inspect, submit, prepare, advance and observe installations. Infer routing
   identifiers from verified session state where possible. Keep external
   operation identities stable across retries; require a terminal observation
   before replacing a failed recipe. Preserve previous recipe revisions and
   existing data. UI closure must not lose the operation or its result.
4. **Context and skills.** Load relevant ODS integration guidance on demand.
   Keep schemas small and direct. Remove contradictory prompts and obsolete
   phrase-based routing as structured state replaces them. Compaction retains
   operation IDs, current revision, outstanding processes and owner constraints.
5. **Real end-to-end verification.** Exercise both catalog and previously
   uncatalogued repositories, including a library, a web application and a
   project with build dependencies. Include configuration-required, failed
   build/repair, cancellation, reconnect and repeated-request cases. Demonstrate
   actual runtime functionality; importing a Python module proves only import
   readiness, not its inference or application behavior.

## Platform and model contract

Do not change the selected model, context size or GPU backend implicitly.
Support small models through usable tools and informative results, not canned
answers or a promise of identical capability. Existing Linux/WSL and macOS
manager transports must remain supported. Windows-native execution needs its
own supported adapter; a POSIX path is not a Windows implementation. Docker
availability and image architecture are observed prerequisites, not assumptions.

Record actual tested combinations separately. Cross-platform unit tests and CI
are useful but do not prove a local installation on every OS/GPU combination.

## Evidence boundary after promotion

The earlier work log mixed test results and live observations from different
revisions. It recorded unavailable tools, incorrect unmanaged-install choices,
environment restrictions, incomplete follow-up tasks, model/context drift,
build failures, and cases where an apparently successful publication did not
establish a usable application. Those failure classes remain relevant to the
acceptance plan; the individual session IDs, local backup paths, process state
and one-workstation restoration instructions are not public operator guidance.

This summary preserves the design boundary without asserting that those probes
passed on the promoted source. A successful read-only inspection is not install
evidence. A prepared recipe is not a running application. Preserve rejection
causes and independent verification failures through the UI and agent response.
Do not overwrite the owner's project scope or substitute a different repository
merely because a requested operation failed.

For any new acceptance claim, retain an exact product/runtime identity and a
sanitized receipt describing the command or user journey, observed outcome,
limitations, and recovery result. Keep raw transcripts and machine-specific
operational evidence outside public documentation. The public main branch's
history is unchanged by this summary; removing diary text from the current page
does not erase earlier published revisions.
