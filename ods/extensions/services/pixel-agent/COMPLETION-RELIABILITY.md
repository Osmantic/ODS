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

Each prompt also receives the current host UTC time. The model must preserve
the owner's requested date/timezone, check source publication dates and avoid
confusing its training cutoff with the actual date.

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
