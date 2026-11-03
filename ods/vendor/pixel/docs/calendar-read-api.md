# Calendar read API candidate for Source 4.3.45

`pixel_calendar_list` and `pixel_calendar_count` now require the same `window`:

- `{kind: "civil-days", date: "2026-11-04", days: 1}` resolves the owner's configured timezone, including DST. `start: "today"` or `"tomorrow"` is an alternative to `date`.
- `{kind: "absolute", timeMin: "2026-11-04T03:00:00Z", timeMax: "2026-11-05T03:00:00Z"}` selects explicit instants.
- `{kind: "rolling", days: 1}` selects exactly 24 hours from one server timestamp.

The end is exclusive. All-day event boundaries use the provider calendar's timezone. Both readers share event validation, resolved window and freshness/coverage metadata. List `count` and `recordsReturned` describe the returned page; `completeMatchCount` describes the complete matching projection only when qualified. Missing or ambiguous time, stale data, unresolved events and incomplete coverage leave `completeMatchCount` unknown.

This changes the **native list argument contract** from Source 4.3.44: flat `timeMin`/`timeMax` are refused before projection access. Native clients must update their pinned Source and schema together and use `window.kind: "absolute"` for existing instant-based queries. No stored events or owner settings are migrated. The pure internal `listCalendar` helper retains a strictly validated legacy absolute adapter; that compatibility does not apply to the native tool.

This is a prospective API candidate. It does not change the sealed 4.3.44 release or authorize installation of 4.3.45.
