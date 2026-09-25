# ODS runtime compatibility repairs

ODS retains the pinned Pixel release and records its own runtime adaptations
separately. These adaptations do not change the upstream release identity.

## OpenClaw 2026.6.33 tool discovery recovery

Two unsuccessful `tool_call` requests could poison later valid requests. The
runtime parsed `Unknown tool id: tool_describe` as a missing tool named `id`,
then checked the prior failure streak without comparing the next target.

The reviewed `openclaw-tool-recovery.json` transformation fixes both behaviors:
it preserves the actual missing ID, including namespace separators, and applies
the missing-tool veto only when that same target is requested again. General
no-progress, polling, and other loop detectors retain their existing limits.

The installer applies this repair after upstream release verification, before
the final gateway restart. It requires the exact original or patched module
SHA-256. A differing 2026.6.33 module is left untouched and reported as an error,
unless another ODS build recorded those exact bytes (see
[Runtime patches from another ODS build](#runtime-patches-from-another-ods-build));
other runtime versions are left untouched as not applicable. Qualification of
other versions remains separate.

Original bytes and a receipt live in the Pixel owner's private directory:
`.openclaw/ods-runtime-patches/tool-recovery`. The helper's `--restore` option
restores the reviewed original and refuses to overwrite unrelated later edits.
Runtime changes take effect on the next gateway restart. A regular ODS reinstall
reapplies the reviewed compatibility repair.

The transformation includes small portions of OpenClaw's MIT-licensed
`src/agents/tool-loop-detection.ts`, distributed in its compiled runtime.
Copyright (c) 2026 OpenClaw Foundation. The complete upstream MIT notice is
retained in [Pixel third-party notices](upstream/THIRD_PARTY_NOTICES.md).

Validation includes execution against the real installed runtime's exported
record/outcome/detect functions, preserving the other detectors, plus separate
backup, idempotence, restore, interrupted-write, and changed-package tests.
These tests do not replace live ODS recovery and fresh-install qualification.

## OpenClaw 2026.6.33 embedded completion recovery

The shared transcript persistence path also fills gaps in embedded agent
transcripts. It incorrectly ran the CLI post-turn compactor on those embedded
results. In live ODS testing, the model completed its task and published a
verified preview, then this redundant compactor timed out and caused the
OpenAI-compatible endpoint to discard the completed response.

The reviewed `openclaw-completion-recovery.json` transformation limits that
CLI lifecycle call to actual CLI runners. Embedded transcript persistence and
the embedded runner's own context management remain intact; this does not
increase context, output or timeout limits. CLI compaction is unchanged.

This repair has the same exact-byte, version, backup and restore requirements
as the discovery repair. Its separate custody directory is
`.openclaw/ods-runtime-patches/completion-recovery`; pass
`--completion-recovery --restore` to the helper to restore its original bytes.
The transformation includes a small portion of OpenClaw's MIT-licensed agent
command runtime, Copyright (c) 2026 OpenClaw Foundation; see the same complete
[upstream MIT notice](upstream/THIRD_PARTY_NOTICES.md).

## OpenClaw 2026.6.33 unchanged-file results

The pinned `write`, `edit` and `apply_patch` tools return `terminate: true`
when a call leaves the file exactly as it was ("No changes made to ..."). The
agent loop ends the turn when every result of a tool batch carries that flag,
so a model whose last call rewrote identical bytes never got a turn to answer.
The gateway logged a turn with no deliverable text
(`non_deliverable_terminal_turn`), and OpenClaw skipped `before_agent_finalize`,
where Pixel checks that a published preview is still current. Fleet round 092
on tower2 (coding-v1) ended this way: the model saved test output to a file
that already held it.

`openclaw-noop-file-change.json` repairs the exact `proxy-Bsfwfsp-.js` bytes.
It changes only the loop's batch test (`shouldTerminateToolBatch`): results of
`write`, `edit` and `apply_patch` no longer end the turn.

- In this release those three tools set the flag only for an unchanged file,
  at four sites. The integration test checks this in the installed package.
- Other terminating results, such as tools delegated to an API client, end the
  batch as before.
- The model receives the same "No changes made" result and answers or carries
  on. OpenClaw's loop detectors and Pixel's block on a repeated no-op edit still
  bound repeated identical calls.

Linux/Windows-WSL applies the repair with `--noop-file-change`; native macOS
composes the same recipe into its protected bundle. Restore uses the existing
backup contract (`--noop-file-change --restore`).

The test starts a real gateway with a deterministic provider. An unchanged
`write`, `edit`, `apply_patch` and a batch of all three each get a second model
call and the answer. The last test runs the round-092 sequence with Pixel's
guard and the ODS ingress. `ODS_NOOP_FILE_CHANGE_RED=1` runs the pinned loop:
the turn ends after the unchanged file, with no answer.

```sh
OPENCLAW_PACKAGE=/path/to/openclaw node --test \
  ods/extensions/services/pixel-agent/tests/runtime_noop_file_change.integration.mjs
```

The replaced function comes from OpenClaw's MIT-licensed agent loop, Copyright
(c) 2026 OpenClaw Foundation; the complete license is retained in the
[upstream MIT notice](upstream/THIRD_PARTY_NOTICES.md).

## OpenClaw 2026.6.33 Tool Search image results

Tool Search wrapped native screenshot content inside a JSON text block. Large
base64 strings then consumed the model's text context before the normal image
adapter could handle them. The `openclaw-image-envelope.json` transformation
keeps supported text and image blocks in their original order, retains text
and annotations without truncation, and preserves errors and the complete
framework-owned `{tool,result}` payload in `details` for ODS receipts.

Only compact tool identity is added to model-visible text. Unsupported or
malformed content retains the existing serializer; it is not silently dropped.
Model image capabilities and context/output budgets are unchanged. Text-only
models still use the runtime's existing image filtering, while image-capable
models can receive the native image blocks.

The installer applies the exact-byte repair after upstream verification. Its
private backup and receipt use `.openclaw/ods-runtime-patches/image-envelope`;
`--image-envelope --restore` restores the reviewed original without replacing
independent changes. Validation executes code extracted from the reviewed
runtime module and checks byte custody, content preservation and error behavior.
Live browser and model qualification is also required.

The transformation includes a small portion of OpenClaw's MIT-licensed Tool
Search runtime, Copyright (c) 2026 OpenClaw Foundation; the complete license is
retained in the [upstream MIT notice](upstream/THIRD_PARTY_NOTICES.md).

## OpenClaw 2026.6.33 compaction default export

The installed compaction wrapper uses `export *`, which omits the implementation
chunk's default export. Its consumer imports that default and calls it after a
successful compaction, producing `TypeError: reconcile is not a function`.
The ODS transformation adds an explicit default re-export. It preserves the
existing implementation's monotonic session count and timestamp handling.

The installer checks both the wrapper and its dependency against reviewed
SHA-256 hashes. A changed dependency, unknown package version or independently
modified wrapper is never patched as though it were the reviewed runtime.
Owner-private backup and custody use
`.openclaw/ods-runtime-patches/compaction-export`; the helper's
`--compaction-export --restore` restores reviewed bytes only.

The installed-package test in `compaction_export.test.mjs` loads the real pinned
chunk in isolated Node VM modules with a synthetic session store. It checks the
missing export before repair, callable default after repair, increasing counts,
replayed counts, timestamp monotonicity and preservation of other session fields.
Set `PIXEL_OPENCLAW_RUNTIME` to the OpenClaw package directory and run Node with
`--experimental-vm-modules --test`. This contract test is separate from native
conversation qualification. It does not resolve or qualify the aggregate
compaction timeout, interrupted-run recovery or assistant-tail continuation.

The wrapper line comes from OpenClaw's MIT-licensed runtime, Copyright (c) 2026
OpenClaw Foundation; the full license remains in the
[upstream MIT notice](upstream/THIRD_PARTY_NOTICES.md). The test does not vendor
the upstream implementation.


## Truncated turn continuation after automatic compaction

For OpenClaw 2026.6.33, `openclaw-compaction-resume.json` binds the exact
`sessions-CZbwb3_c.js` bytes. A same-model response ending in `length` above
the compaction threshold continues the compacted session once instead of
ending an unfinished tool turn. The incomplete trailing assistant is removed
from in-memory compaction input; completed tool results and the durable
transcript remain. The original user prompt is not replayed. The existing
overflow recovery bound also limits this continuation, and unsuccessful or
aborted compaction does not resume. Normal completed responses do not retry.
The installer applies `--compaction-resume`; its owner-private backup and
receipt support `--compaction-resume --restore`. Unknown runtime bytes fail
closed. This does not guarantee arbitrary model-generated projects compile.

The exact transport error `Context overflow: estimated context size exceeds
safe threshold during tool loop.` uses the existing bounded overflow recovery
path too. This preserves completed tool work and resumes only after successful
compaction; unrelated tool failures do not trigger this recovery. The repair
accepts the previous reviewed patch hash and reconstructs the original before
upgrading it. Model and settings defaults retain the calculated output/transport
reserve without a redundant half-window reserve floor. Explicit larger settings
reserves remain supported. Compaction cannot make an oversized irreducible
prompt fit every model.

## Concurrent compaction session locks

The same source-bound `openclaw-compaction-budget.json` repair retains the
active attempt's cancellation handle through automatic compaction. The native
`agent_end` event can precede overflow recovery and continuation in the same
attempt; clearing the handle there made later progress-limit aborts return
false while the model continued. Cleanup now occurs in the existing outer
attempt `finally`, whose handle identity check protects a newer run. No
stop-reason heuristic, broad cancellation, registry replacement or force-clear
is added. Normal completion and prompt exceptions still release their handle.

The previous reviewed module hash migrates through the original source hash,
using the existing Linux/WSL repair receipt and macOS staged-bundle composition.
No already-running runtime is modified by the installer-free regression:

```sh
OPENCLAW_PACKAGE_DIR=/path/to/openclaw node --test \
  ods/extensions/services/pixel-agent/tests/runtime_compaction_abort.integration.mjs
```

This checks the real SDK registry and continuation boundary using test-owned
handles and a deterministic compactor, including the original failure,
repeated retries, abort acknowledgement, normal/error cleanup, and stale
handle/session isolation. It does not invoke a model or qualify live fleet
cancellation by itself.

`openclaw-compaction-budget.json` also repairs OpenClaw 2026.6.33's
`selection-BEwSQKM-.js` session lock controller. Split-turn compaction starts
history and turn-prefix summaries concurrently. Both prompt wrappers release
and reacquire the same session lock. Previously, both could observe an absent
lock and attempt independent non-reentrant acquisitions: one succeeded while
the other timed out waiting on its own session.

Concurrent reacquisitions now await one in-flight acquisition, including its
transcript fence verification. Acquisition and fence failures reach every
waiter; a rejected fence releases the acquired lock. The next cycle can try
again. Physical locking, external-owner exclusion, takeover detection and
configured timeouts remain unchanged. The prior reviewed budget repair can
migrate through the original hash, with the existing backup/restore contract.

This shared repair is consumed by Linux/Windows-WSL host installation and by
the macOS native runtime bundle. It contains no inference-backend or GPU
selection changes. JavaScript concurrency tests run on Linux, macOS and Windows
CI; that is not a qualification of every GPU, driver or Windows installation.

To test the actual pinned runtime's controller and physical file locks without
starting an agent or touching an owner's conversations:

```sh
OPENCLAW_PACKAGE_DIR=/path/to/openclaw node --test \
  ods/extensions/services/pixel-agent/tests/runtime_compaction_lock.integration.mjs
```

Use original or shared-repaired package bytes, before macOS-specific bundle
composition. The test checks exact hashes, reproduces the old failure, and
verifies concurrent streams, resumed writes, external exclusion, transcript
takeover rejection and independent sessions using temporary files.

## File reads beyond end of file

OpenClaw 2026.6.33's adaptive read wrapper converted the base reader's exact
`Offset ... is beyond end of file (... lines total)` error into an empty
successful result. This hides the real file boundary and can make an agent
keep requesting larger offsets instead of correcting its range.

`openclaw-read-range.json` repairs the exact `openclaw-tools-iHHy99PD.js` bytes.
An out-of-range read now retains the original offset and line count, reports
`isError: true` with `READ_OFFSET_BEYOND_EOF`, and explains 1-based line ranges.
It does not clamp the request or invent file contents. If a file shrinks during
adaptive paging, already-read text is retained with the error, not certified
as a complete successful read. Normal text, images, empty files, unrelated
errors and optional daily-memory reads keep their existing behavior.

Linux/Windows-WSL applies the source-bound repair with `--read-range`; native
macOS composes the same recipe into its protected bundle. Restore uses the
existing reviewed-byte backup contract (`--read-range --restore`). Unknown
versions or modified runtime bytes are not patched as the qualified release.

The actual pinned reader can be exercised without inference or owner data:

```sh
OPENCLAW_PACKAGE_DIR=/path/to/openclaw node --test \
  ods/extensions/services/pixel-agent/tests/runtime_read_range.integration.mjs
```

The cross-platform CI matrix runs these real-reader tests on Linux, macOS and
Windows. This verifies file-tool behavior, not GPU throughput or the correctness
of arbitrary code generated by a model.

## Tool results sent to the model

Before every model request of an attempt, OpenClaw 2026.6.33 projects the tool
results in the prompt: each result is capped at the agent's
`toolResultMaxChars`, and all results together at four times that value (64k
characters with ODS's 16k cap). The pinned projection freezes every result it
has already sent. Once the frozen results fill the aggregate budget, the only
results it can still reduce are the new ones, so each new result is sent empty.
The transport then renders a single-block empty result as
`(see attached image)`. A new attempt re-trims the oldest results instead, which
changes already-sent history at every owner turn.

Fleet round 057 on tower2 (session `88ecde62`) replayed token-exactly with the
pinned code: the context held 76k characters of tool output when the coding
follow-up started. The model's first read returned nothing, it answered "The
file is empty" and looped on `ls` with different exec hosts until the failure
guard stopped the run. It never saw the exec-host errors or the loop-guard
feedback either. Across five recorded tower2 sessions of 2026-09-25, 242 of
498 new results reached the model empty.

`openclaw-tool-result-projection.json` repairs the exact
`tool-result-truncation-CbxVHy2D.js` bytes. It replaces only the live
projection (`truncateOversizedToolResultsInMessages`):

- Every new result, and every result of a parallel batch, is sent whole within
  the per-result cap. The cap itself is unchanged.
- When all results exceed the aggregate budget, the oldest earlier results are
  reduced to the runtime's own truncation notice, down to half the budget in
  one step. Results of 320 characters or less, such as errors and write
  confirmations, stay whole. Images and tool identity are kept.
- The choice depends only on transcript order. Calls between reductions and the
  next attempt re-send identical history bytes, so the prompt prefix changes
  about once per half-budget of new output instead of at every owner turn.

The session transcript, the pre-prompt compaction check and the recovery
rewrite path are unchanged. `toolResultMaxChars` stays at 16k: doubling it on
131k-context hosts would push the pre-prompt estimate, which counts tool text
at 2 characters per token, over the compaction budget at owner turns that
currently fit.

Linux/Windows-WSL applies the repair with `--tool-result-projection`; native
macOS composes the same recipe into its protected bundle. Restore uses the
existing backup contract (`--tool-result-projection --restore`).

The pinned module can be compared with the repaired one without inference or
owner data. The second test starts a real gateway with a deterministic provider
and shows each exec result reaching the next provider request (set
`ODS_TOOL_RESULT_PROJECTION_RED=1` to see the pinned projection starve it):

```sh
OPENCLAW_PACKAGE_DIR=/path/to/openclaw node --test \
  ods/extensions/services/pixel-agent/tests/runtime_tool_result_projection.integration.mjs
OPENCLAW_PACKAGE=/path/to/openclaw node --test \
  ods/extensions/services/pixel-agent/tests/runtime_tool_result_delivery.integration.mjs
```

## Runtime patches from another ODS build

A different ODS build, such as a newer candidate or a downgrade source, can
leave the same pinned runtime with repair recipes this version does not know.
Every repair records its module, reviewed source hash and intended bytes in the
owner-private receipt before changing the runtime, next to a backup of the
source bytes.

For a set this version manages, unknown module bytes are rebuilt only when that
set's receipt records those exact bytes and its backup still hashes to the
reviewed source. The receipt then records `recoveredFrom: verified-backup` and
the replaced hash. Unrecorded changes, a missing or changed backup, or another
module's receipt still fail closed without writing.

Before its own repairs, the installer runs the helper with `--restore-foreign`
and the names of the sets it manages. Every other set under
`.openclaw/ods-runtime-patches` with a receipt must pass the same checks. All
sets are verified before any write. Each module is then atomically restored to
its verified source bytes, and the set's state is moved to the owner-private
`.openclaw/ods-runtime-patches.retired` archive, never deleted. A failed
verification stops the install before any module changes.
