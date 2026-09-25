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

## Cited pages that were not read

When the owner asks for sources to be opened, every cited public URL needs a
successful page read (a 2xx `web_fetch` with text, or matched targeted
extraction) in the same response. A citation matches a read `url` or `finalUrl`
after conservative normalization only: scheme and host case, default ports,
the fragment and one trailing path slash. The query string is kept. A link the
answer itself labels as unverified or not opened stays as written.

`web_fetch` decodes any 2xx body as text, so a PDF or an image comes back as
undecoded bytes (tower2 round 061 and tower1, 2026-09-25: the NVIDIA RTX 5070
user guide PDF, listed as read). Such a result is not a read
(`document-body.mjs`, by content type or by the bytes themselves): it counts
for no citation, the host's list of pages read or the stop synthesis, and its
persisted result is replaced by a receipt saying so which, for a PDF, points
to `pixel_ods_web_extract`. The shared reader extracts a PDF's text
(`pdf-text.mjs`: the first 10 pages of at most 8 MB, in a worker thread
terminated at a deadline of at most 5 seconds and half the read's timeout) or
returns a not-read receipt with the type, size and reason (too large,
timeout, encrypted, no extractable text, unsupported).

Models often cite event detail links they saw on a listing page without
opening them. Before judging the answer, the host reads those pages itself
(`citation-verification.mjs`): at most four public URLs per answer, only for a
source-read request whose run already got web results, and only when every
unread citation can be checked. The reads use the same guarded reader as
`pixel_ods_web_extract` (OpenClaw's strict SSRF guard, no environment proxy,
three redirects, 1 MB, a browser-compatible request with one plain fallback
after a 403/406, text extraction; a bot challenge counts as not read), in
parallel under one 4-second
deadline, and each counts against the response's page-reading and total web
allowances. Nothing is read when the operator disabled or denied page reads,
the owner excluded web access, a private-network request was denied, the run
was cancelled, or the URL was already host-read in this run.

A host read counts only when the page returns 2xx HTML, text or PDF text on the cited
site (not its root) and carries the claim anchors the answer attaches to that
citation: the words of the item's title (title field, heading, bold name, link
text or leading proper nouns) together, and the attributed date (day, month
and any stated year), within 400 characters and with no other date between
them. A time stated next to the date must not be contradicted; a claim without
a date is anchored by its numbers. Anchors never come from the URL and
URL-shaped page text is ignored, so a slug such as `flyers-capitals-9-26-26`
matches only the page's own display title and date. Error pages, cancelled or
postponed events and query strings that could echo the terms do not count. A
verified page is a separate host-verification receipt, not a model read. When
every unread citation verifies, the answer is delivered unchanged without
another model turn; otherwise verified pages count as read and the rest follow
the revision below. `tests/host_citation_verification.test.mjs` replays the
tower3 and tower1 fleet cases.

An answer that cites unread URLs gets one revision (idempotency key
`ods-opened-source-attribution`). Its fixed instruction names exactly those
URLs and asks the model to replace each with a page it actually read in this
response or remove it and mark the claim unverified; where each item needs its
own source, it prefers the item's own page to a shared listing. If the answer still cites
unread URLs afterwards, or the harness refuses the revision, the owner receives
that answer with only those links replaced by `[source not verified]`
(`[fonte não verificada]` in Portuguese), Markdown link syntax around them
flattened to text, and one fixed source-check note. Nothing else in the answer
changes and no link is added. The outcome stays `failed`, so the harness and
owner still see that verification was incomplete. The whole answer is replaced
with the incomplete-research text only when none of its cited URLs was read,
too little prose remains outside links, or it exceeds 20,000 characters. If a
tool limit stops the revision pass, the answer from its tool-free answer turn
(below) is newer and supersedes this armed delivery.
`tests/partial_citation_delivery.test.mjs` replays the tower1 fleet case.

## Perplexica research

