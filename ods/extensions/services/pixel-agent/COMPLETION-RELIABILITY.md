# Completion reliability

The Portal integration uses OpenClaw's `before_agent_finalize` hook to recover
premature final replies. This is model-independent orchestration, not a claim
that a small model can perform every task.

`completion-assurance.mjs` tracks each run independently. It detects a bounded
set of Portuguese/English execution promises and explicit current-news requests.
Discovery alone is not execution, and an empty search is not supporting evidence.
The guard can request at most two additional passes, inside the existing run's
tool, cancellation and progress limits. Short follow-ups keep the preceding
owner request in context. Literal-output requests, ordinary conversation,
clarifications and candid limitations do not initiate recovery.

Web source URLs come from structured results, never invented model links. A
missing attribution triggers revision; if the harness refuses another pass after
possible side effects, delivery retains the answer and appends actual returned
source links. This verifies source provenance, not every statement in a summary.
If execution never occurred, delivery reports incompleteness instead of another
promise. Existing Operations, publication and permission checks take precedence.
Recovery does not replay side effects or grant additional permissions.

## Saved project delivery

A model can successfully write an HTML project and then stop without calling
`pixel_ods_workspace_preview`. The pinned harness refuses another model pass
after potential side effects, so a revision instruction alone cannot reliably
finish that delivery.

Finalization now permits one internal publication attempt for one unambiguous
directory whose `index.html` was successfully written in the current run. It
uses the existing preview tool and Unix-socket or Docker Desktop transport,
including host path validation, immutable snapshot creation and HTTP readback.
It never writes project bytes, starts a server, reruns commands or asks the
model to repeat the task. The ordinary preview guard still evaluates the exact
directory. The trusted receipt reaches the existing ingress preview card.

Recovery requires the same active run/session/workspace, permitted publication,
no pending tools or processes, no failed commands, and no missing requested
verification or visual-edit prerequisite. Explicit no-preview requests,
clarification, mixed Operations/download/extension tasks, exhausted budgets,
ambiguous projects and previous publication attempts do not trigger it. Custom
tool-policy restrictions conservatively disable recovery; ordinary tools retain
their normal policy handling. Publication waits at most 30 seconds and stop on
run invalidation. A late receipt cannot revive a cancelled run. A host snapshot
may already exist when cancellation interrupts the receipt wait; cancellation
does not promise rollback of publication.

This is publication recovery, not code repair or proof of playability. The
receipt explicitly scopes its evidence to the snapshot and HTTP readback.
Missing files, unsafe paths, unavailable preview services and rejected receipts
remain incomplete deliveries rather than fabricated success.

`tests/preview_delivery_recovery.test.mjs` and the tool-loop tests cover policy,
deadlines, cancellation, duplicate finalization and invalid receipts. The real
pinned-harness fixture `tests/runtime_preview_delivery.integration.mjs` exercises
an actual file write, premature model finalization, the existing publication
tool against a deterministic host response, and the real ingress SSE preview
card. It uses disposable state and no real model or production data. Set
`OPENCLAW_PACKAGE` to the pinned installed runtime to run it. CI runs the guard
tests on Windows, macOS and Linux, and the process-level fixture on macOS/Linux.

Each prompt also receives the current host UTC time. The model must preserve
the owner's requested date/timezone, check source publication dates and avoid
confusing its training cutoff with the actual date.

## Tool-limit finalization

When the run-progress budget (`run-progress-budget.mjs`) or the research
web-loop terminal (a web tool requested again after two research-budget
refusals) stops a response, the limits are unchanged and every tool stays
blocked. `progress-finalization.mjs`
grants one tool-free answer turn instead of discarding the gathered evidence.
OpenClaw applies `tool_result_persist` to the saved transcript only, so the
model learns of the stop through the refusal of its next tool call, whose text
is one fixed instruction: answer from evidence already returned, keep the
requested format, and mark missing or unverified items. The following model
call is the answer turn. Parallel siblings in the refused call's model round
receive the same instruction; with no observed model round, the next tool call
ends the run. A model that answers without another tool call is treated the
same way.

The owner receives that answer followed by host facts the model cannot alter:
the tool-limit note, a failed or pending test result, cited links that were
never read (when the owner asked for sources to be opened), and the last
verified preview or an explicit statement that none was verified. The outcome
stays `failed`; a research-loop stop also notes that the web research
allowance was used up. A tool call in the answer turn aborts the run at that
tool boundary; an empty, silent, promise-only, tool-like or oversized answer, a
further model call, owner cancellation, or an unverified localhost URL in a
visual task all fall back to the original stop text (the research-loop stop
text for that path). Operations, exact
downloads, managed extension requests and team coordination keep the strict
stop text. The instruction is constant text at the end of the conversation,
never system-prompt content.

