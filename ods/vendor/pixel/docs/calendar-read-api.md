# Calendar read API candidate for Source 4.3.46

`pixel_calendar_list` and `pixel_calendar_count` now require the same `window`:

- `{kind: "civil-days", date: "2026-11-04", days: 1}` resolves the owner's configured timezone, including DST. `start: "today"` or `"tomorrow"` is an alternative to `date`.
- `{kind: "absolute", timeMin: "2026-11-04T03:00:00Z", timeMax: "2026-11-05T03:00:00Z"}` selects explicit instants.
- `{kind: "rolling", days: 1}` selects exactly 24 hours from one server timestamp.

The end is exclusive. All-day event boundaries use the provider calendar's timezone. Both readers share event validation, resolved window and freshness/coverage metadata. List `count` and `recordsReturned` describe the returned page; `completeMatchCount` describes the complete matching projection only when qualified. Missing or ambiguous time, stale data, unresolved events and incomplete coverage leave `completeMatchCount` unknown.

When an all-day event has no usable calendar timezone or has incomplete civil bounds, a possible overlap remains readable with `timeWindowUnverified: true`. This is advisory evidence, not confirmed busy time. List `totalMatches`, page `count` and pagination can include these possible matches; `observedMatchCount` counts only events whose overlap is confirmed, and the content-free count reader never returns the advisory records. `unresolvedAllDayEventCount` identifies relevant uncertainty. Valid distant all-day dates do not affect the requested interval. Flags and uncertainty-adjusted coverage are derived without changing the saved projection; source-supplied flags are ignored.

`readState: "ok"` means the projection was readable, not that its answer is complete. Use `windowState`, `completeWithinWindow`, `completeMatchCount`, `freshness`, `syncState` and `limitations` to qualify a time-window answer. A returned list's coverage is downgraded for relevant unresolved events, stale/unknown freshness or unverified sync; an unavailable window keeps `totalMatches` unknown rather than certifying zero.

This changes the **native list argument contract** from Source 4.3.44 and the upstream 4.3.45 all-day fix: flat `timeMin`/`timeMax` are refused before projection access. Native clients must update their pinned Source and schema together and use `window.kind: "absolute"` for existing instant-based queries. No stored events or owner settings are migrated. The pure internal `listCalendar` helper retains a strictly validated legacy absolute adapter; that compatibility does not apply to the native tool.

This combines the two independently packaged 4.3.45 candidates. Their qualification records remain separate; their version number alone cannot establish the API contract. This document does not promote either historical candidate or claim an installed 4.3.46 validation.
