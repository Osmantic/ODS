# Pixel enhancement priorities for ODS

This is a historical adoption proposal, not evidence that its capabilities
are installed or qualified in the selected ODS revision. Private upstream issue
references below are retained as plain identifiers for provenance; each proposal
is described here so a public reader does not need access to that tracker.
Use [public qualification criteria](PUBLIC-BETA-QUALIFICATION.md) and
commit-bound release receipts to assess current behavior.

## Recommended order

| Priority | Pixel proposal | Value for ODS | First useful increment and acceptance evidence |
| --- | --- | --- | --- |
| First | #201: artifact-backed context (private upstream reference) | Reduce repeated large outputs in the prompt while retaining the complete evidence needed to finish and recover work. Repeated large source reads can exhaust the context needed for repair and completion. | Store complete tool output once; return a readable excerpt and a session-scoped retrieval handle with bounded selectors. Complete a real multi-step repair across context compaction and restart, retrieving the exact retained source and preserving the original task and failed checks. Measure context use, latency and peak memory on the laptop and larger hosts. |
| First, alongside context | #203: exact model provenance and capability-aware routing (private upstream reference) | Distinguish the configured label from the provider, model, backend and settings that actually served each request. This is necessary to diagnose slow runs and memory/output limits accurately. | Record the resolved route and observed reasoning/context/output settings per call, then use measured hardware fit for defaults. Exercise an owner-selected model change and an explicit fallback; show the actual serving identity and preserve the memory envelope and owner overrides. Keep ordinary model choice open. |
| Next | #199: stale-safe edits (private upstream reference) | Make focused edits dependable when a formatter, another agent or the owner changes a file after Pixel reads it. This is preventive protection; the inbox concurrency experiment does not prove this particular defect. | Add a read digest and an atomic compare-and-apply operation using Pixel's own edit format. Demonstrate an intervening edit, zero mutation on rejection, fresh reread and successful retry. Cover Unicode, LF/CRLF, repeated text and cancellation. A separate hash check followed by an unlocked write is insufficient. |
| Next, where supported | #200: fresh post-edit diagnostics (private upstream reference) | Give prompt feedback on syntax, types and unresolved imports without asking the model to rediscover them through repeated reads. | Bind diagnostics to the edited file version and return a concise delta. Demonstrate a broken edit, fresh diagnostics, correction and project tests. A missing or stale language server must not be reported as a clean result. Keep debugging/execution permissions distinct from diagnostics. |
| Incremental foundation | #198: engine-neutral executor contract (private upstream reference) | Reduce coupling to one engine's tool envelopes, progress events, cancellation and recovery behavior. Engine identity is distinct from inference-model identity. | Specify and test the lifecycle seam around an existing working route. Verify actual work, terminal delivery, cancellation and resume through it. Artifact retrieval, edit integrity and current-route fixes can proceed independently; a multi-engine rewrite is not a prerequisite. |
| After lifecycle and edit integrity | #202: isolated subagent patch capsules (private upstream reference) | Let Pixel use parallel workers without shared mutable checkouts or unreviewed integration. | Start with one child, an isolated workspace, explicit resource ownership and a patch returned to the parent. Verify independent work, stale-patch rejection, parent cancellation, descendant cleanup and reviewed integration. Do not infer child authority from model prose. |
| Optional measured canary | #204: advisory shadow reviewer (private upstream reference) | May catch repeated low-value actions or wrong hypotheses before a long run expires. Repeated generator rewrites are an evaluation case, not proof a reviewer would fix them. | Use a separate, bounded context at sparse checkpoints. Compare matched tasks with and without review: unique useful findings, completion quality, latency, tokens and primary-model disruption. Advice remains visible and cannot grant permissions or control execution. It must remain optional on constrained hardware. |

## Existing repairs to qualify before adding replacements

- #209 (private upstream reference) describes duplicate
  embedded/CLI compaction and a lost response. ODS already contains a scoped,
  exact-version completion-recovery adaptation documented in
  [runtime repairs](ODS-RUNTIME-REPAIRS.md). Verify its installed coverage and
  truthful continuation. Context pressure alone does not establish a
  duplicate-compaction race or a lost request.
- #140 (private upstream reference) documents an `off`
  reasoning value becoming truthy. Verify the selected ODS revision's defensive configuration.
  Measure actual requests and visible output; a slow laptop run alone does
  not prove thinking was accidentally enabled. Do not increase limits to
  conceal a protocol, tool-call or generated-code defect.
- #139 (private upstream reference) and
  #141 (private upstream reference) concern execution surviving
  cancellation. Existing ODS cancellation mechanisms need descendant and
  restart qualification on each supported surface before expanding child jobs.
- #223 (private upstream reference) is useful for a genuinely
  read-only audit mode: retain capable inspection while enforcing the selected
  write restriction mechanically. It must not silently narrow the owner's
  ordinary or Full Access modes.

## What these proposals do not fix automatically

The generated inbox application's temporary-file collisions and ledger races
need application-level locking and atomic writes. Stale-safe source editing
does not repair that application algorithm. Language-server diagnostics do not
prove decimal totals, document layout, preview publication or working controls.
An advisory model cannot replace accurate tool results or deterministic
permission enforcement.

Keep live workflows as acceptance: create and revise an app; import/export
real synthetic files; recover after interrupted work; manage an ODS extension;
inspect an authorized host or LAN peer; and finish with a result the owner can
use. Rotate tasks and wording. Compare each mechanism against the existing
route under the same model and memory settings. Record OS, GPU/backend, model,
effective limits, failures, latency and memory separately; five busy sessions
do not establish five qualified installations.

## Design provenance and original implementation

This review and plan are original. No OMP source code, prompts, patch grammar,
tests or assets were imported. The Pixel proposals already distinguish their
own contracts from the systems that informed them.

Conceptual sources reviewed at OMP revision
`eea5628f13043286e17c4a2ea4fc28b15fda33ca` include
[artifact storage](https://github.com/can1357/oh-my-pi/blob/eea5628f13043286e17c4a2ea4fc28b15fda33ca/docs/blob-artifact-architecture.md),
[compaction](https://github.com/can1357/oh-my-pi/blob/eea5628f13043286e17c4a2ea4fc28b15fda33ca/docs/compaction.md),
and the issue-linked documentation for
[Hashline](https://github.com/can1357/oh-my-pi/blob/eea5628f13043286e17c4a2ea4fc28b15fda33ca/packages/hashline/README.md),
[RPC](https://github.com/can1357/oh-my-pi/blob/eea5628f13043286e17c4a2ea4fc28b15fda33ca/docs/rpc.md),
[tasks](https://github.com/can1357/oh-my-pi/blob/eea5628f13043286e17c4a2ea4fc28b15fda33ca/docs/tools/task.md)
and [advisory review](https://github.com/can1357/oh-my-pi/blob/eea5628f13043286e17c4a2ea4fc28b15fda33ca/docs/advisor-watchdog.md).
The pinned [OMP license](https://github.com/can1357/oh-my-pi/blob/eea5628f13043286e17c4a2ea4fc28b15fda33ca/LICENSE)
is MIT and names Mario Zechner, Can Bölük and Stencil Labs, Inc.

Implement these concepts through original Pixel/ODS interfaces and tests.
Retain precise conceptual-source links where they influenced the design.
Existing bundled third-party materials continue to carry their actual notices;
the [bundled notices](../../vendor/pixel/THIRD_PARTY_NOTICES.md) describe those terms.
Conceptual attribution must not be used to imply incorporation of code or
endorsement by another project.
