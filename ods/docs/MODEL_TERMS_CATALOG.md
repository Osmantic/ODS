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
`GET /api/models/{model_id}/terms`. The download action also presents those
terms in a required review dialog. It shows the publisher, declared base and
recorded ancestors separately, license documents, recorded commercial-use
conditions, upstream acceptance requirements, unresolved issues and retained
notices. Missing records appear as incomplete information.

The endpoint performs no host operation. The panel has cancellation, response
identity checks and a deadline covering both the request and response body.
It accepts only HTTPS links without embedded credentials. The larger source
records are fetched only when opened; they are not added to the regular model
status polling payload.

Download and retry requests carry `termsAcknowledgement`, containing an explicit
`acknowledged: true` and the current `termsDigest`. The API and the host agent
independently validate it against their canonical record before creating a
download. Missing acknowledgement returns 428, changed terms or artifact
identity return 409, and incomplete metadata returns 412. A separate
`upstreamAccepted: true` is required when recorded publisher acceptance applies.
Canceling or closing the dialog initiates no download. This is an acknowledgement
of displayed information, not a license grant or acceptance on another website.

Hugging Face imports present the selected artifact's pinned publisher metadata
and declared base names before saving/importing it. Such community observations
remain unassessed: a Hub license tag does not complete review of the full chain.
An acknowledged, structurally valid pending record is not automatically changed
to reviewed. Publisher access restrictions still apply.

The [installer review helper](../installers/MODEL_DOWNLOAD_REVIEW.md) applies
the same contract to direct catalog GGUF downloads on Linux, macOS and Windows.
Interactive confirmation defaults to no; unattended operation requires an
explicit receipt for the exact artifact and terms. Background bootstrap retries
revalidate that receipt before contacting the artifact source. Already verified
local files can be reused without a new download. Historical pre-download
snapshots without an exact catalog identity are rejected with a supported-path
explanation. Embedding, speech and image service downloads remain separate work.

## Factual license review

The separate [license review snapshot](MODEL_LICENSE_REVIEWS.json) records
30 exact source chains: 29 with permissive licenses subject to conditions and
one with research/evaluation restrictions. The other 27 remain unassessed.
Reviewed status records the findings; it does not make a restricted model
permissive or establish permission for a particular use. Conditions for each
layer remain visible instead of applying a quantizer's declaration to every
ancestor.

Eleven [retained license and notice files](../config/model-notices/) preserve
the reviewed bytes, including full license text, the Phi component notice and
the complete Google license HTML block plus its extracted text. The review
records the full response fingerprint and the verified extraction; unrelated
website scripts and client configuration are not shipped. Git
attributes preserve their fingerprints across checkout. The original source
inventory and artifact repair observations have not been rewritten.

## Validation and remaining work

`python ods/scripts/check-model-terms.py` validates structured observations;
`--release-ready` additionally rejects incomplete license reviews. The dedicated
model-provenance workflow runs structural regressions and offers the explicit
release qualification check. Its structural success must not be reported as
completed legal review. The release check still fails because 27 reviews are
pending.
The validator accepts multiple observation snapshots and binds each model to
the declared snapshot hash. Supplemental artifact identities must be complete;
changed checksums, sizes or quantizations cannot pass against old evidence.
Repaired models cannot select an obsolete snapshot, and conflicting replacement
snapshots are rejected. Completed reviews additionally bind the source snapshot,
exact artifact identity, ancestry, conditions and retained notices to their own
reviewed evidence hash. Missing files, substituted bytes, paths escaping the
notice directory and modified license-block extraction are rejected.
Twenty-five source/evidence tests passed on Windows Python 3.11 and WSL Python
3.12. The four Gemma pin contracts and actual resolver calls
passed for five Windows and seven POSIX tier selections; the broader Bash tier
suite passed 131 checks and 19 parity comparisons. The macOS resolver was run on
Linux, not on a native macOS host.

The local dashboard suite passed all 1,625 tests in 200 files; the production
build and lint passed. Tests cover lazy loading, unsafe links, incorrect identity,
cancellation, missing metadata, stalled response bodies, unselected confirmations,
and changed terms without an automatic download retry. Python contracts cover
direct host requests as well as API acknowledgement and source evidence.
The installer suite passed 13 cases on Windows (two POSIX-only skips) and 14
on WSL (one Windows-only skip), including real terminal input, EOF/cancellation,
stale receipts, the Windows PowerShell/pwsh wrappers and a background transfer
stub. Existing retry, finalization and cached-recovery fixtures also passed;
no model weights were downloaded. Focused API/download regressions passed again
after tightening the completed-review schema.
The Linux session installer now restarts the host agent after installing new
code, including explicit bind configurations that skip the later network-bind
restart. Seven isolated lifecycle cases pass; a countercheck using the old
start-only behavior fails for an already healthy older agent. No live service
was restarted during these checks.

Still outstanding:

- Reconcile conflicting or absent publisher/base terms and record the applicable
  commercial-use and acceptance conditions, including required notices.
- Complete the terms/download inventory for embedding, speech and image services
  and qualify the installer contract on native machines. The GGUF contract does
  not cover arbitrary external helpers or owner-installed tools.
- Qualify the two replacement Gemma artifacts with full downloads and inference
  before reporting runtime compatibility or performance as verified.
- Carry the required license/notice content with any applicable distribution;
  a source URL and fingerprint are evidence, not a substitute for every notice.
- Qualify the final committed candidate through CI and clean installations.
- Qualify update activation separately: the existing source-checkout
  `ods-update.sh` path uses Git pull and Compose up without rebuilding the API
  or restarting the host agent. That path alone has not been shown to activate
  these new guards; installer reinstall and an explicit rebuild/restart are
  different paths and must not be conflated in release evidence.

No model was downloaded, no publisher terms were accepted and no running user
installation was changed as part of these metadata/UI checks.
