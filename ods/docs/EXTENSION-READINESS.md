# Extension catalog source and qualification status

Reviewed 2026-09-24 UTC against main promotion
`1bc5e1cbb24864f1c9efd551e0613202af324e3f`.

[PR #6156](https://github.com/Osmantic/ODS/pull/6156) merged on 2026-09-23 at
`1d6038ef9679b89632f5fa4765ad1d6347786711`; its changes are included in the
[September main promotion](PUBLIC_BETA_PROMOTION_2026-09.md). The former
instruction to wait for PR #6155 is not a current merge dependency. PR #6155
remained an open draft when this record was refreshed; its state does not prove
or disprove the current tree's platform behavior.

## Reproducible source evidence

| Area | Evidence at the reviewed source | Boundary |
| --- | --- | --- |
| Generated catalog | `config/extensions-catalog.json`: 200 entries, 200 unique IDs | Presence does not prove deployability or application quality |
| Extension definitions | `python scripts/audit-extensions.py --project-dir . --include-library --strict`: 203 services, zero errors/warnings | Structural source audit; no application images pulled or started |
| Library source | 171 library directories, 137 `upstream.json` records | 34 recipes lack structured provenance; the audit does not clear their terms |
| Pixel source custody | `python scripts/verify-pixel-bundle.py`: pass | Matching visible source and a single-commit installation bundle; not installed acceptance |
| Platform and model behavior | See the promotion record and [Portal regression acceptance](pixel/PORTAL-REGRESSION-ACCEPTANCE.md) | No new physical-machine or live-model acceptance is asserted here |

Catalog requests use existing recipes; source-project preparation and managed
installation have separate evidence boundaries. A proposal, successful import,
prepared recipe, running process and healthy application are different states.
Record which state an acceptance test establishes.

The earlier page included transient workstation deployment claims and test
counts from multiple revisions. Those do not establish acceptance at the
promoted commit. Future receipts must bind product SHA, command, platform,
runtime identity, outcome and retained evidence. Do not publish private session
IDs, personal paths, credentials or conversational content in this public page.

## Remaining acceptance

Exercise actual application use, required configuration, failed install/repair,
cancellation, reconnect, repeated requests, and platform-specific lifecycle
recovery before describing the entire catalog as qualified. Static catalog and
unit-test success do not supply that evidence. Current work must preserve the
runtime testing campaign's selected versions and settings.
