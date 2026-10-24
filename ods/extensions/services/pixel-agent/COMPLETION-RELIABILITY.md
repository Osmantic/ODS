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
that answer with only those links replaced by `[source not verified]`,
Markdown link syntax around them flattened to text, and one fixed source-check
note. Nothing else in the answer changes and no link is added. Fixed notices
such as this marker and note are English for every owner; the model's own
answer is never translated. The outcome stays `failed`, so the harness and
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

A listed card name can also head more than one item (fleet round 114,
Qwen3.5-9B: the owner asked for the Midnight card to start hidden behind a
"Show sold out" button; the model published that hidden card and a full copy
in the section the button revealed, both `h2.event-title`, so the click showed
the card twice while the show/hide inspection of the hidden card passed). This
is reported the same way, with its own fixed text and the heading count, only
when the owner listed cards or headings, the name is the whole text of at
least two headings of one HTML document that all share one tag, class list and
parent element tag, none of them inside a `template` or `noscript` element,
and every other name of the same list is the whole text of exactly one heading
of that document. A second page that repeats a card (a waitlist or detail
page) is not a copy. Text the owner asked to start hidden still counts, since
every copy is published. A script that may render the name keeps the check
silent, and so does any plausible request in the owner's own words to show
content more than once: feature, highlight, repeat, mirror or duplicate it,
show it again, too, as well, also in another place or in both places, or put
the items in a carousel. Route notes the host appends to the message are not
the owner's words. `tests/requested_duplicate_heading.test.mjs` replays that
page and the legitimate repetitions it must not report.

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

## Empty lookups are answers

