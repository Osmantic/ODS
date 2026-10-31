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
- While a response is under way, a model with a context of 131k tokens or more
  keeps twice the aggregate (128k characters) in view, reduced to half of that
  in one step. On the laptop (2026-10-30), one 227-call elf-of-doom response
  read all of the 1,929-line `ui.js` about nine times after its earlier reads
  were reduced, while its prompt never went above 55k of 131k tokens.
- A request gets the wider view only when it fits the tool-loop guard below at
  every message of the transcript. The guard measures this projection and
  counts tool text twice against window × 4 × 0.9 characters. The newest batch
  is counted before it is reduced, and dense scripts are counted as OpenClaw's
  own estimate counts them (about one token per character). Otherwise the
  request sends exactly the base projection, so the guard trips at the same
  request as without the wider view. The provider stream's input can shrink
  between its requests (old images are pruned, prompt context is dropped), so
  once an attempt has declined the wider view it keeps the base projection
  until the attempt ends. The next owner turn decides afresh, which can restore
  results sent as notices once and rewrite that much of the cached prefix.
- A transcript that ends with a final reply, as the pre-prompt compaction check
  sees it, is measured at the base aggregate. The first request of the next turn
  can send the wider view when it fits the guard, so its room for the reply
  rests on the loop guard rather than on the pre-prompt check's reserve.
- While a response is under way, the provider stream's reductions take the
  reads it has already superseded first, oldest first, and only then the other
  results oldest first. A read is superseded when a later read of the same
  file shows its text again (the same lines or a wider range), or when a later
  change replaced what it shows: an `edit` of text the read shows, a `write` of
  the file, or an `apply_patch` whose result lists the file (or that moved it
  away). Changes count only when the tool's own result reports them.
- Reads are recognized from the call before each result: `read` with its path,
  offset and limit, or `exec` running, after at most one `cd`, a plain
  `sed -n` range of one file, `cat` or `cat -n` of one file, or `cat -n`,
  `nl -ba` or `grep -n ""` of one file piped to a `sed -n` range. Paths compare
  as the sandbox resolves them, relative to the workspace or under
  `/workspace`. Any other result is never superseded, and changes made through
  `exec` are not seen. A reduction counts only what superseded a result by the
  batch it reduces for, so earlier sends stay byte-stable.
- The tool-loop guard calls the projection without an attempt's state and gets
  exactly the projection above, so it measures, and trips, as it would without
  the superseded-first order. Only calls with the attempt's state use the
  order: the provider stream, and the pre-prompt compaction check at the start
  of an attempt, which then sees what the attempt's first request sends (a
  transcript that ends with a final reply still gets the base projection).
  They use the order only while their own input fits the guard's limit at
  every message, in the view the response starts with (the wider view at 131k
  tokens or more, the base view below). The stream's input can count more than
  the guard's: OpenClaw sends a compaction summary to the model as user text,
  and the guard counts it as 256 characters. Once the order does not fit, the
  attempt sends exactly the projection above until it ends. That changes the
  cached prompt prefix once; with the wider view, a later decline of that view
  can change it once more.
- Edit arguments are read as the edit tool reads them: `edits` can arrive as a
  JSON string, which the tool parses in the attempt's copy while the session
  file keeps the string, and a legacy `oldText` joins them. So the next turn
  reaches the same reductions as the attempt that made the edit.
- The first request of the next turn can send the order when it fits, so, as
  with the wider view, its room for the reply rests on the loop guard rather
  than on the pre-prompt check's reserve.

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

## Compaction during long runs

Inside one long owner turn, OpenClaw 2026.6.33 compacts when its tool-loop
guard (`installToolResultContextGuard` in `selection-BEwSQKM-.js`) throws
`Context overflow: estimated context size exceeds safe threshold during tool
loop.` The guard counts characters against 3.6 per window token, with tool
results weighted at 2 characters per token. It measured the raw in-memory
results, but the provider stream sends them through the projection above: 16k
per result, 64k in total, older results reduced to a notice. On the RTX Spark
(2026-10-16, 131k context) a fresh session compacted four times in 15 minutes
while the model's real prompt was 35-46k tokens. Each compaction dropped the
files the agent had just read, and it made no edits in 20 minutes.