`pixel_ods_research` sends a research brief to the owner's installed
Perplexica (Vane) service. In speed and balanced mode Perplexica answers from
search-result snippets without reading any page. On the fleet journeys only 1
of the 25 links in its answers came from its own returned sources, and 18 of
the 25 were dead (`findings/perplexica-fast-search.md`). The tool is therefore
orientation only:

- Its factory offers it only while Perplexica answers `GET /api/config` with a
  chat model and an embedding model selected. The check is cached for 60
  seconds, refreshed in the background when the factory runs, and never delays
  a run. A refused connection during a call marks Perplexica absent. The
  configuration body also holds provider API keys, so only the four model
  identities are kept from it. The tool is deferred behind Tool Search:
  offering or hiding it changes the Tool Search catalog, not the system prompt
  or the directly visible tools.
- OpenClaw 2026.6.33 caches a plugin's tool descriptors per agent configuration
  once its factories have returned every tool in the manifest, and then builds
  later tool lists from that cache without calling the factories. So a host
  without Perplexica never lists the tool, but once Perplexica has been
  available, the tool stays listed until the gateway restarts or its
  configuration changes. A call to a Perplexica that has since stopped returns
  an unavailable result; after that, calls fail as a missing tool runtime until
  a later check finds Perplexica configured again.
- The model sees Perplexica's answer, and each returned source with its title
  and a capped search snippet (300 characters for cited sources, 160 for the
  first eight uncited ones, 3,500 in total, at most 20 sources, cited sources
  kept first).
- The result is sized for Tool Search, the tool's only path. There the model
  reads one text block, `JSON.stringify({tool, result}, null, 2)`: the catalog
  entry with the full description, then the result with `details`, all escaped
  a second time. OpenClaw keeps a block within the agent's
  `contextLimits.toolResultMaxChars` unchanged and cuts the middle of a longer
  one, which drops sources and the closing evidence marker. So that whole
  block fits the cap minus 200 characters, at most 12,000 (the 4,000-character
  installer floor when the cap is unknown). For the test's large answer (40
  lines citing 25 sources with snippets), a 4,000 cap keeps about 1,400
  characters of answer and no sources, 8,192 the minimum answer and 13
  sources, and 12,000 the minimum answer, 20 sources and 5 snippets; the
  header says what was left out. `details` carries only the URLs of up to five
  cited sources, each at most 300 characters, and counts.
- Every `http(s)` link in the answer that is not among the returned sources,
  including private addresses, is replaced with
  `[link not in Perplexica sources]` before the model sees it. Source matching
  ignores http/https, a leading `www.` and one trailing slash. Links written
  without a scheme are not checked, and the result says so.
- Vane's `scrape_url` action opens any URL its model names, without address
  validation, from the Perplexica container on the ODS network, and Vane
  offers it in every mode. ODS disables it when the container starts
  (`extensions/services/perplexica/docker-entrypoint.sh`); that, not the
  brief, is what keeps a brief or a search result from making Perplexica open
  an internal address.
- The brief is at most 1,000 characters, and common address forms are removed
  before it is sent: URLs with any scheme, `www.` names, IP literals
  (including short, integer and hexadecimal IPv4 with a port or path),
  `host:port`, `localhost`, local-only names such as `.internal`, dotted names
  with a path, and lowercase single-label names with two or more path
  segments. Full-width and ideographic forms are folded first. This is a
  heuristic with known gaps (a single-label name with one path segment, a bare
  short or integer IPv4), not a guarantee. A brief that was only addresses is
  refused.
- One call uses one search and one page-reading unit, and needs two units of the
  total web allowance. A response may call it once; a repeat, like an unusable
  brief, is refused before it runs and uses no allowance. The call does not
  end an unread-search streak. The default wait is 120 seconds
  (`PIXEL_ODS_RESEARCH_TIMEOUT_MS`).
- Nothing it returns is a page read: its answer and sources never produce a
  read receipt, so citing them after a source-read request still needs
  `web_fetch` or `pixel_ods_web_extract`, or the host citation check. The
  sources in `details` (up to five its answer cites) count only toward the
  weaker "research returned sources" check, in the direct and Tool Search
  forms alike.