The run-progress budget charges every non-zero exit as a failure. Searches that
came up empty are exempt (`lookupFoundNothing`): a `grep`, `egrep`, `fgrep`, `rg`,
`git grep` or `which` that completed with exit 1, optionally after one literal
`cd DIR &&`. The search tools exit 1 only when nothing matched, and `which` only
when a name did not resolve. Models also chain searches
(`grep a f | head; echo ===; grep b g`), so a list joined by `;` or `&&` qualifies
when every step is a lookup, an `echo` or `sed -n 'N,Mp'`, optionally piped into
`head`, `tail`, `cat`, `wc`, `sort` or `uniq`, and the last step is a bare
lookup. None of those other steps can exit 1, so exit 1 still means a search
found nothing. OpenClaw merges stdout and stderr, and an error line in the output
(grep's warnings and binary-match notices aside) disqualifies the command. rg 13
prints some errors with no recognisable prefix, so `rg` may only be the last
step, where its own exit status separates an error from an empty search. Options
that run programs or write files (`rg --pre`, `git grep -O`, `sort -o`, `sed -i`)
disqualify the command too. The reasoning assumes the sandbox's GNU tools and
rg 13 under dash.

The receipt must be bound to the exact pending call (run, transport, session and
the executed, wrapped parameters), and the model's own command is read from that
call's record. Such a lookup is recorded as discovery: it is not charged and it
resets nothing, up to `FREE_EMPTY_LOOKUPS_PER_RUN` per run, after which it is an
ordinary failure again. Discovery never resets the round fuse, so a run that only
searches still stops after `roundsWithoutProgress` rounds. Any other program in
the command, `||`, background jobs, redirections, substitutions, escapes outside
quotes, an error in the output and every other exit code stay failures. On the
RTX Spark (Elf of Doom), empty greps counted as failures filled the 12-failure
total and stopped a run with 21 good edits behind it; in the next run four of the
five empty searches were chains like the one above.
`tests/empty_lookup_progress.test.mjs` replays the hook sequence for both the
direct and the Tool Search transport.

## Red node runs are test results

The agent sandbox has `node` without npm, and the verification guide asks the
agent to run `node --check` on each script, `node --test` with explicit test
files, and small harness scripts. A red result is the answer that loop exists
to get: charged as failures, an edit and red-test loop reached the 12-failure
total, and one red `node --check` per file the 4-failure streak, before the fix.
`nodeReportedFailure` exempts a command whose every step is a node run: `node`
with long options and either `--check` (or `-c`) and one script, `--test` and
its files, or a script file and its arguments, optionally behind
`timeout DURATION` and followed by `2>&1`, joined by `;` or `&&` after one
optional literal `cd DIR &&`. It must have completed with exit 1 and printed
something. Inline code (`-e`, `-p`, `--eval`, `--print`), stdin, the REPL,
`--run`, other short options, pipes, `||`, redirections, substitutions and
environment assignments disqualify the command. node not found (127), a timeout
(124, or OpenClaw's own), a signal or abort, a bad option (9), a run with no
output, a shell error, and a missing script, module or file (`MODULE_NOT_FOUND`,
`ERR_MODULE_NOT_FOUND`, `ENOENT`, node --test's `Could not find`) stay failures.

The receipt binding is the one empty lookups use. A red run is recorded as
discovery up to `FREE_NODE_RESULTS_PER_RUN` per run, never resets the round
fuse, and never erases earlier failures. The same failing command three times
without an edit is still refused by the direct exec retry limit, and the
verification guide asks for one measurement before and one after an edit,
because a third identical successful exec is refused too.
`tests/sandbox_node_guard.test.mjs` replays both transports.

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
- **Owner's request:** the owner's message, which also sets the answer's
  language. In a team or goal run it is only the owner's own words from the
  message Portal built around them (`managedTurnOwnerWords` in
  `response-language.mjs`): the request after "Owner's requested outcome:",
  the goal after "/goal" or "Original objective:", and the owner's answers.
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

## Run deadline

OpenClaw stops each attempt at `agents.defaults.timeoutSeconds`, wherever the
attempt is. The ODS installers pin it to 1800 seconds. This section does not
change that policy, which is coupled to `diagnostics.stuckSessionAbortMs`, the
session write-lock hold and the ingress and dashboard-api budgets.

On the RTX Spark (2026-10-16, a 131K local model, after #7728 let long runs
keep their context), one owner message asked for several game fixes. The run
worked for exactly 30 minutes (288 model calls, 6 edits) and was still reading
and editing when it stopped. `ui.js` already called `wolfHitShapes()`,
`bossHitShapes()` and `WOLF_ZOOM`, but the `chars.js` edit that defines them
had failed, so 4 of 6 levels threw `ReferenceError`. The owner saw "LLM request
failed." and OpenClaw's advice to increase `agents.defaults.timeoutSeconds`,
which ODS resets. The model had no warning that the deadline was near.

`plugin/run-deadline.mjs` now handles both sides:

- **The model is warned.** `before_prompt_build` runs once per attempt, just
  after the runtime arms that attempt's timer. For the owner's own Portal turns
  the guard records the configured timeout, when the response began (its first
  attempt) and when this attempt began. When 5 minutes of the response remain
  (at most a sixth of the timeout), and again at 2 minutes (at most a
  fifteenth), the next persisted tool result ends with one fixed
  `[ODS Pixel time limit]` block. It tells the model to finish or undo a partly
  made change across files so the saved project still works, stop calling
  tools, and tell the owner what is done, what is left, and that "continue"
  resumes the rest.
  - The schedule counts from the response, not the attempt. A retry, a
    compaction retry or a revision gets a fresh runtime timer, but the ingress
    stops the whole response at 1920 seconds, whatever attempt it is on. A
    fresh schedule would place a later attempt's notices after that cut.
  - Persisted results reach the live model through the ODS projection
    (`host/openclaw-compaction-budget.json`). The block is short and separate,
    so the per-result size cap keeps it whole.
  - The text holds only durations from config, so its bytes are stable.
  - Each stage is sent once per response. After a compaction, whose summary
    can drop it (`keepRecentTokens` is 8192 on a 131K model), the stage now due
    is sent again with the same bytes. A tool-limit stop, its tool-free answer
    turn, an owner cancel and any other ODS abort get no notice.
- **The owner is told what happened.** No hook reports the runtime's timeout
  kind, so `agent_end` classifies it. All of these must hold:
  - the attempt failed (`success: false`);
  - the attempt, counted from its own start, ran at least the timeout minus
    15 seconds;
  - ODS did not stop it: no owner cancel, tool limit or recursive-delete
    refusal, and no guard abort. The guard marks every run it aborts.
  - its last assistant message is not a completed answer, which OpenClaw
    delivers itself.

  Delivery verification then returns `failed` with the text below, followed by
  the last verified preview and any failure already recorded, such as a failed
  test. When that failure already links a preview, for example because files
  changed after it was published, the preview is linked once, by the failure.

  > **Portal stopped this response after 30 minutes, the most one response can
  > run.** This request needed more time. Files saved before the stop are kept,
  > but the last change may be unfinished, so the project may not work yet.
  > Send "continue" to pick up where it left off. If it keeps happening,
  > [get help on Discord](https://discord.gg/4ntNp9MAwC).

  The sentence about saved files appears only when the response changed
  workspace files (a successful write, edit or exec). A research or chat
  response gets only the limit and the "continue" step.

  The ingress replaces the runtime text with it and reports `pixel_outcome`
  `failed`. Channel delivery (`reply_payload_sending`) uses the same
  verification. This also covers a deadline during a tool call, where OpenClaw
  emits no timeout text at all.

A deadline during compaction does not get this copy. The runtime extends that
attempt by the compaction grace (`agents.defaults.compaction.timeoutSeconds`,
which ODS also pins to 1800 seconds). That always outlasts the ingress's
2-minute margin, so the response still ends with the dashboard's "Portal could
not complete the response".

Subagent, cron and team-worker runs get no notice or copy: subagent and cron
lanes can use another timeout or none, and a team worker reports to its
coordinator rather than the owner.
`tests/run_deadline.test.mjs` and `tests/run_deadline_delivery.test.mjs`
(including the real ingress over loopback) cover these paths.

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
same chat, without UI metadata in the model request: each question, then its
answer.

That text reads correctly only beside the model's own question. OpenClaw can
start it in a new native session that lacks the request and the question (RTX
Spark, 2026-10-16: the model answered its own "Where should I apply…?" with a
location and edited nothing). When a run sets a card (the ask tool's result, or
the requested-choice repair at finalize), the plugin therefore keeps one private
record per chat (`.ods-pending-clarifications` in the OpenClaw state directory,
owner-only, keyed by a hash of the session key): its questions and the owner
request they clarify, capped at 4,000 characters. Portal's delivery contract is
cut at its last marker that starts a line, so a request that quotes the marker
keeps its own words. A request of attached images alone is stored as a fixed
placeholder, because image parts never reach the hook's prompt text. The next
owner-authored turn in that chat claims the record; a subagent announce
continuation, cron, a team worker or another session never does. Only when that
turn is the card's exact answer (the first question's text, then every question
in order) does its run get fixed model-only context (`before_prompt_build`
`appendContext`) with the request: follow an answer that declines or changes it,
otherwise finish the request with the answers. That answer keeps the record until
its run ends with success, so an answer blocked at admission, failed or lost to
a gateway restart is understood when resent; any other owner turn consumes the
record unused. Unanswered records expire after 24 hours, and deleting the
conversation deletes its record with the transcripts. A follow-up card keeps the
original request and up to two earlier answers.

The context is added whether or not OpenClaw kept the native session: a
compaction can summarize the model's own question away as surely as a
replacement session lacks it, and in an unchanged session the note only restates
the transcript. The owner text is unchanged, so recursive-delete authorization,
choice-question intent, Playground project reservation and every route see what
they saw before; an earlier in-text frame quoting the request turned a declined
delete into authorization. Further cases:

- The note stays out of the transcript, which keeps only the owner text. OpenClaw
  trajectory files (`<session>.trajectory.jsonl`) record every model prompt, so
  they hold the note, as they already hold the owner's own messages. Conversation
  deletion does not yet remove trajectory files; that is a separate follow-up.
- If the answering run delegates, each announce continuation of that run (the
  child's report back in the owner session) carries the same note while Portal
  still holds that delegation's custody (no stop, gateway restart or expiry).
  That run's success has already settled the card, and in a replacement session
  only the note names the request.
- Stopping the answer withdraws its note with the rest of that run, and the card
  stays. When the owner resends the same answer, it gets the clarification note
  without the withdrawn-request note, which would call that same text withdrawn.
  A different message after the stop still gets the withdrawn-request note.
- A card asked inside a goal task is answered with a goal continuation
  (`/goal Continue the goal ...`, dashboard `continueGoal`) that carries its own
  original objective. It never has the card's answer shape, so it consumes the
  record without a note.
- The dashboard's clean-context recovery retry runs under a new chat ID, so an
  answer retried that way has no record.

`tests/pending_clarification.test.mjs` replays the field turn after a gateway
restart, drives the real plugin entry and compares each owner-intent outcome
with and without the record; `tests/runtime_clarification_answer.integration.mjs`
sends the answer through the pinned runtime into a replaced native session.

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

## Failed preview checks keep the explanation

On the RTX Spark (Elf of Doom), an interaction check failed and the agent then
wrote the owner a correct root-cause explanation. Portal replaced the whole
reply with its host delivery ("The attempted preview interaction checks have
not passed browser inspection ... Your preview is ready."), so the owner never
saw it. `preview-explanation.mjs` changes only that substitution: when a
workspace preview outcome is `failed` or `pending` (a browser, interaction or
requested-text check that did not pass, a stale snapshot, no publication, a
verification command still running, or any of these with extension, lane-stop
or missing-download facts added), the agent's final reply comes first. A fixed
note follows under **Portal check:**, then the unchanged host result:

- The note always says "The reply above is the agent's own account, not a
  verified result." and then "This request did not pass Portal's checks." (or,
  when pending, "Portal's checks for this request have not finished."). No
  wording heuristic decides this, so a claim of success in any wording, quoted,
  in code or in a checklist, is still followed by that plain statement. The
  reply itself is never rewritten.
- When the reply promises more work ("I'll fix line 748 now") or ends by
  narrating its next action, the note adds that Portal has stopped for this
  message and the step has not started.
- When the current snapshot's latest inspection failed, one host line names
  the failed step from the validated receipt, for example "step 2
  (assert-painted) on `#arena` captured one color only (#8fce3a)". Only the
  step number, action, error code, the capsule's color and the plan's locator
  (quoted as code) are used. This line is not added to a host-only delivery.
- The outcome keeps its status, and every host failure line and the preview
  receipt still follow, so the dashboard still shows the preview card and the
  failure. An open code block in the reply is closed with its own fence, read
  as the dashboard's `publicationDisplayText` reads it.
- The host-only delivery stays as before when the reply is empty, silent, too
  short, tool-call text, over 20,000 characters, links any local URL other than
  this outcome's own preview, or carries Portal's wording: its label or receipt
  lines ("Your preview is ready.", "[Open preview]", "Published from your
  workspace."), however markdown or HTML draws them (character references,
  no-break or zero-width spaces, fullwidth letters, emphasis split across the
  words).
- The reply is the `lastAssistantMessage` seen at `before_agent_finalize`, and
  only when the transcript's last assistant message, a turn without tool calls,
  holds exactly that visible text. When that turn has none, OpenClaw 2026.6.33
  fills `lastAssistantMessage` with the joined narration of earlier tool-call
  turns, which is no reply. Any later model call, and an owner cancel, clear it.
- Passing (`append`) outcomes are unchanged, and so is the ingress verification
  schema. A run-deadline, tool-limit, recursive-delete or clarification
  delivery keeps its own text.
- The reply is filtered like a tool-limit answer, not scrubbed: a path or other
  text the agent quotes is delivered as in any passing reply.

`tests/preview_explanation_delivery.test.mjs` replays the Elf of Doom case
through the real guard, inspection receipt and ingress, plus the regression
cases (success claims in many forms, empty, silent and narration-only replies,
imitated notes, passing and pending checks, local links, code fences).

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
