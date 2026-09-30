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

A host read counts only when the page returns 2xx HTML or text on the cited
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

### Owner-requested control names

A quotation right after a button or link noun and a naming cue ("an accessible
button named exactly "Show sold out"", "a link called "Docs"", "a button with
the label "Close"") is also a control name: some button (or link) must have
exactly that accessible name after the page scripts ran. The text itself is
still an ordinary requested literal. Bytes cannot show this: tower2 round 100
had the right button text, but `script.js` ran
`setAttribute('aria-label', 'Show the sold out midnight concert card')` on
load. The model's exact-name click matched nothing, it switched to a CSS
selector, that inspection passed, and the fleet's
`getByRole('button', {name: 'Show sold out', exact: true})` click failed.

Every inspection receipt now carries the load-time names of the page's buttons
and links (`controls`, see `host/preview-inspection-protocol.md`), captured
before any step, hidden ones included. A rendered control is named as the
fleet's default `getByRole(role, {name, exact: true})` names it, so hidden
descendants (an `aria-hidden` icon or chevron, a hidden alternate label, a
`display: none` badge) never turn a correct name into a missing one; a hidden
control keeps its hidden-inclusive name. The latest receipt bound to the current
snapshot decides, whether its steps passed or failed or it came back incomplete
for an untested show/hide change (its `details.receipt`, below). That holds
whatever `error` the harness attaches to the `after_tool_call` event: OpenClaw
2026.6.33 sets one on every error result of a direct call (tower2's transport),
and a thrown call has no receipt to bind. "Named exactly" compares
case-sensitively after whitespace and typographic normalization, "named"
without "exactly" ignores case. A name counts as missing only when the receipt
lists every button and link of the page. A snapshot without such a receipt
(no inspection yet, or a capsule built before `controls`) is unverified, never
failed. The miss keeps the most telling control: the one whose own text is the
requested name while an `aria-label` or `aria-labelledby` replaced it, one
named the same except for letter case, or a control of the other role with
that name. At publication the plugin also records, from the snapshot bytes,
which published files set `aria-label`/`aria-labelledby` from script and which
`aria-label` values the HTML markup carries, only to say where the replacing
name came from.

The repair step travels on that inspection's own result, failed or passed,
because the pinned harness drops finalization revisions after plugin tool
calls. For round 100 it reads: "The owner requested a button named exactly
"Show sold out", but after the page scripts ran no button has that accessible
name: the button whose text is "Show sold out" is named "Show the sold out
midnight concert card" by an aria-label that a published script sets when the
page loads (["script.js"]), which replaces its text as the accessible name.
Remove that override or make it exactly "Show sold out", republish, then
inspect the new snapshot." The inspection tool's own locator feedback for an
exact-name miss says the same from the receipt, instead of blaming hidden
elements. When a rendered control of that role was named exactly the locator's
name at load and no click ran before the step, the feedback says the name is
on the page and asks for a CSS locator for that step. It was written for
role/name steps that matched only Chromium's own name, which keeps the space
beside an `aria-hidden` icon (`" Show sold out"`) while load-time names follow
`getByRole`. Role/name steps now also match the `getByRole` name (the same
rules as the load-time names), so that icon page matches, and the feedback
remains for a page whose control changed between load and the step. An
untested show/hide change prescribes the owner's exact name as the click when
the plan had none, so a correct icon page would otherwise be sent back to the
same unmatchable name. When no inspection covered the snapshot and no show/hide check
already asks for one, the next step asks for an inspection by that exact role
and name. Finalization requests one bounded revision
(`pixel-ods-workspace-preview-control-name`), and delivery stays `failed` with
a fixed statement of the missing name. `tests/requested_control_names.test.mjs`
replays round 100 with the recorded snapshot bytes
(`ods/tests/fixtures/preview-controls/tower2-r100`), the model's two recorded
inspection plans and the names this capsule reports for those bytes, and four
variants of the repaired page whose correct button carries hidden decorations:
no name repair, and delivery passes.

## Show/hide inspection coverage

When the owner asks for show/hide behavior, delivery needs a passing
`pixel_ods_workspace_preview_inspect` plan that asserts one locator with
opposite visibility before and after a click. Two fleet runs passed every step
without that:

- Laptop round 100 (Qwen3.5-9B) inspected `assert-visible(button "Show sold
  out")`, `click(button)`, `assert-visible(".event-card.sold-out.revealed")`.
  It never asserted the card before the click.
- Tower2 round 102 (Qwen3-Coder-Next) inspected
  `assert-hidden(".sold-out-card.hidden")`, `click(button)`,
  `assert-visible(".sold-out-card:not(.hidden)")`. Each locator includes the
  state it checks, so the two can match different elements.

In both runs the tool said "Preview inspection passed" with a caveat, the
model claimed the card was verified, and finalization then failed the
delivery. The pinned harness drops a `before_agent_finalize` revision after
any plugin tool call, so only the inspection result can steer the model.

For such a request the run guard binds a requirement to the exact pending
inspection call: a direct call, or the Tool Search child of a pending
`tool_call`. A passing receipt whose plan has no such transition then returns
`isError: true` with `details.status: "incomplete"` and
`errorCode: "transition_untested"`. The capsule receipt stays in
`details.receipt`.

The text starts `Preview inspection INCOMPLETE - not verified.` and says what
is missing. That is no affected element asserted before the click, or
different locators on the two sides. It then gives ready-to-send arguments:

- **Control.** The model's own first click step, unchanged. If the plan has no
  click, the owner's quoted control name.
- **Target.** One locator, used in both assertions
  (`inspection-target.mjs`). It is chosen against an outline of the published
  `index.html`, read from the same digest-bound bytes as the requested-text
  check (`publishedElementOutline`). The outline holds tags, ids, classes,
  parents, heading names, and the classes the published scripts add, remove or
  toggle. In order:
  1. The model's own locator with its state qualifiers removed from the
     subject: state classes, `:not(.state)`, and hidden/open/aria-hidden
     attributes. It is used only if it names exactly one element that contains
     the heading the owner named. That element's id is preferred:
     `#midnight-card` for tower2, `.event-card.sold-out` for laptop, which has
     no id.
  2. The owner-named heading, with its exact published accessible name.
  3. The owner's phrase as a heading name.
  Without a usable outline, the model's state-free locator is offered with a
  caveat.
- **Direction.** Hidden first, unless the owner asked the click to hide
  something.

A later turn that preserves the behavior keeps the earlier wording.

These stay unchanged: a passing transition of the same snapshot earlier in the
run, failed receipts (returned byte for byte), page errors, and requests
without show/hide behavior. Incomplete is never interaction evidence. A Tool
Search child inspection also keeps its parent's earlier proof, so a later
read-only check still preserves it.

The fleet prompt asks for a show/hide change and an exactly named button at
once. The load-time control names of an incomplete result's receipt still
count (see "Owner-requested control names"), so on the round 100 snapshot the
same result carries the corrected steps and then the name repair, and delivery
stays `failed` for the name. `tests/control_names_transition.test.mjs` replays
that combination with the round 100 bytes, the repaired page and its four
hidden-decoration variants, on direct calls (harness `error` set) and through
Tool Search: correct pages are never sent a name repair and are certified by
the corrected steps.

`tests/inspection_transition_coverage.test.mjs` replays both runs' create and
update turns, with the recorded bytes, receipts and refusals. It sends the
suggested arguments back through the guard until delivery passes.

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

## Search and read

Fleet research turns were slow because of model turns and context compaction,
not page fetching: on tower2 (round 054) seven searches and four page reads put
about 94 KB of tool output into the context, one read took 0.13-0.22 s, and a
compaction took 11-17 s. OpenCode answers the event journey in three calls
because its search returns page text. `pixel_ods_search_read`
(`plugin/search-read.mjs`) does the same without any model call:

- One search through OpenClaw's configured `web_search` provider (called
  in-process, so the tool hooks are not re-entered), then the top results read
  in parallel through the one shared guarded reader that `pixel_ods_web_extract`
  and the host citation check use: at most two reads per host, a 12-second read
  timeout and one 15-second deadline for all reads. The reader's one plain
  retry after a 403/406 runs inside that read's timeout, with the time the
  first request left, and is skipped when under 2 seconds remain; the same rule
  keeps `pixel_ods_web_extract` at 20 seconds and the host check at 4 seconds
  per read, retry included. With `urls` instead of
  `query` it reads up to five given pages (such as detail links from a
  listing) in one call, without a search.
- Candidates keep the provider's order, drop non-public URLs and duplicates,
  list binaries and social sites as leads, prefer a requested `site`, open
  content pages before a site root or search/tag index, and take one page per
  host before a second.
- Each read page yields a short excerpt: line-aligned windows ranked by query
  terms and by the facts the request names (dates, times, prices, board power,
  memory, frame rates). Terms that appear on most of a page's windows
  (navigation, footers) count less, and words that name a fact ("events",
  "price") are matched by that fact's pattern rather than as terms. Inside
  a window only lines with a distinctive term, a requested fact or a date
  are kept, with their neighbours. Same-site links on those lines, never
  the site root, are listed as `[L#]` leads, dated or term-bearing links
  first. An off-site link is listed only when it gives one dated entry its
  own page (see Listings below), and then it ranks first.
- The output is at most 5,000 characters and never more than the live
  `contextLimits.toolResultMaxChars` minus 800. Leads are dropped first, then
  links, then excerpt length; an excerpt that still does not fit is omitted.

Receipts come only from the tool's `details`. A page counts as read for the
cited-page check when it was read with a 2xx text document from a citable
public final URL, a relevant window was emitted, its excerpt was delivered,
and that excerpt carries evidence. Evidence is page text other than the title
and script or style residue, at least 40 characters of it (80 for a `urls`
overview). In search mode it must also name a query term and, when the
request names facts (dates for events; prices, board power or memory for
components), show one of them. So a title with a line of JavaScript, a site's
own bot check, city navigation without a dated event, or a lone "$35" or
"24/7" is listed as opened but not citable, and the host citation check still
reads the page when it is cited. On the 2026-09-25 tower2 measurement, 24 of 73
receipts at the first PR head and 8 of 72 over the #6699 reader were such
pages. A final URL that cannot be cited (a trailing-dot host, an IDN top-level
domain) makes the page not read: its text is not shown and the requested URL
is never receipted for it. The requested URL counts only when it is the same
document as the final URL (scheme, `www.` and one trailing slash normalised);
after any other redirect only the final URL counts. Search results and `[L#]`
links are leads; citing one still requires a read, or the host citation check.
Each page's host line carries only its tag, URL and status and precedes its
own untrusted-content boundary; the page title is printed inside it. Page
lines are indented, and marker-like text, `[R#]`/`[L#]` tags and
chat-template tokens in page text are neutralised, so a page cannot forge a
receipt line. Through Tool Search's `tool_call` the model also sees
`details`, so it carries no page-supplied text outside a boundary: search
results keep only their URLs, and a receipted page's title stays in its own
untrusted-content envelope, which the host unwraps for its read-page list.

A call costs one search (none with `urls`) and its page reads. The guard
charges them before the call and lowers `maxPages` to the reads left after a
reserve for the host citation check (four reads, or a quarter of a smaller
page-reading allowance); reads the tool never attempted are refunded from its
bound result, exactly once, direct or through Tool Search. A call refused only
because of that reserve ran nothing: it is a free correction, like a paused
search, and never counts toward the web-loop stop, since single-page reads and
searches remain available. A search_read that
repeats an earlier search_read with the same site preference is recalled once
with the pages that search already read; it is never paused for unread leads.
The tool is offered only where the runtime search API exists and the
operator's configuration permits both the search and page reads (page reads not
disabled or denied, `web_search` enabled and not denied, no trusted
environment proxy for web fetches).

