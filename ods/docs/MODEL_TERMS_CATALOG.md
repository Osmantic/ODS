# Model provenance in the catalog

PB-013 implementation checkpoint, 2026-09-23. This is a local evidence record,
not approval of the beta or clearance to redistribute model weights.

The 57 catalog entries now carry publisher/source revision records and a
versioned `terms` object. The backfill is derived from the frozen
[public-source inventory](MODEL_TERMS_AUDIT.md). The migration requires the
original catalog hash and exact inventory coverage; it cannot silently apply
old observations to a changed catalog. Model sizes, context limits, quantization
identities and download checksums were preserved.

Of the 50 formerly mutable `main` references, 48 were pinned to the observed
commit containing the configured file. Seven were already pinned. Two configured
Gemma files were absent from their source revisions; those URLs were not given
a misleading verified identity. Their entries retain an explicit
`ARTIFACT_IDENTITY_REQUIRES_REPAIR` issue. Fixing them requires a review of the
replacement artifact's source, quantization, checksum and terms together.

## What the product now shows

The Models page has an on-demand **Sources and terms** panel, backed by
`GET /api/models/{model_id}/terms`. It shows the publisher, declared base and
recorded ancestors separately, their license declarations and documents,
unassessed commercial-use conditions, upstream access/acceptance observations
and missing-file warnings. Missing records appear as incomplete information.

The endpoint performs no host operation. The panel has cancellation, response
identity checks and a deadline covering both the request and response body.
It accepts only HTTPS links without embedded credentials. The larger source
records are fetched only when opened; they are not added to the regular model
status polling payload.

## Validation and remaining work

`python ods/scripts/check-model-terms.py` validates structured observations;
`--release-ready` additionally rejects incomplete license reviews. The dedicated
model-provenance workflow runs structural regressions and offers the explicit
release qualification check. Its structural success must not be reported as
completed legal review. All 57 entries remain unassessed for release.

The local dashboard suite passed all 1,602 tests in 198 files; the production
build and lint passed. The six new UI tests cover lazy loading, unsafe links,
incorrect response identity, retries, cancellation, missing metadata and a
stalled response body. Python contracts separately cover migration, source
binding, missing files and fail-closed release qualification.

Still outstanding:

- Reconcile conflicting or absent publisher/base terms and record the applicable
  commercial-use and acceptance conditions, including required notices.
- Present applicable terms before downloads through the dashboard, Hugging Face
  import, host-agent and installer paths. The present panel is read-only and
  optional; it is not a consent mechanism or enforcement gate.
- Repair the two missing Gemma artifact identities with verified replacements.
- Carry the required license/notice content with any applicable distribution;
  a source URL and fingerprint are evidence, not a substitute for every notice.
- Qualify the final committed candidate through CI and clean installations.

No model was downloaded, no publisher terms were accepted and no running user
installation was changed as part of these metadata/UI checks.