`tests/perplexica_research.test.mjs` replays the measured answers
(`tests/fixtures/perplexica-answers.mjs`: 24 of 25 speed and balanced links and
37 of 37 quality links are replaced), checks the Tool Search size from 4,000
to 131,072 characters of cap and the brief filter.
`runtime_tool_surface.integration.mjs` checks the size through the real
`tool_call` at 4,000, 8,000 and 12,000. `tests/perplexica_availability.test.mjs`
covers the probe and the not-installed case, and
`tests/perplexica_research_budget.test.mjs` the allowance and the read rule.
`ods/tests/test-perplexica-entrypoint.py` runs the `scrape_url` patch on the
action objects from the current and the previous pinned image and executes
them.

## Owner-requested text

`requested-literals.mjs` checks each published snapshot for exact text the
owner asked for (a cued quotation or a counted list of names). A miss never
blocks publication: the publication result carries a note, and delivery stays
`failed` with the preview kept and a fixed statement of the missing text.

Before that failure, the model gets one bounded revision per response. Its
fixed instruction names the missing text as a JSON list and asks the model to
add it exactly as requested (for example, as the card heading when the owner
described a card title), republish with `pixel_ods_workspace_preview`, and keep
everything else unchanged. The pinned harness refuses a `before_agent_finalize`
revision after potential side effects, and a publication is one (tower1 round
067: `before_agent_finalize requested revision after potential side effects;
finalizing`). So the instruction goes on the model's next successful tool
result for that same unrepaired snapshot, after the publication note, such as
an inspection of it. Only when no such result occurs does finalization request
it as a revision (idempotency key `pixel-ods-workspace-preview-requested-text`,
one attempt). A republish that contains the text is judged normally; otherwise
the unchanged failure delivery stands and no further revision is requested.
Nothing is revised after owner cancellation or a tool-limit stop, and the
revision grants no tool allowance. `tests/requested_text_revision.test.mjs`
replays the tower1 case.

A listed name can also be on the page but not as its item's heading (tower2
round 073: "Dawn jazz" was only a badge, and the card heading read "Dawn Jazz
at the Rose Pavilion"). This is reported through the same note, revision and
delivery, with fixed text that quotes the longer heading, but only for names
from a counted list: no hN heading equals the name, a heading contains it as
whole words and names no other listed item, and another listed item is exactly
a heading of that same level. The workspace guide also tells the model to use
owner-named items verbatim as headings or labels.
`tests/requested_heading_revision.test.mjs` replays the tower2 case.

A listed card name can also be on the page but in no heading at all (tower2
round 082: "Dawn jazz" was only a badge, and its card's `h2.event-title` read
"Sunrise Sessions"). This is reported the same way, with its own fixed text,
only when the owner listed cards or headings ("three event cards"; words after
the noun, as in "tabs with titles", do not count), no hN heading equals or
contains the name, and a group of at least two headings with the same tag and
class list includes another name from the same list exactly. Evidence from
another list (section headings beside dish cards) never counts, and the
round 073 rule above also takes its same-level evidence from the same list
only. Comments, scripts and styles are never heading text; a script that may
render the name still keeps the check silent.
`tests/requested_item_heading_presence.test.mjs` replays that page.