### Listings and per-item sources

Fleet evidence (tower1 round 091, integration build `2f89f3ea`): asked for
"a direct official source URL" for each of three Philadelphia events, Pixel
answered in 3 calls and 23 s, but cited an aggregator's live-music list for
one event and the same Visit Philadelphia season guide for two. On `main` the
same journey took 17 calls and cited venue pages. Every cited page had been
read, so the read-receipt check passed the answer. The guide links each
entry's title to the entry's own site, but `search_read` listed only
same-site links, so the model never saw those own pages. The model does the
same without `search_read`: in round 092 on `main` (`04f0a835`, tower2,
Qwen3-Coder-Next, 29 calls) the answer cited one Visit Philadelphia month
guide, read with `web_fetch`, for two of its three events. So the check below
covers every read path, not only `search_read`.

- **Listings** (`plugin/source-kind.mjs`). A read page that names at least
  five distinct dated entries is a listing: a calendar, a season guide, an
  aggregator's list. Each dated line is keyed by its own words without dates,
  times and filler; a bare date line takes the nearest line above that names
  something. A schedule of one event's dates, or a detail page with a short
  sidebar, stays under five. `search_read` marks a listing on its host line
  (`| listing: N dated entries`) and in `details.pages[].listing`, and adds
  one fixed header sentence: a listing is a lead for each entry, not that
  entry's own page.
