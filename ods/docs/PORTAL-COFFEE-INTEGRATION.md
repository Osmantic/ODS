# Coffee integrations: composition and qualification

Development composition of [#8324](https://github.com/Osmantic/ODS/pull/8324)
with [#8293](https://github.com/Osmantic/ODS/pull/8293). This document does not
mark either PR merged or establish release acceptance.

## Sources and ownership

- Coffee's reviewed starting point is `09a570d9814494d99faf270b59f0af0c0107c97f`.
  Its three commits retain their author and history in the composition merge.
- The existing Portal foundation starts at
  `8ae5af7b33fd4e86513427e19838149b903e1472`, with current main
  `a1073a2adf8f6e8944172ba7005bd9cbb549465e` merged separately.
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
