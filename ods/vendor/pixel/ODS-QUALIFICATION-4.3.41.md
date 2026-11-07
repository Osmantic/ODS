# Portal runtime candidate: internal package 4.3.41

Functional source checkpoint: `cc49a94b991b99e254e0b5555e6b21740a62dc09`.
Prepared on 2026-10-29 for the Portal integrations draft. This is an
ODS-maintained internal candidate, not an upstream or public release.

The Source Broker adds metadata-only Gmail and Calendar count tools under the
existing read permissions. They return fixed count/freshness/coverage metadata,
with no message/event records, IDs, subjects, senders or content. Gmail totals
remain scoped to the local projection; the account total remains unavailable.
Calendar distinguishes exact rolling durations and owner-timezone civil dates,
using the global calendar timezone for all-day events. Producer pagination
retains that timezone and conservatively refuses missing or inconsistent
metadata, including resumed legacy cursors.

Invalid schemas are refused before Source reads. Malformed records, duplicate
Calendar IDs, unreadable snapshots, invalid/future clocks, incomplete coverage,
ambiguous midnight and skipped civil dates cannot silently certify zero or
complete coverage. Existing list/get tools, permission groups, schemas and
managed-template customizations are preserved. No new provider scope, endpoint,
dependency, SDK repair, image or trust anchor is introduced.

Source component checks passed with the locked TypeBox 1.3.13 and pinned SDK
2026.6.33. Planner/render/bootstrap/migration and deferred dispatcher controls
passed, including the earlier template digests and owner edits. Independent
review reproduced the final implementation bytes and exercised malformed data,
timezone pagination, DST and count-receipt cases. Release-copy tests imported
the actual staged query modules in all three broker/dependency configurations;
control/probe tests verify the two count IDs without broadening unknown tools.

These are scoped code and fixture results. At this preparation checkpoint the
4.3.41 public source bundle, installed upgrade, actual count-tool model behavior,
real Google recovery and native macOS acceptance remain unqualified. The prior
failed 2B/8K journey is preserved and is not converted into a pass by these tests.
The fixed request's answer, data exposure and persisted cron receipts still need
a separately recorded model run. Guidance is not a privacy or per-turn read ACL.

The package version increases because held source upgrades require a strictly
increasing version. Historical compatibility rows remain intact. Keep the
consolidation in draft until its remaining acceptance and maintainer review.
