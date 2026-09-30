# Snapshot download verification

This increment depends on #6998 (select-option and blocked-download diagnostics),
#6980 (immutable PDF/ZIP publication), and #6999 (readable capsule image sources
under the installer's private umask). Integrate those changes before installing
this broker, plugin and rebuilt capsule together. The download image capability
is `org.osmantic.ods.inspection.download=snapshot-download-v1`; older images fail
closed. Existing plans retain their original scopes and download cancellation.

An inspection may contain one final `download` step with its normal locator plus
`path`, `expectedBytes` and `expectedSha256`. The path must be an existing
snapshot-relative PDF or ZIP, with 1–4,194,304 bytes. The expected length and
SHA-256 are checked against the independently rehashed snapshot bundle before
Chromium starts. No URL or script is accepted as part of the action.
The `download` step performs its own click: use any visibility/text assertions
followed directly by `download`, without a preceding ordinary click on that
download control. A blocked attempt from an ordinary click is reported as
`unexpected_download`, not a verified delivery or evidence of a broken page.

The actual control is clicked in Chromium inside the unchanged opaque iframe
sandbox. Only during this action may its exact canonical artifact URL navigate.
The browser normally denies downloads. During the capture window it uses CDP
GUID filenames on a dedicated 4 MiB, noexec/nosuid/nodev private tmpfs, so even
callbacks delayed by an adversarial page cannot consume the general browser
temporary directory with download bytes. Page-supplied filenames are ignored.

Success requires an actual trusted click on the observed control, followed by
one browser download event from the inspected frame, completion, and a regular
private file whose byte length and SHA-256 match the snapshot. The click marker
uses a CDP binding restricted to the existing isolated execution context; it is
not exposed in the page's main world. Synthetic dispatches and timer downloads
before the control becomes actionable cannot supply this evidence.
The capture has a five-second deadline, a two-second click timeout and a 250 ms
post-completion observation interval. A second download, wrong URL/frame,
blob/data download, popup or external request cancels verification. No-download
or ordinary navigation is not success. Downloads in ordinary click plans and
the independent palette context remain blocked. Cancellation kills/removes the
capsule through the existing broker lifecycle; nothing is exported or reopened.

GET and HEAD reproduce publication response headers, including length, MIME,
CSP, CORS and X-Preview-SHA256. PDF/ZIP use the publisher's actual
`application/octet-stream` and `Content-Disposition: attachment` policy from
#6980, including links without a download attribute. The inspector does not add
an attachment override that differs from the published page.

A passed download receipt proves only bytes captured inside this capsule after the observed
click during the bounded window. It does not prove causal behavior of arbitrary
page timers, future downloads, delivery to the user's computer, PDF rendering,
ZIP CRC/member correctness, extraction, execution, or overall website quality.
Download bytes and suggested filenames never appear in receipts or logs.
Failed and unavailable receipt scopes describe the verification boundary only;
they do not assert that any download was captured. A blocked snapshot-document
navigation closes only the rejected private page, interrupting a post-click AX
query that could otherwise remain pending on the aborted frame until the broker
deadline. Earlier step evidence and the policy failure remain in the receipt.

Verification: `ods/tests/test_preview_inspection_download.py` includes protocol,
publisher-header parity and opt-in Docker tests. Set `ODS_INSPECTION_TEST_IMAGE`
to the newly built image ID to run real Chromium cases. Plugin receipt and
schema regressions are in `tests/inspection_download.test.mjs`. The existing
private-context image CI job builds and runs both permission and download tests.
