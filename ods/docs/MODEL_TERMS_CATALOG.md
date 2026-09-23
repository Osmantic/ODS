# Model provenance in the catalog

PB-013 implementation checkpoint, 2026-09-23. This is a local evidence record,
not approval of the beta or clearance to redistribute model weights.

The 57 catalog entries now carry publisher/source revision records and a
versioned `terms` object. The backfill is derived from the frozen
[public-source inventory](MODEL_TERMS_AUDIT.md). The migration requires the
original catalog hash and exact inventory coverage; it cannot silently apply
old observations to a changed catalog. The initial migration preserved model
sizes, context limits, quantization identities and download checksums.

Of the 50 formerly mutable `main` references, 48 were pinned to the observed
commit containing the configured file. Seven were already pinned. Two configured
Gemma files were absent from their source revisions. Those two downloads now
have separate [replacement evidence](MODEL_ARTIFACT_REPAIRS.json), preserving
the original audit snapshot and each previous download identity:

| Catalog model | Replacement publisher | Pinned source revision | Bytes |
| --- | --- | --- | --- |
| `gemma4-26b-a4b-q4` | `bartowski/google_gemma-4-26B-A4B-it-GGUF` | `10f3b41bcf8d3047f4e136e7197ffc2dd1654c9d` | 17,035,039,872 |
| `gemma4-31b-q4` | `unsloth/gemma-4-31B-it-GGUF` | `c1ac76e99d5513b141e8adde7288b85c3f9c32ec` | 18,323,733,440 |

Both retain Q4_K_M quantization and their existing catalog IDs and context
limits. Anonymous metadata and Git LFS pointers agree on each artifact's
SHA-256 and size; HTTP HEAD verified availability and content length. The
publisher README and license declarations are recorded. These checks did not
download complete weights or qualify inference, performance or license clearance.
The 12 affected Linux, macOS and Windows fallback definitions use the same
pinned files and checksums. All 57 primary catalog download references are now
pinned; this does not imply every download elsewhere in the product is pinned.

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
The validator accepts multiple observation snapshots and binds each model to
the declared snapshot hash. Supplemental artifact identities must be complete;
changed checksums, sizes or quantizations cannot pass against old evidence.
Repaired models cannot select an obsolete snapshot, and conflicting replacement
snapshots are rejected. Seventeen source/evidence tests passed on Windows Python
3.11 and WSL Python 3.12. The four Gemma pin contracts and actual resolver calls
passed for five Windows and seven POSIX tier selections; the broader Bash tier
suite passed 131 checks and 19 parity comparisons. The macOS resolver was run on
Linux, not on a native macOS host.

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
- Qualify the two replacement Gemma artifacts with full downloads and inference
  before reporting runtime compatibility or performance as verified.
- Carry the required license/notice content with any applicable distribution;
  a source URL and fingerprint are evidence, not a substitute for every notice.
- Qualify the final committed candidate through CI and clean installations.

No model was downloaded, no publisher terms were accepted and no running user
installation was changed as part of these metadata/UI checks.
