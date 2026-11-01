# Coffee integrations: composition and qualification

Development composition of [#8324](https://github.com/Osmantic/ODS/pull/8324)
with [#8293](https://github.com/Osmantic/ODS/pull/8293). This document does not
mark either PR merged or establish release acceptance.

## Sources and ownership

- Coffee's reviewed starting point is `09a570d9814494d99faf270b59f0af0c0107c97f`.
  Its three commits retain their author and history in the composition merge.
- The follow-up composition also preserves Mike's four commits on that branch
  through `775fc0a0a8a076796bd82a66e2d1e03697a72f2a` and the existing draft's
  recovery changes through `b89d464a5d0a4887eeabc726c6754cec190dee39`.
- The existing Portal foundation starts at
  `8ae5af7b33fd4e86513427e19838149b903e1472`, with current main
  `a1073a2adf8f6e8944172ba7005bd9cbb549465e` merged separately.
- The November 1 source checkpoint additionally merges main through
  `82ed80df72dbb596aac52a1104e9a319ca3bb86a`. Each installed checkpoint below
  remains bound to its own source; earlier evidence is not retroactively
  assigned to this merge.
- The design experiment is a different worktree. Its visual changes are not
  part of this integration branch.

The Google/Calendar and fixed reviewed-app journey stays in Connected apps.
Coffee's custom MCP servers, Home Assistant, ntfy, Obsidian and exact-action
approvals stay in Portal integrations. They share the existing Settings shell,
native OpenClaw execution and native cron; there is no second scheduler.

The union has 32 Portal plugin registrations. It retains the connection-status
tool and all three Coffee tools in the manifest, onboarding, runtime policy,
installer verification and grammar inventory. Taking either old inventory
wholesale would remove tools from the other feature set.

## Acceptance to prove

| Area | Evidence required before calling the composition complete |
| --- | --- |
| Existing connections | Original Google grant and configuration retained; status/read policy unchanged; no unrelated account or owner state replaced. |
| Notes | Real disposable vault search/read/create/append; refusal outside it, through symbolic/hidden paths and across races; concurrent writes and exact approval binding. |
| MCP HTTP / Home Assistant | Discovery, effective per-tool permission, a real tool result, provider error, unavailable server and wrong token; no credential forwarding through redirects. |
| MCP local programs | Real start, call, restart and process-group cleanup; bounded initialization, pipe writes and replies; idle retirement; private environment and diagnostics. |
| Approvals | Off refuses; Ask first waits without executing; exact approved retry executes once; expiry/decline/duplicate controls; target/configuration changes cannot reuse an old approval. |
| Notifications | Real local ntfy receives the intended test/chat/approval message; wrong authentication and unavailable service are honest errors; approvals link grants no authority. |
| Native harness | Actual local model selects the tools from a natural request; errors remain errors through Pi/delivery; cancellation before submission causes no action, and cancellation after submission retains admission until the service settles. |
| Scheduling | Existing native cron retains waiting/success/failure results; an approved exact retry works; notification invocation uses the existing tool/permission path. |
| Settings | Real browser saves/discards, retains drafts through polling and partial failures, reports committed state, shows approvals/activity and works at phone width. |
| Installation | Ordinary local update, service custody and inspected gateway filesystem boundaries, with honest Full Access limitations; installer/uninstaller fixtures and selected runtime bytes; no QA delegation left in active services. |

The original author suites passed locally against the unmodified Coffee head:
service and local-process tests on WSL, Node adapter tests on WSL and API tests
with the existing Windows Python dependencies. They use invented services,
temporary vaults and local sockets. They do not by themselves prove installed
isolation, model behavior, real Home Assistant or browser acceptance.

Additional probes found approval target-binding, note traversal, redirect and
diagnostic defects that were corrected in the composition. Negative evidence is preserved
outside the public source tree. Qualification must use the corrected composed
head, not the earlier baseline totals.

The composition now binds approvals to resource generations and exact filesystem
identities, anchors note writes to opened directories, and refuses credential
forwarding through redirects. MCP startup, pipe writes, HTTP responses and
catalog paging have explicit shared deadlines; an incomplete catalog cannot be
presented as a completed check. Known configured plaintext secrets are masked in
presentation text without rewriting routing IDs or schema constraints.

Settings reconciles an acknowledged save before a later discovery/test failure,
ignores obsolete polling responses, and uses the existing router's navigation
blocker to protect drafts. The native client distinguishes cancellation before
submission from cancellation after an action has started, and reports a lost
response as an unknown outcome rather than automatically repeating it.

These are implementation changes with local regression evidence. Installed
browser/model qualification remains separate from unit and simulated-native
tests; the table above is still the acceptance checklist.

The existing source-bound ToolSearch compatibility recipe also completes an
aborted outer dispatch only after the underlying call settles. Deferred text
errors reach both the native error flag and Pi's `details.ok` contract while
retaining the original tool/result provenance. Normal success is unchanged.
The previous recipe remains a verified migration input; the OpenClaw version,
vendored Portal source and release pins were not advanced for this repair.

Local qualification includes the actual native gateway cancellation lifecycle,
matching hook identities, normal completion and a failing integration reply;
the model endpoint in that regression is deterministic fixture SSE. The real
llama.cpp grammar bridge compiles the combined 32-tool inventory and the native
catalog surface. Neither test establishes live local-model acceptance.

## Installed Windows/WSL qualification

Four ordinary updates, at `8d71df83d`, `674d4bd40`, `d0a722794` and `f16e39be3`, completed on
the owner's Windows/WSL/Docker installation with the actual RTX 5090 and local
Qwen3.5 9B. Independent selected-file inspection matched the installed plugin,
integration service and pinned SDK to the reviewed source. Full Access, the
existing read-only Google choices and opaque credential bytes were preserved.
The post-update inspection found no temporary QA authority references in the
ten selected ODS units and ten independently selected product containers.
This is scoped installed evidence, not a whole-host or isolation attestation.

All new accounts, devices, notes and notification destinations in these tests
were invented QA resources:

- Natural chat searched and read an Obsidian note. An append waited for owner
  approval, executed once after approval, and required a new approval on replay.
  Declining that second request left the note unchanged.
- Real Home Assistant MCP returned the test temperature. A pending lamp action
  survived the second normal update with the integration configuration and
  secrets unchanged; the approved retry then turned on that invented entity.
- The official filesystem MCP started on demand, read a permitted file, and
  refused an outside-root file. Its failure remained a native tool error and
  the model reported the refusal. Restart stopped its exact process group;
  the next check started a new group. Passive observation confirmed natural
  idle retirement without a test signal or timeout change.
- The actual ntfy server received the UI test notification. Invalid ntfy and
  Home Assistant credentials produced explicit errors; the original QA settings
  were restored. After the last update, the unchanged natural chat request
  used the direct notification tool and delivered the exact message once to
  ntfy. A fresh conversation using different wording, without naming ntfy or a
  tool, also delivered its intended test message. This does not qualify delivery
  to a physical phone.
- Real browser save/discard, dirty navigation, partial-save failure and saved
  secret fields were exercised. Desktop and 390px phone views were inspected;
  the phone draft was discarded without changing saved settings.
- A later Google status-only chat used the retained connection without reading
  message or event records.

Two actual model failures remain negative evidence: a notification request
looked only at the custom MCP list and incorrectly inferred that ntfy was
unconfigured; a native cron request wrote the requested note into the ordinary
workspace instead of the configured Obsidian vault. Neither attempted the
corresponding native integration tool. The cron result is retained and its
one-time schedule was archived. It does not prove an approved vault write.

The subsequent stable capability map and explicit MCP catalog scope address
those distinctions without classifying the owner's wording. The existing
native catalog also offers the three integration schemas directly at qualified
32K or larger contexts, retaining policy filtering, ambiguous-name deferral,
canonical discovery IDs and the smaller-context limits. These are the same
implementations and dispatcher, not a second execution path.

On the installed repair, the unchanged notification request and unchanged cron
payload followed their native integration tools directly. The cron call
returned `approval-required`, finished without a file effect and retained its
waiting explanation. After owner approval, another one-time cron job with the
identical payload consumed that exact approval and created the intended note
inside the configured vault. The body matched the requested text with the
service's documented final newline; no copy appeared in the workspace. These
are two distinct one-time jobs, not automatic resumption of the first job.

A further native cron run read that note and sent its visible text through the
notification tool; the independent ntfy sink received it once. This did not
preserve the file's exact bytes: the final LF was absent from the notify
argument, and the notification service also applies `message.strip()`. The raw
byte-equality check remains negative rather than being normalized into a pass.
It qualifies this read/notification seam, not a byte-for-byte file transfer or
Coffee's forthcoming first-class scheduled-notification UI. All four owned
one-time test schedules were archived through normal owner controls, and the
saved result of the last archived task was reopened successfully.

The final update also preserved the three integration state files byte for
byte, the existing read-only Google choice/credential bytes and Full Access.
Independent inspection matched the running installation's selected program,
plugin and native `d20d8071...` SDK bytes/custody to the reviewed source. All
ten selected product containers were healthy and the temporary QA authorities
were absent from the inspected service/container scope. The temporary Edge
unavailable observation during bootstrap did not establish an update failure;
the normal installer completed without intervention. No circular dependency
was proved, and no readiness gate was bypassed.

The earlier failed requests stay negative evidence; they are not counted as
successful acceptance. The later observations establish these scoped 9B flows,
not arbitrary-model reliability or full release acceptance.

## November 1 composition follow-up

The newer Coffee/Mike changes retain their own history. The combined service
keeps destination generations, checked catalog identities, opened-parent and
note identities, secret redaction and shared transport deadlines. Saving settings
or checking tools now revokes pending and approved requests, including unrelated
ones, before later state writes. The earlier tests that preserved approvals
through unrelated saves were updated for this deliberate policy change; an
ordinary software update without a settings save remains a different operation.

Provider calls and approval notifications run outside the admission lock. Local
MCP startup captures the admitted configuration, checks it again before publishing
the process and keeps the existing session deadline while waiting for startup.
Restart or a settings change cannot publish an obsolete startup. Notes search
traverses one opened vault descriptor, while writes retain the reviewed target
and parent identities. The Unix descriptor, redirect, concurrent approval and
actual local-process controls pass on the composed source.

Reviewing Mike's plugin-recovery changes exposed a wire gap: Root and the owner
UI accepted optional `pluginChange` status, while Edge rejected it. Edge now
accepts exactly the same closed projection; legacy absence remains valid and
malformed or credential-bearing fields still refuse. Root/host/Edge parity,
HTTP relay and connection-evidence delivery controls cover this seam.

The WSL source checks also reproduced stale changed-file analysis after rapid
equal-size rewrites. A cached analysis now requires the safely read content
digest and loading kind; each candidate read still charges the existing scan
budget. Exact-read and hard-link refusals remain intact. Deterministic colliding
stat-clock controls and Windows/WSL tests cover the repair. The pre-existing
baseline size/mtime filter is unchanged: this does not claim detection of every
possible rewrite that deliberately restores the baseline metadata.

The subsequent ordinary November 1 update installed `f16e39be3`. Independent
Git/archive expectations matched the five installed integration programs, ten
selected plugin files, rendered unit, native ToolSearch `d20d8071...` and main's
reviewed projection `88c7ec12...`. Both native custody receipts and original
canonical backups matched. Full Access and the original read-only Google record,
credential bytes and observed scope were retained. All ten product containers
were healthy, and the scoped postflight found no temporary QA authority references.
The private update backup retained all ten earlier general snapshots using the
normal backup command's temporary `MAX_BACKUPS=11` setting.

On that installed source, a fresh 9B chat used the status tool without message or
event reads, read the invented note correctly, and appended its exact line once
after owner approval and an explicit "already approved" follow-up. Two other
turns only narrated a need for approval without calling a tool: repeating the
original request after approval and asking for a second identical write. These
are model-behavior negatives, not an approval-service binding failure. No second
write or new pending request occurred. Approval does not automatically restart
the chat; a new owner turn is required. Do not count the no-call turns as
successful retry/replay tests.

A real-model comparison first exercised the request/approve/retry/new-request
sequence with reduced history. Both the published source and a candidate with
four clearer tool/result descriptions passed, so that experiment did not
establish a fix. Repeating the comparison with the exact six-turn invented QA
history reproduced the negative on both versions: zero new tool calls, no new
pending request and no second write. The original transcript remained intact;
the SDK changed only the private execution copy's header working directory.
The four description changes alone do not resolve this counterexample. A
separate per-turn contract then passed the same full-history continuation through
the actual prompt hook: the original source again made no call, while the
candidate submitted the exact write, received a new pending ID and preserved
the used approval and note bytes. This is one bounded comparison on the local
9B model, not a guarantee across models. The contract sits beside each current
owner turn, outside the cached system prompt; it neither routes requests by
keywords nor creates or approves actions itself. Separate fresh-session controls
also passed: an already approved exact request executed once and became used;
an unrelated question made no integration calls or effects. The actual installed
continuation remains a separate gate.
These comparisons loaded the production plugin and native SDK/guards with the
source-read catalog; optional research and project-build capabilities were
unavailable in the private fixture. They do not qualify those services or a Mac.

The October 30 invented QA configurations and credentials were cleared through
the owner API, and only
the four owned test containers, two networks and two volumes were removed. Notes,
private logs and backups were preserved; the four one-time test jobs remain
archived with their results. The cleanup driver's initial missing-object parser
failure was retained; a follow-up confirmed all exact QA resources absent.
The separate November 1 invented notes connection remains enabled while the
approval continuation is investigated; its file and receipts are preserved.

The published `f16e39be3` checkpoint completed its hosted checks without failures
(the advisory tracking job intentionally skipped). Later code needs its own
checks and installed qualification. This remains a
development draft, not a request to merge or a completed release qualification.

## Protocol and platform limits

MCP tool annotations are supplied by the server and are not proof of a tool's
effects. The owner chooses which server and tools to trust; labels must state
that distinction. See the [MCP tool specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).
An external server or owner-selected program receives its configured credential
and can deliberately encode or transmit it. Literal redaction is not a general
data-loss-prevention boundary.

Coffee's current service is Linux/WSL only. Native macOS needs its own service
and owner/agent isolation implementation; a passing simulated or runner contract
does not make that product journey available.

## Next Coffee PR

First-class scheduled-task notification options, Paperless, Immich, page-change
watching and daily-news workflows were announced for a later PR. They are not
implemented or qualified here. A test task invoking the existing notify tool
only proves the current cron/tool seam and does not replace that future work.

Keep the composition in draft until the evidence above has been reviewed.