The same path covers files the owner lists for a named directory that is then
published (tower2 round 082 coding-v1: "create a public directory with
index.html, test-results.txt ..." was published without `test-results.txt`,
which the model had written to the project root). Each list part must be one
file name after plain lead words (such as "raw byte-for-byte source copies"),
optionally followed by a description naming no other file. The whole list is
skipped when any part is a nested list, a path, "a copy of x.py", a file type
the host preview cannot publish (its `ALLOWED_SUFFIXES`, mirrored as
`PREVIEW_FILE_SUFFIXES` and kept equal by a contract test), optional or
conditional ("optional", "if you have time", "you can"), removed, or content
or a runtime result ("the sales.csv data as a chart", "export.csv the page
generates"). The names bind to the published directory's name and are checked
against the receipt's complete published path list.
`tests/requested_published_files.test.mjs` replays that case.

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
same way. OpenClaw's in-session auto-compaction summarizes through the run's own
model stream; the summarization calls it starts (at most two, bracketed by
`before_compaction`) are not counted as turns, so a compaction after the answer
cannot forfeit it. Any other further model call still does.

The owner receives that answer followed by host facts the model cannot alter:
the tool-limit note, a failed or pending test result, cited links that were
never read (when the owner asked for sources to be opened), and the last
verified preview (with any owner-requested text the published page lacks) or an
explicit statement that none was verified. The outcome stays `failed`; a
research-loop stop also notes that the web research allowance was used up.

Plugins cannot remove tools from a single model call, so some models still
call a tool in the answer turn. That call is refused and the run is aborted at
that tool boundary, as before. When the same assistant message also carries
substantive answer text, that text is kept as a partial answer (tower3 r8):
`before_message_write` observes the message, and it is matched to the refused
call by tool-call ID, never by timing. OpenClaw writes the message before it
dispatches the message's calls; the reverse order is handled too. The text
must pass every check a tool-free answer passes and, with narration such as
"Let me search once more" set aside, still hold at least 160 letters or digits
across at least two other lines or sentences. Because the aborted run never
reaches `before_agent_finalize`, its cited pages get the same bounded host
verification there, and `/pixel-ods/verification` waits for it. The delivery
adds a host fact that the requested calls were refused and did not run.

An empty, silent, promise-only, tool-like, narration-only or oversized answer, a
further model call, owner cancellation, or an unverified localhost URL in a
visual task all fall back to the original stop text (the research-loop stop
text for that path). Unless the owner cancelled, that text is followed by the
host's list of pages read successfully in the response (tower2 round 061):
current-run `web_fetch` and targeted-extraction read receipts plus host
citation verifications, deduplicated, at most eight with a count of the rest,
each with its page-reported title reduced to plain words when one is known.
Only the list varies. Operations, exact downloads, managed extension requests
and team coordination keep the strict stop text, with no list. The instruction
is constant text at the end of the conversation, never system-prompt content.

### Stop synthesis

Some models keep calling tools until the stop although the pages they read
already hold most of the answer (tower1 round 069, tower2 round 061). When a
progress, research-loop or failure stop leaves no answer text, the owner did
not cancel, and at least two pages have read receipts, `stop-synthesis.mjs`
makes ONE extra model request before delivery, then never retries. It uses
OpenClaw's plugin LLM runtime (`api.runtime.llm.complete`): the same
configured provider and model as Pixel's turns, a request with no tools,
`max_completion_tokens` 1200, temperature 0.2, and a 75-second timeout. The
runtime targets the default agent and a plugin may not override the agent or
model, so the synthesis runs only when Pixel is the default agent. The request
carries one fixed system instruction and a user message with the owner's
request and up to eight page excerpts, in read order:

- **Instruction:** answer only from the excerpts, cite only their URLs, use
  `null` or "not found" for anything they do not establish, never substitute a
  related figure, and no process narration.
- **Excerpts:** at most 1,800 characters per page and 12,000 in total, taken
  from the read ledger and selected around the request's terms and quantities.
  A page's `web_fetch` and targeted extraction are merged, the extraction
  first.
- **Page data:** unwrapped from OpenClaw's untrusted-content markers, rewrapped
  in numbered data blocks, and neutralized so page text cannot open or close a
  block.

The synthesis is skipped when:

- the owner sent a new message (a newer run owns the session, checked again
  when the reply arrives);
- the loopback route's `/health` probe fails;
- the run's last model call failed in transport;
- an earlier synthesis in the process failed within five minutes;
- the stop was receipt-based (Operations, exact downloads, managed extension
  requests, team coordination);
- an answer, tool-free or partial, already exists.

The reply must pass the partial-answer checks (substantive, not narration,
not tool-like, no unverified localhost URL). The #6680 host citation
verification applies to it, and cited links without a read receipt are listed
as unverified. The owner then receives it as the `failed` partial answer, with
the tool-limit note, a note that ODS asked the same model once more without
tools, the other host facts, and the pages it was given. Otherwise the
fallback and page list above apply. `/pixel-ods/verification` waits for this
bounded step; on tower2 (qwen3-coder-next) it took 3–9 seconds.

## Silent owner replies

An owner-authored dashboard or Portal message (a `user`-triggered run in the
`agent:pixel:openai-user:ods-…` session) always needs a visible reply. If the
final reply is only OpenClaw's silent sentinel (`NO_REPLY`, `HEARTBEAT_OK`, or
their JSON forms), `owner-visible-reply.mjs` requests one revision pass with a
fixed instruction. A second silent reply keeps the ingress fallback ("Pixel
ended without a visible answer"). OpenClaw already retries an empty final reply
once before this hook runs; heartbeat, cron and team turns keep `NO_REPLY`
semantics, and the harness still refuses a revision after side effects.

`tests/progress_finalization.test.mjs`, `tests/partial_finalization.test.mjs`,
`tests/stop_synthesis.test.mjs`, `tests/owner_visible_reply.test.mjs` and the
real-harness fixtures `tests/runtime_progress_finalization.integration.mjs`,
`tests/runtime_stop_synthesis.integration.mjs` and
`tests/runtime_owner_visible_reply.integration.mjs` cover these paths.

## Owner cancellation

Every recovery decision is bound to the run that armed it. An acknowledged
owner cancel (`/pixel-ods/abort`) voids that run's completion assurance: no
further revision, no armed replacement text, and any host citation read it is
waiting on is aborted. OpenClaw already refuses a revision for an aborted
attempt; if one started just before the cancel, its prompt is told to stop and
its tool calls stay refused.

OpenClaw keeps the cancelled request in the transcript without an answer, and a
model otherwise treats it as still pending (tower1 round 067: a later "Reply
with exactly …" ran the cancelled research first). The first owner turn after
the cancel therefore gets fixed, model-only context (a `before_prompt_build`
`prependContext`, never persisted as owner text) saying that request is
withdrawn unless the new message asks to resume it. In that run, web evidence
does not by itself require attribution: the missing-source revision applies
only when the current message asks for research or source reads, or resends the
cancelled request. A later message sees none of this.
`tests/cancel_request_binding.test.mjs` replays the fleet case and a cancel
before, during and after a revision is armed; the real-harness fixture
`tests/runtime_cancel_recovery.integration.mjs` cancels through the ingress.

## Search availability

Existing SearXNG installations can report HTTP 200 with zero results while their
upstream engines return CAPTCHA, access denial or rate limits. Inspect
`unresponsive_engines` before treating that as an absence of news. The pinned
native `parallel-free` provider is supported by `host/native_search.py` and is
the default for new installations. Existing owners' provider choices are retained
by the installer. Provider service availability remains an external dependency;
neither engine guarantees coverage of a particular date or source. Only the
public search brief should be sent to an external search provider.

## Research pacing

Search results are leads, and each one adds several kilobytes to the live
context. On the fleet, research runs issued five to seven searches before
reading a page. The context guard then compacted the conversation, the leads
disappeared from view, the model repeated the same searches, and the search
allowance ran out before any page was read. `plugin/research-pacing.mjs` adds
three run-scoped checks, all delivered as tool results so the system prompt
stays unchanged:

- After three consecutive searches that returned leads without a page read in
  between, the next search is paused once and the model is asked to read the
  leads first. It may search again immediately if none fits.
- A search that adds no term to an earlier search in the same response (same
  model numbers and years, filler words such as "official" or "site" ignored)
  is answered with that search's result URLs instead of running again. This
  recovers leads lost to compaction, including after the search allowance is
  spent, while pages can still be read. A deliberate second repeat runs.
- When the owner states the date ("Today is YYYY-MM-DD", "as of ..."), a search
  that names an earlier month is followed by a date check note.

Pauses and recalls run nothing, do not use the search allowance and are not
charged as tool failures; each is limited to two per response, after which
searches proceed unchanged. The fixed evidence and projection notes on search
results are repeated only at the normal coaching interval; the per-call
research budget line stays on every result.

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