- **Own-page links.** An off-site link is kept when it sits within 400
  characters of a date, its label is not an action ("Buy tickets"), and it is
  either an "official site" link or the entry's title (at least half its line)
  naming the link's host or path: "DesignPhiladelphia Festival" to
  designphiladelphia.org, "Halloween Nights at Eastern State Penitentiary" to
  easternstate.org. Maps, social sites, ticket buttons, links inside a
  sentence and footer partners are not kept, and a subdomain of the same site
  is not off-site. Such links print as `[L#]` next to the entry title;
  `details.pages[].ownLinks` keeps up to 48 per listing (URLs only; through
  Tool Search, where the model also sees `details`, only the printed ones).
- **Other read paths.** Completion assurance also judges a `web_fetch` page
  (its markdown text, with the same own-link rule) and a
  `pixel_ods_web_extract` excerpt (text only, so no own links) as listings.
- **Per-item source check** (`plugin/item-sources.mjs`, called from
  `before_agent_finalize` after the completion checks pass). It applies only
  when the owner asked for a source per item ("for each ... a direct official
  source URL", "the official link for each event"), not to "cite your
  sources". An item is a citation whose segment names a subject and a date or
  number (the same segmentation as the host citation check). An item is
  flagged when its only cited page is a listing read in this run, unless the
  page is the item's own (its host is named after the item, or its path names
  it twice) or the answer says plainly that the item's own page was
  unavailable, next to the item or in a note that names it ("the official site
  returned 403"). The owner's own request words (the city, "events") never
  identify an item. Flagged items get one continuation per run: it names each
  item, its listing, and the item's own page when a listing read in the run
  links one, and asks for one `search_read` call with those URLs, one search
  for an item without one, or an honest limitation. The check reads no page,
  never rewrites the answer, and runs only while a page read beyond the host
  citation check's reserve is left. After the continuation the answer is
  delivered as it is.

Replaying round 091 with this change (the recorded prompt, search arguments
and result list, both recorded `pixel_ods_web_extract` results and the
recorded answer; page bodies from same-day snapshots through the real reader
and the OpenClaw 2026.6.33 extractor): the first call marks the aggregator
and the guide as listings and prints `DesignPhiladelphia Festival [L6]`; the
recorded answer gets one continuation naming designphiladelphia.org and
easternstate.org for the two guide items; one `urls` call reads both with
receipts, 4 tool calls in all. On `main`'s own answers: round 089's (three
venue detail pages) is accepted unchanged; round 090's gets one continuation
for its Mt. Joy item, which cites an aggregator's tour page, while its film
festival item passes on its stated 403; tower2 round 092's (the month guide
read with `web_fetch`) gets one continuation naming delawareriverfest.org and
the Oktoberfest page for its two guide items. tower1 round 092 on `main`
failed differently: its two detail-page citations were never read and the
host citation check did not match them (the page says "October 18" without a
year, the answer "October 18, 2026"), so the existing unread-source revision
applies there first, as before. `tests/item_sources.test.mjs` covers the
listing and own-link rules, the round 091 replay, the round 092 `web_fetch`
and targeted-extraction paths, one continuation at most, honest
unavailability, and requests without a per-item source.

HTML extraction for every guarded read runs in a short-lived worker thread
(`plugin/html-extraction.mjs`) that loads the SDK's own extractor, verified by
its source text, and is terminated after 5 seconds or when the caller aborts.
OpenClaw's extractor uses lazy element regexes whose cost is quadratic on
markup without closing tags (1 MB of unclosed `<li>`: 10.2 s on tower2), and
it would otherwise block every Pixel session on the gateway thread. Without a
usable worker the extraction runs in-process on the first 256 KB of HTML.
`tests/search_read_replay.test.mjs` replays the event and component journeys.

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