The compaction summarizer then read the stored transcript. The pinned
`serializeConversation` caps each tool result at 2,000 characters, but it keeps
every result, every reasoning block and every edit or write payload whole, and
it sends the whole stretch in one request. In a 31-minute game build on the
same host, 299 entries (769k JSON characters, about 190k tokens) followed the
first overflow compaction. The next one asked the 131k model to summarize all
of them in one request. The compaction ended incomplete (`willRetry=false`)
and Portal showed that it could not complete the response. The Spark's log
records only a model response (status 200 after 32 ms) and the incomplete end
68 seconds later, with no LiteLLM or llama.cpp error. In the Spark-shaped
regression fixture the same request is 156,902 tokens by the transport's
estimate, so the transport cuts `max_tokens` to 1 and the server rejects it.

Two hash-chained recipes fix both:

- `openclaw-compaction-budget.json` passes the provider stream's projection
  (`persistedToolResultModelProjection`, then
  `truncateOversizedToolResultsInMessages` with the same caps) to the loop
  guard. The guard measures the tool results the request will carry. Its
  threshold, its returned messages and the stream itself are unchanged.
- `openclaw-compaction-empty.json` bounds every summary request in
  `proxy-Bsfwfsp-.js` (history and split-turn prefix, automatic, overflow and
  manual `/compact`). Its input is held to an ordinary turn at the compaction
  threshold: the transport's own estimate (`estimateStringChars`, 4
  characters per token, 1.25 margin) plus `reserveTokens` fits the window.
  The reserve covers the summary output and leaves tokenizer headroom. A
  conversation within the budget is sent byte-for-byte as before. Above it,
  like the live projection, the oldest long tool results, tool-call argument
  values and reasoning become a short notice first; requests, replies, tool
  names and short values such as paths stay. Only text that cannot be reduced
  loses its oldest messages, with a count marker. When the previous summary
  and the instructions leave no room for even the newest reduced message, the
  compaction fails with `summarization_failed` before any request. This can
  happen on an 8K or 16K model whose previous summary ends with a long
  verbatim owner request. Without the check it would save a summary that
  silently drops all the work since the last checkpoint.

The session transcript, `reserveTokens`, `keepRecentTokens` and the one or two
summary calls per compaction are unchanged. The read/modified file lists still
come from every summarized message. The 1800-second run deadline is unchanged.
Both recipes migrate from the previous reviewed hashes through the original
source with the existing Linux/WSL receipts (`--compaction-budget`,
`--compaction-empty`, each with `--restore`); the macOS staged bundle composes
them as before.

The regression runs the real runner and compaction wrapper with a loopback
provider that rejects prompts larger than its context, as llama.cpp does:

- Twelve reads of a 30k-character file do not compact.
- Large writes, whose tool-call arguments the projection keeps whole, still
  trip the guard. The last prompt before it fires is above 85% of the window,
  the provider rejects nothing, and the run continues after the compaction.
- A 300-entry Spark-shaped tail compacts directly (overflow and manual).
- The same tail compacts twice inside one run, after two provider overflows.
  Each time it uses the session's own overflow auto-compaction, the path that
  failed on the Spark. The run retries and answers.
- At 8K and 16K, a previous summary with no room for new work fails as
  described above.

`ODS_LONG_RUN_COMPACTION_BASELINE=1` loads the previous recipes, and the same
assertions then fail:

```sh
OPENCLAW_PACKAGE_DIR=/path/to/openclaw node --test \
  ods/extensions/services/pixel-agent/tests/runtime_long_run_compaction.integration.mjs
```

## Preview screenshots stay in their run

`pixel_ods_workspace_preview_inspect` can return one screenshot of the agent's
own preview to a model ODS declares image-capable
(`host/preview-inspection-protocol.md`, Viewport screenshot). The tool result,
image included, stays in the session transcript, because ODS sends the
persisted copy of a result to the next request of the attempt. The pinned
runtime then resends tool-result images from the last three completed turns to
whatever model serves the next turn. It strips them only for a row whose
`input` lacks `image`, and Portal writes `["text","image"]` for an `unknown`
route too. So after a switch of the Portal alias from a vision model to one
whose image input is unknown, earlier screenshots would reach it as
`image_url` parts (up to nine in one request): an image test the owner never
allowed, and one that llama-server without a projector rejects.

`openclaw-compaction-budget.json` extends `pruneProcessedHistoryImages` in
`selection-BEwSQKM-.js`, which runs before every model request. Before the
latest user message, every image block of a
`pixel_ods_workspace_preview_inspect` result, direct or as a Tool Search
`tool_call` result whose first block names that child
(`openclaw-image-envelope.json`), becomes the runtime's own
`[image data removed - already processed by model]`. The result's text,
including the screenshot sentence, is unchanged. Owner images and other tools'
images keep the runtime's three-turn rule. The cleanup is idempotent, so later
requests resend identical history; the prompt prefix changes once, at the
first request of the run after the one that took the screenshot. A host retry
prompt is a user message as well, so a retry within a run no longer sees the
image, only its sentence.

