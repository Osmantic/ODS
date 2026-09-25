# Portal task interface and goal execution

The empty conversation uses a silver prompt bar. Once a message exists, the
normal compact composer returns. The existing theme controls still determine
the panel, text and diff colors. Motion honors `prefers-reduced-motion` and
controls remain usable by keyboard and in narrow browser windows.

## Using goals

Choose **Goal** in Prompt commands or enter `/goal` followed by the desired
outcome. No agent count is required. Portal saves the goal, public plan, actual
responses and clarification answers. A successful partial turn can start a
new, individually identified continuation in the same worker session.

The durable controller shares the existing agent-team execution lane: only one
managed worker uses the shared model at a time. Closing or reloading the
browser does not submit another run. Stop records the cancellation request
before contacting the runtime. An interrupted controller requires an explicit
stop; transport failures and uncertain effects are never automatically replayed.

Completion requires a terminal transport receipt, a nonempty delivered answer,
an acceptable host verification outcome, and a valid completed public plan.
The plan is **reported by the model**, not independent proof of arbitrary work.
Existing file/publication verification and tool permission checks still apply.
The controller pauses after four turns without completed-step progress or
twelve total work turns. Clarifications pause for the owner, with at most four
answer rounds and 4,000 retained answer characters. Completed work, preferences
and the original objective are carried forward; the UI offers explicit
continuation after a stopped or incomplete goal.

## What the activity and response show

- The compact expandable activity timeline replaces the boxed Live activity UI.
  Its last 24 real tool calls mix public updates, plan steps, searches/sources,
  tool operations and clarification traces in chronological order. Failed,
  blocked and unconfirmed operations remain visible; a recovered attempt does
  not label a completed run as failed. These are execution observations and
  deliberately public reports, never private model reasoning.
- The optional `pixel_ods_activity` tool sends a bounded public progress message.
  The header shows that message while work runs and measured elapsed time.
  It never plays a canned reasoning sequence. Running logs follow new events
  until the owner scrolls upward; completed logs collapse and can be reopened.
- Clicking command/file rows opens bounded details. Successful writes/edits
  can show syntax-highlighted excerpts, addition/deletion counts and Copy.
  Current `edits[]` and older replacement schemas are supported. A write has
  no known previous content, so it does not claim a created file or a full diff.
  Truncated excerpts do not claim complete line counts. Failed edits do not
  display proposed changes as successful effects.
- Display metadata uses schema v3 with closed fields and limits: 160-character
  public labels, 400-character details, three safe HTTP(S) source links, eight
  plan steps and 1,000 characters per change side. Known credential files and
  common secret-bearing lines are omitted from excerpts. This is bounded
  display filtering, not a guarantee that arbitrary authored text is secret-free.
  All transport/UI readers retain compatibility with schemas v1 and v2.
- The context ring uses the last assistant call's input/output/cache token
  measurements and its actual context budget. It never substitutes cumulative
  billing usage or an estimated zero. Without measurements, only the configured
  capacity is shown. Hover, keyboard focus or touch opens details.
- Newly delivered response text fades in without a synthetic typing queue.
  Earlier chunks retain their DOM nodes; code and Markdown remain intact.
  Reduced-motion settings disable the effect. The runtime may
  retain text until its existing verification finishes; CSS does not bypass that.
- Inline source badges preserve HTTP(S) links supplied in the answer. They do
  not invent citations, fetch link previews or claim that a link was verified.
- Published artifact changes continue to use the existing real snapshot diffs.
  Activity excerpts are separate tool observations, not snapshot verification.

## Component provenance

The following UImaxxing registry layouts were adapted to the existing React,
Tailwind and Lucide setup; attribution and source provenance remain in the files:

- https://uimaxx.ing/r/neon-prompt-bar.json
- https://uimaxx.ing/r/code-diff-card.json
- https://uimaxx.ing/r/counter-progress-ring.json

Their supplied notices allow use, modification and shipping in products,
including commercial products, and prohibit republication as a component
library or use as machine-learning training data. No registry installer or new
UI dependency was required. The reasoning/activity, streaming, plan, question
and inline-citation equivalents are Portal implementations; no paid Kobra
component, license token or protected source was used.

The current activity and streaming implementations adapt the visual patterns of:

- https://www.aicss.dev/r/thinking-reasoning.json
- https://beui.dev/components/agents/agent-activity
- https://beui.dev/components/agents/streaming-response

They use Portal components and real runtime events instead of the reference
demos' hardcoded progress or timed text. No additional UI dependency was added.

## Validation and portability

Regression coverage includes durable continuation, idempotency, cancellation,
question/answer recovery, malformed plans, stalled models, owner isolation,
schema compatibility, actual token telemetry and preservation of complete text.
Receipt availability is retried with the same read-only run ID; the model
request is not resubmitted. Frontend tests cover the command menu, inline
questions, history reload, accessible tooltips, citation links and diff behavior.

The controller tests ran on Windows Python and Linux containers; native ingress
and installer checks ran in Linux/WSL. The interface was inspected in a compact
browser viewport. A real local Qwen 3.5 4B run continued over multiple turns,
completed both public plan steps and delivered a checked ten-word welcome
sentence; the ring displayed 8,456 / 65,536 tokens. This is a smoke test, not a
claim that every task or small model will complete correctly. No macOS machine
was available for a native execution test. Product code contains no developer
machine paths or dependency on this workstation's Docker overlay configuration.

