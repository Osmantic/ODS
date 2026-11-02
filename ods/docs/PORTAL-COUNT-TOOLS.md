# Portal Gmail and Calendar counts

The development source adds `pixel_gmail_count` and `pixel_calendar_count` to
the existing Source Broker. They use the existing Gmail and Calendar read
permissions; they add no OAuth scopes, account endpoints or separate service.
An installed runtime must receive the matching source bundle through the normal
upgrade process before these tools are available there.

These tools return count metadata without email/event arrays, IDs, subjects,
senders or content. They do not impose a per-turn limit on other permitted read
tools. A model's decision to use them and the accuracy of its final answer still
require behavioral qualification.

## Gmail

`pixel_gmail_count` accepts an optional `query`, using the same sanitized local
projection filters as Gmail search. `indexedMatchCount` describes that local
index. `mailboxTotal` remains null and `canAnswerMailboxTotal` remains false:
even complete coverage of configured queries is not a count of all Gmail.

Inspect `freshness`, `syncState`, `coverageState`, `readState` and `limitations`.
A partial, stale, unverified or unreadable source cannot establish absence.
Malformed records do not silently become zero matches. The output does not
repeat the query or arbitrary coverage/error fields.

## Calendar

`pixel_calendar_count` requires a `window` and accepts an optional `query`:

- `{"kind":"absolute","timeMin":"...","timeMax":"..."}` uses full timestamps
  with explicit UTC offsets and an exclusive end.
- `{"kind":"rolling","days":7}` uses the server's current instant through
  exactly seven elapsed days; it avoids asking the model to calculate dates.
- `{"kind":"civil-days","days":7,"start":"today"}` uses calendar dates in the
  owner's configured `agents.defaults.userTimezone`. `tomorrow` is also supported.
  Civil days can have 23 or 25 hours. Missing, ambiguous or skipped civil
  boundaries are unavailable rather than silently normalized.

Relative windows accept 1–730 days. One server clock sample is used per call.
All-day events use the calendar's global timezone from the existing Google
`events.list` response, independently of the owner's request timezone. Missing
or inconsistent timezone across pages prevents a complete count, including
when resuming an older cursor that did not retain timezone metadata.

`observedMatchCount` remains local snapshot evidence. `completeMatchCount` is
available only with fresh, verified synchronization, complete coverage of the
requested interval, valid calendar timezone and no unresolved event bounds.
Do not substitute zero for null or treat an out-of-coverage window as empty.

## Errors, compatibility and qualification

The existing locked schema validator rejects invalid tool arguments before
reading Source files. Count failures return fixed unavailable metadata without
echoing paths, malformed JSON, provider fields or exception text. An unknown
window kind is null only in an unavailable result. Future or malformed source
timestamps do not become fresh evidence.

The [machine-readable contract](portal-count-tools.contract.json) describes
these boundaries. Existing list/get tools and read/write permission groups are
retained. The subsequent [connection-tool work](PORTAL-CONNECTION-TOOLS.md)
replaces phrase-selected read plans with stable capability guidance and native
tool descriptions. A missing count capability is a limitation, not evidence
that the account is empty. Managed workspace migrations preserve recognized
historical templates and owner edits.

Controlled Source, planner, bootstrap, dispatcher and release-copy checks cover
these components. They do not certify real Google consent/recovery or native
macOS execution.

A separate October 29 run on the exact count-tools source used a private 2B/8K
CPU engine and fictitious projections. Both model legs failed. Chat used an
incorrect Calendar interval, attempted detail reads blocked by the test guard,
and exposed synthetic identifiers/titles in its answer. Gmail count itself
returned metadata only and kept the account total unknown. Native cron exhausted
its discovery guard without completing a Source read. Post-restart scheduling
and retention of that error result have separate receipts; they do not qualify
the model's answer. The run completed naturally, raw evidence was retained and
its disposable resources were removed. The installed account, gateway identity
and model configuration were unchanged. Both this run and the earlier failed
2B/8K run remain negative evidence; count tools do not make that model accepted.

After the ordinary installed 4.3.41 update, one native task with the installed
9B/64K model used both count tools successfully and returned no records. Complete
saved replies qualify the fresh partial Gmail index, unknown mailbox total and
a fresh, covered Calendar count. Calendar resolved seven civil dates including
today in the owner's timezone; this is not a rolling 168-hour-from-now claim.
The answer disclosed the partial index and unknown mailbox total. Normal task
removal retained the same complete result. The existing read-only Google grant
was preserved; no email or event was changed. This is one observed installed
request, not arbitrary-model acceptance or a new browser/UI qualification.

The [October 28 checkpoint](drafts/PORTAL-COUNT-TOOLS-HANDOFF.md) preserves the
earlier design and unapplied partial patch; it is historical material, not the
current implementation or its release acceptance.