The transcript is unchanged. The recipe keeps the previous reviewed hash in
`previousReplacements`, so installed Linux/WSL runtimes migrate through the
original source with `--compaction-budget` (and `--restore`); the macOS staged
bundle composes it as before.
`runtime_preview_screenshot_context.integration.mjs` runs the reviewed prune
with the Tool Search envelope and the openai-completions request ODS's gateway
route uses:

```sh
OPENCLAW_PACKAGE_DIR=/path/to/openclaw node --test \
  ods/extensions/services/pixel-agent/tests/runtime_preview_screenshot_context.integration.mjs
```

## Tool-call arguments under the native macOS stream order

OpenClaw 2026.6.33's openai-completions transport parses streamed tool-call
arguments with a JSON repair that runs before a strict parse. When a string
so far ends like a Windows drive prefix, as Python's `as f:` or `as e:` does,
the repair keeps each following control escape such as `\n`, `\t` or `\r` as
a literal backslash sequence until the next quote or colon. Upstream, the embedded
runner's malformed-argument wrapper re-parses the raw argument text strictly
and replaces that result, so valid JSON reaches the tool unchanged.

The native macOS bundle's buffered stream-progress patch moves the model-call
diagnostic observer inside that wrapper. The observer returns a proxy that
intercepts reads of the stream's iterator and `result` but not writes. The
wrappers applied outside it (argument repair, tool-name trimming, standalone
text tool-call promotion and sensitive-stop handling) assigned their
replacements to the proxy's target, and the proxy kept returning its own, so
none of them ran on macOS. In one recorded macOS qualification round, 12 of 97
distinct tool calls (file writes, edits and commands carrying Python code) had
literal `\n` where the model wrote line breaks. One edit meant to fix that was
corrupted the same way, and the coding task failed.

`openclaw-diagnostic-stream-writes.json` repairs the exact
`attempt.model-diagnostic-events-DqqiPQPY.js` bytes. The proxy now keeps an
iterator or `result` assigned to it and returns it on later reads. Each outer
wrapper still reaches the observer through the value it read before assigning,
so the observer continues to see the raw transport events and final result.
Other writes reach the stream as before. Where the observer is outermost, as in
the upstream order used by Linux/Windows-WSL, nothing assigns to it and
behavior is unchanged.

Native macOS composes the recipe into its protected bundle before the
selection repair, so the stream-progress patch still applies last.
Linux/Windows-WSL applies the same recipe with `--diagnostic-stream-writes`;
restore uses the existing backup contract (`--diagnostic-stream-writes
--restore`). The transport's JSON repair itself is unchanged: arguments longer
than the wrapper's 64,000-character buffer still reach the tool as the
transport parsed them, on every platform.

The real-gateway test composes a runtime copy with the macOS bundle code and
writes a Python file through a deterministic provider. Set
`ODS_DIAGNOSTIC_STREAM_WRITES_RED=1` to keep the pinned observer and see the
literal `\n`:

```sh
OPENCLAW_PACKAGE=/path/to/openclaw node --test \
  ods/extensions/services/pixel-agent/tests/runtime_diagnostic_stream_writes.integration.mjs
```

The transformation includes a small portion of OpenClaw's MIT-licensed
diagnostic events runtime, Copyright (c) 2026 OpenClaw Foundation; see the
[upstream MIT notice](upstream/THIRD_PARTY_NOTICES.md).

## Delegated completion across an automatic session reset

OpenClaw 2026.6.33 applies its daily/idle session policy when a native child
completion resumes its parent. A request crossing the default 04:00 reset
boundary could therefore replace the parent session before ODS checks the
child's admission proof, interrupting an otherwise valid delegated response.

`openclaw-subagent-session.json` binds the repair to exact `agent-Dme3RrGI.js`
bytes. Only backend handoffs with the native ODS owner, child and announcement
run formats preserve the current session across automatic expiration. ODS still
requires the original exact owner/session/spawn receipt before inference or
tools. Explicit owner resets and new owner requests retain the native policy;
a late completion cannot adopt the replacement owner session.

Linux/WSL apply the reviewed recipe with `--subagent-session`; macOS composes
the same recipe into its verified runtime bundle. The existing private backup,
receipt and guarded `--restore` contract applies. The real-gateway fixture
crosses 03:59:59 to 04:00:01 inside its disposable gateway process only, verifies
both child results and the revised final response, and separately exercises
native owner reset with a queued late announcement. It never changes the host
clock or the installed package.

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