A second live run calculated 17 + 25, checked the subtraction, and completed
both plan steps after durable continuation, with 5,768 / 65,536 tokens measured.
Earlier live runs exposed intermittent upstream transport failures. Receipt
reads now have bounded retries; ambiguous work is still never resubmitted.
The edge records only the exception category for diagnosis. The latest run
did not reproduce the failure, which does not establish that every possible
transport failure has been eliminated.

The activity smoke test exposed a preview-intent bug: an ordinary filename
under `portal-.../notes.txt` was interpreted as a website request. The intent
projection now excludes nonvisual file operands, with regression coverage for
Windows and POSIX paths. Explicit website/HTML publication still requires its
existing verification. The local 4B model can still select the wrong tool or
misinterpret an edit; rendering accurate activity does not eliminate model errors.

The latest real-model checks displayed a completed edit with +1/-1, expanded
its before/after excerpt, copied the displayed diff, then read the file and
delivered its contents. A separate web search returned real source links and
a final answer linking to the official React useState documentation. Source
titles remove the runtime's technical evidence envelope for display only;
model-facing evidence remains unchanged. The first unconstrained file test
still exposed hallucinated tool names; the guard stopped that run and kept its
actual failed/completed steps. These checks establish the activity integration,
not universal model reliability.


## Tabbed workspace and published-file review

Publication cards in the conversation now open Preview, Review or the selected
changed file in a compact workspace. Review displays one verified diff with a
searchable folder tree on the right. Opening its source creates a closable,
deduplicated file tab. Verified Markdown is rendered as a document with a source
toggle; relative file links resolve within that same publication.

The preview iframe stays mounted across tab switches and panel collapse, so
reading a file does not reset an interactive preview. The workspace uses the
latest verified publication of the selected project, without a version selector.
Review lists every current manifest file plus explicitly deleted paths, and
opens unchanged files in the verified source viewer. Small panels use a file drawer;
keyboard tab navigation, focus restoration and transparent, theme-aware scrollbars
cover the chat and workspace. No external editor, undo or live repository state is
implied: this view reads the existing verified publication snapshots.

Focused regressions cover opening a workspace before publication, publication
switches during manifest loading, failed-manifest retries, obsolete pending file
requests, exact file selection and draft-only publication requests. Existing
snapshot integrity, clipboard, source and conversation recovery tests are retained.

The code/file-list divider supports pointer dragging, keyboard resizing and
container-size changes. Folder trees and breadcrumbs show the publication's real
relative directory; paths sent to the snapshot API remain relative to that
publication. Existing projects are not relabeled as Playground.

On wallpaper themes, chat publication cards use a transparent blurred surface
that picks up the wallpaper color. The jump-to-latest action and response copy
icon have no box. The known successful publication footer is hidden only when
the same verified publication already has a card. Stored text, model history,
failure messages and useful answer content are preserved. A comparison fetch
failure still leaves the verified web preview accessible.

## Project directories and composer controls

New projects use real `Playground/<descriptive-name>` directories below the
configured agent workspace. The runtime reserves unused names, persists the
conversation binding, and routes file operations, patch headers, exec workdirs
and publication to the same directory. Explicit owner paths and legacy projects
stay where they are. Shell source is never rewritten. A new project must begin
with a file write before running project commands, including when a small model
tries to start with an exec or patch. Native Windows and Linux tests cover name
collisions, continuation, restart, unsafe paths and routing provenance.

A local Qwen 3.5 4B run created `Playground/cafeteria-aurora/index.html`, then
published it. Its actual workspace bytes matched the HTTP preview's verified
entry hash. This verifies creation and publication, not every website interaction.

The composer has a compact model selector in place of the character counter.
It loads the installed catalog on first use and uses the existing model-switch
API and readiness checks. The input length limit and context usage ring remain.
The task list reflects validated goal or public plan events, with no simulated
completion. Streaming text progressively reveals new answers even when all
network deltas arrive in one batch; saved history, reduced-motion preferences,
stops and errors render immediately. Display animation does not delay persistence
or tool execution. These presentations adapt the task-list and streaming-response
references to Portal's existing React components rather than installing a second
UI stack.

## Subagent workspace

Agent icons and conversation links open a closable Subagents tab in the same
workspace as Preview, Review and source files. The list groups the current
conversation's agents by active, completed and attention states. Selecting an
agent shows its recorded conversation, live activity, questions and actual
retry/stop actions; the back button returns to the list. Question drafts survive
tab switches and reopening the panel. A new publication does not displace the
active agent conversation or reload the existing web frame.

Workspace requests are consumed once and scoped to the conversation so closing
the tab or changing chats cannot reopen a stale selection. File requests leave
the agent view before loading, keeping retry controls visible if a manifest
cannot be fetched. Review omits the first-publication explanatory sentence.

Workspace, Review, source files and Subagents share the Settings panel's glass
surface on wallpaper themes. Inner canvases stay transparent; file drawers retain
their own readable surface. Reduced transparency and browsers without backdrop
blur use an opaque fallback. Published iframe content is not filtered.

Answer links and tool sources show site favicons, including inside Subagents.
The browser requests icons lazily from a fixed Google favicon-cache endpoint;
only the public hostname is sent, without article paths, query strings or referrer.
Local addresses and authenticated URLs use a globe. Missing/offline icons also
fall back without removing or changing the link. The CSP permits only the icon
endpoint, not arbitrary remote images. An icon is decoration, not source verification.