## Silent owner replies

An owner-authored dashboard or Portal message (a `user`-triggered run in the
`agent:pixel:openai-user:ods-…` session) always needs a visible reply. If the
final reply is only OpenClaw's silent sentinel (`NO_REPLY`, `HEARTBEAT_OK`, or
their JSON forms), `owner-visible-reply.mjs` requests one revision pass with a
fixed instruction. A second silent reply keeps the ingress fallback ("Pixel
ended without a visible answer"). OpenClaw already retries an empty final reply
once before this hook runs; heartbeat, cron and team turns keep `NO_REPLY`
semantics, and the harness still refuses a revision after side effects.

`tests/progress_finalization.test.mjs`, `tests/owner_visible_reply.test.mjs`
and the real-harness fixtures `tests/runtime_progress_finalization.integration.mjs`
and `tests/runtime_owner_visible_reply.integration.mjs` cover both paths.

## Search availability

Existing SearXNG installations can report HTTP 200 with zero results while their
upstream engines return CAPTCHA, access denial or rate limits. Inspect
`unresponsive_engines` before treating that as an absence of news. The pinned
native `parallel-free` provider is supported by `host/native_search.py` and is
the default for new installations. Existing owners' provider choices are retained
by the installer. Provider service availability remains an external dependency;
neither engine guarantees coverage of a particular date or source. Only the
public search brief should be sent to an external search provider.

## Interactive clarification

`pixel_ods_ask_user` presents one to three questions, each with two to four
choices and an optional free-text answer in the dashboard. A validated tool
receipt pauses subsequent tools and finalizes the turn with `pending` delivery.
The model cannot choose for the owner or turn this card into Operations approval.
The ingress releases the bounded `pixel_questions` envelope only on the verified
terminal SSE frame. Malformed receipts fail closed. Questions and draft answers
are retained with the conversation; Continue sends ordinary owner text in that
same chat, without UI metadata in the model request.

The plugin loader isolates imports to its package. Keep its `questions-schema.mjs`
and the independently installed ingress `questions_schema.mjs` aligned; the
parity test enforces this. At the tool input only, unambiguous small-model
aliases (`text`, `choices`, `{text: ...}` options, omitted IDs and `wait: true`)
normalize to the canonical contract. A boolean `required` hint never selects or
submits an answer. Conflicting fields and unrecognized attributes fail.
When the owner explicitly asks for questions with choices, a narrow presentation
fallback also recognizes a complete final preference question followed by two to
four bullet options. It creates the same bounded card without another model call.
It rejects surrounding prose, code, translations, numbered steps and plans.
A model can still miss a suitable clarification or produce unsupported wording.

## Focused verification

For an explicitly published repair naming one workspace file, recognized
"do not create new files" or "do not edit other files" instructions activate a
narrow existing-file intent gate. Direct and Tool Search calls must target that
exact file after a successful read in the current run. Only update-only patch
syntax is admitted; add/delete/move, other paths, arbitrary shell/process calls
and excluded web tools are blocked. A later failed read invalidates that read
evidence. Ordinary unconstrained repairs retain their existing tools.

This gate is not a general natural-language permission parser, shell sandbox,
or atomic filesystem existence check. Core file tools and sandbox policy still
own path/link/race containment. Source guard tests in
`tests/workspace_general_routing.test.mjs` do not establish installed runtime
behavior or general-task success.

Run `node --test tests/completion_assurance.test.mjs` and the existing tool-loop,
progress-budget, prompt-contract and task-activity tests from this directory.
`tests/runtime_completion_assurance.integration.mjs` additionally runs the real
pinned OpenClaw 2026.6.33 harness against a synthetic unreliable model when
`OPENCLAW_PACKAGE` points to that installed package. It uses a disposable home,
loopback-only fixture tools and no production credentials. Where WSL intercepts
loopback connections, run this fixture in a private network namespace with its
loopback interface enabled.

Also run `tests/ask_user.test.mjs`, `tests/pixel_ingress.test.mjs`, the dashboard's
`PixelQuestions.test.jsx` and `Pixel.test.jsx`, and `ods/tests/test-pixel-host-install.sh`.
Run host permission tests on Linux, where the production services execute;
Windows filesystem modes do not implement the required Unix ownership contract.

Real-model checks should include an initial research request, a short continuation
after a promise, missing citations, unavailable search, greetings and a small
workspace action. Passing these cases is not certification for all models,
languages, operating systems or tasks.
