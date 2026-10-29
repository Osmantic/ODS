# Portal count tools: paused implementation checkpoint

**Historical checkpoint from October 28.** Work resumed on October 29. See
[the current count tools](../PORTAL-COUNT-TOOLS.md) for the implementation and
its qualification limits. The artifacts and pending-work statements below
describe the earlier paused state and are retained for provenance.

This checkpoint belongs to draft PR #8293. It preserves unfinished work for
resumption; the count tools below are **not implemented or available**. Do not
apply the attached patch to an installed runtime or treat it as release evidence.

## Completed in the active branch

Commits `fa00b6e78` and `133d22053` select Gmail and Calendar guidance for
current-owner count questions, including Portuguese, and register the new
classifier literals in the existing English-default check. They preserve the
reusable operating cores and current permission boundaries. Guidance does not
enforce how many records a model reads or certify its answer.

The installed acceptance run used internal runtime 4.3.40, source
`5154ac8e97a38cf77f2768c9f6021bd02d9ab574`. The new guidance commits have not
been installed in that runtime. The existing Google read-only connection was
preserved. Connected-app and scheduled-task forms were inspected in Chrome;
the requested idle field borders were absent and the focused name field kept
its focus indicator. No task was submitted during that UI inspection.

## Evidence that remains negative or incomplete

- The isolated 2B/8K run failed the counts-only journey: Gmail summaries were
  returned despite the request, and a Calendar result used the wrong window.
  This is an unresolved limitation, not a passing small-model qualification.
- A local audit error interrupted the native cron analysis. Saved tool output
  is insufficient to claim verified cron history, restart or result retention.
- Isolated test resources were cleaned up. The installed source, grant and
  active model were independently checked afterward without replacing them.
- The official Google app branding is saved in Testing. Domain ownership,
  official client setup, verification and public release remain separate work.
- Real-provider recovery/write journeys, native macOS execution and complete
  installed update/rollback acceptance remain open. Keep this PR in draft.

## Preserved artifacts

- [Proposed Source contract](portal-count-tools.contract.json): closed input and
  output schemas plus freshness, coverage and timezone rules. Its historical
  `private-source-count-contract-v2` label denotes a design draft; it contains
  no account data and is not an implementation.
- [Partial Root/config patch](portal-count-tools-root-prototype.patch): seven
  files based on `fa00b6e78d569d1d313a1d110cffe571f594abbc`, preserved without
  applying them to production files. It adds catalog/config names, explicit
  planner groups and regression tests. Those tests were **not executed**.
- [Sanitized prototype manifest](portal-count-tools-root-prototype.manifest.json):
  file hashes, scope and missing work. Only a selected-file whitespace check
  passed. No composed Source implementation, bundle, version or pin update exists.

The Source implementation was not started: its isolated clone failed before
checkout. That clone's helper processes were stopped. Do not infer working
Source tools from the Root/config patch.

## Resume in this order

1. Implement `pixel_gmail_count` and `pixel_calendar_count` in the canonical
   Source plugin/query engine/broker, under the existing read capabilities and
   OAuth scopes. Return counts and fixed metadata only, never message/event
   records, IDs, subjects, query echoes or arbitrary error text. Unreadable or
   malformed sources must not become a count of zero.
2. Preserve the Calendar `events.list` response's global timezone across every
   page. Missing or inconsistent timezone, including a legacy partial cursor,
   prevents a complete count. All-day events use the calendar timezone; civil
   request windows use `agents.defaults.userTimezone`. Handle DST, ambiguous or
   nonexistent midnight, skipped dates and exclusive end boundaries explicitly.
3. Review and compose the partial Root/config patch with that implementation.
   Preserve existing tool ordering and independent grants. Complete manifest
   parity, named helper/guide tool IDs, workspace templates, migrations and
   historical template-digest compatibility without overwriting user edits.
4. Execute focused and composed tests for schemas, errors, freshness, coverage,
   timezone pagination, permission combinations and existing tools. Then use
   the normal reproducible source-bundle/pin and installer process; do not patch
   a frozen runtime, receipt or hold by hand.
5. Repeat the bounded small-model journey and installed update/rollback checks.
   Record actual content and retained cron receipts as well as execution status.
   Evaluate CI at the eventual PR head before requesting release review.

Coffee's custom MCP servers, Home Assistant, ntfy, Obsidian and per-action
approvals remain his scope. This count proposal does not introduce a competing
integration service or new approval system.

See [the consolidation status](../PORTAL-INTEGRATIONS-STATUS.md) for the wider
implemented scope and acceptance gaps.
