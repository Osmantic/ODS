# Portal public-beta qualification

Portal (labelled Pixel on some ODS surfaces) requires qualification at an exact
ODS revision. This page defines acceptance cases; it does not report a running
fleet, current PR state, or release-wide success. Historical personal test
sessions and machine-local rollout records are not public release evidence.

| User journey | Acceptance evidence |
| --- | --- |
| Installation and lifecycle | A fresh installation completes a real conversation; model change, restart, update, and rollback preserve data and report the observed serving identity. |
| Research | Sources remain attributable through compaction; distinguish search excerpts from fetched pages and verify final claims and requested files. |
| Generated applications | Exercise the delivered application's controls in a browser; publishing, source inspection, or screenshot capture alone does not prove interaction. |
| Completion and recovery | Reconnect, Stop, and resume preserve results and do not duplicate execution; warnings do not replace the useful final answer. |
| Files and images | Upload, retrieval, bounded archive handling, document extraction, and image delivery each receive an end-to-end check. |
| Provider and access settings | Verify activation, reset, revocation, fallback, and access transitions on an installed system; fixture serialization is a separate result. |
| Inference sharing | Verify client-local tool execution, remote model identity, revocation, and failure handling on both ends. |

Record OS and hardware class, actual model/backend, configured and observed
limits, reproducible steps, expected and actual results, and failures as well
as passes. Keep source tests, image builds, installed checks, and user
interaction results separate. Publish a redacted receipt tied to the exact
source and artifact digests, without session IDs, personal directories, private
host names, or conversation content.

See [the community test guide](COMMUNITY-HANDOFF.md),
[Portal migration acceptance](../PORTAL-HARNESS-MIGRATION.md), and
[Release validation](../RELEASE_VALIDATION.md). Remaining acceptance is defined
by the selected release's published receipts, not by historical test counts.
