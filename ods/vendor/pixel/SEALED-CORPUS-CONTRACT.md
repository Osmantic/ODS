# Sealed-corpus custody and disclosure contract

This is the formal custody contract for the sealed matched-harness corpus
(`portal_outcome_sealed_corpus.py` + `portal_outcome_battery_campaign.py`).

## Roots and custody

- A trusted custodian splits a full battery into **two distinct absolute
  roots**: a `tuning_root` (tuning-visible) and a `reveal_root` (owner/guardian
  reveal custody).  Neither root may be nested in the other, and both must be
  owner-private absolute directories whose parents are already owner-private.
- The `tuning_root` holds only `tuning.json` (the tuning corpus) and
  `commitment.json` (the content-free opaque commitment).  It must never
  contain, reference, or enumerate any held-out task byte, id, profile, axis,
  source, per-task size, or filename.
- The `reveal_root` is custodied **outside the tuning worker namespace,
  permissions, and service account**.  Until an authoritative tuning freeze
  validates, the reveal root must remain outside any process that runs tuning
  materialization, the tuning campaign, compatibility review, or freeze.
  Host-side namespace/service-account gates enforce this in deployment; the
  code enforces absolute-root path separation and owner-private parents, and
  tuning APIs and campaign invocations never receive, derive, enumerate, stat,
  or open the reveal path.
- The `reveal_root` holds `manifest.json` (an exact reveal manifest) and opaque
  ordinal task files (`NNNN.task`).  No semantic filenames, ids, profile, axis,
  source, or per-task metadata appear in the tuning-visible commitment.

## Operational bounds

- The v1 aggregate/input bound is exactly 1 GiB
  (`MAX_AGGREGATE_BYTES = 1024 * 1024 * 1024`); per-task payloads remain
  bounded to 96 MiB.  No corpus, aggregate, or single input record may be
  read into memory beyond the 1 GiB cap.

## Content-free commitment

- The commitment binds only opaque aggregates: `tuningTaskCount`,
  `tuningCorpusSha256` (exact canonical `tuning.json` bytes),
  `tuningSourceTaskSetSha256` (canonical tuning source-task hash set),
  `heldOutTaskCount`, `heldOutTaskSetSha256` (over canonical task-payload
  hashes with no ids), `heldOutTotalBytes`, `revealManifestSha256` (exact
  reveal-manifest bytes), plus schema/set identity.  No held-out ids,
  profile, axis, source, per-task size, or filename-derived metadata is
  present.

## Authoritative freeze

- The authoritative freeze is produced by the tuning campaign from its
  validated completed tuning attempts.  There is no free-standing freeze
  authority and a caller cannot supply or invent tuning results.  The freeze
  binds the exact content-free commitment, campaign/candidate identity
  (derived from the campaign identity), the exact tuning materialization
  inventory (`tuningMaterializedTaskSetSha256`), the full canonical
  tuning-evidence set (`tuningEvidenceSetSha256`: ids, task hashes, attempts,
  comparison hashes, status, classification), and the recomputed pair
  comparison evidence.  It freezes only after every committed tuning task has
  a passing, duplicated-id-free, canonically-sorted result.  The freeze's
  top-level candidate/source/architecture/model/inference/pair/preflight/
  runtime/profile/regime/verifier fields must equal the embedded candidate
  binding and the original campaign identity.  Tuning materialization proves
  the exact `tuning.json` corpus bytes and source-task set against the
  commitment before any task is materialized.

## Reveal and held-out

- A post-freeze guardian materializer receives the `reveal_root`, the exact
  authoritative freeze, and the commitment; it verifies the raw manifest and
  opaque task bytes against the commitment, then emits an immutable owner-
  private reveal receipt and a separately materialized held-out root.  The
  campaign never opens raw reveal bytes; it validates the receipt, the
  held-out materialization, the freeze, and the commitment.
- Guardian authorization is **evidence-derived, never self-authenticating**.
  Before any held-out manifest or task byte is opened, the guardian reloads
  and validates the original sealed tuning materialization, the original
  tuning campaign output (`campaign.json`), the exact pair configuration and
  its preflight, recomputes every completed tuning pair through the real
  `_valid_pair` path, rebuilds the authoritative campaign identity and freeze,
  and requires exact canonical equality with the stored freeze at the original
  campaign freeze location.  A forged but schema-valid, internally
  self-consistent freeze with invented tuning evidence is rejected before
  reveal.
- Before `_verify_reveal`, the guardian also compares the exact SHA-256 of the
  supplied `model_payload` and `inference_payload`, the verifier image digest,
  architecture, profile, evaluation regime, and runtime condition against the
  recomputed frozen candidate; any mismatch rejects before any held-out byte is
  opened and leaves no held-out output or receipt.
- The reveal receipt is immutable and binds commitment, freeze, campaign/
  candidate identity, reveal manifest, and held-out materialization.  Any
  mismatch with a preexisting receipt rejects; it is never ignored.

## Fail-closed

- Reveal before freeze, wrong/substituted/extra/missing reveal file, opaque
  filename traversal, symlink/hardlink, wrong owner/mode, oversized file or
  aggregate, duplicate keys, duplicate tuning ids, candidate drift, commitment
  substitution, materialization drift, retuning after freeze, and mismatched
  preexisting receipt all reject.  Any drift requires a new candidate and a
  fresh commitment.

## Operational CLI sequence

A trusted operator runs the sealed lifecycle through the portal operator CLIs in
exactly this order. Each numbered step is a separate command invocation.

1. **split** — `portal_outcome_sealed_corpus.py split --battery <battery> --tuning-root <tuning> --reveal-root <reveal>`
   Creates the distinct owner-private tuning-visible and reveal roots.
2. **tuning-materialize** — `portal_outcome_sealed_corpus.py tuning-materialize --tuning-root <tuning> --model <model> --inference <inference> --verifier-image-digest <digest> --output-root <mat>`
   Materializes tuning tasks only from the tuning-visible root, validates the
   exact tuning corpus/commitment binding, and never touches the reveal root.
3. **tuning campaign** — `portal_outcome_battery_campaign.py --materialization <mat> --pair-config <config> --output <campaign> --partition tuning --sealed-tuning-root <tuning> --runtime-condition ...`
4. **freeze tuning** — `portal_outcome_battery_campaign.py --materialization <mat> --pair-config <config> --output <campaign> --partition tuning --freeze-tuning --sealed-tuning-root <tuning> --runtime-condition ...`
5. **guardian-materialize with original evidence** — `portal_outcome_sealed_corpus.py guardian-materialize --reveal-root <reveal> --tuning-root <tuning> --freeze <campaign>/tuning-baseline-freeze.json --receipt <receipt> --model <model> --inference <inference> --verifier-image-digest <digest> --output-root <heldout> --tuning-materialization-root <mat> --tuning-output-root <campaign> --pair-configuration-path <config> ...`
6. **held-out campaign with receipt/original evidence** — `portal_outcome_battery_campaign.py --materialization <heldout> --pair-config <config> --output <campaign> --partition held-out --sealed-tuning-root <tuning> --sealed-reveal-receipt <receipt> --sealed-tuning-materialization-root <mat> --sealed-tuning-output-root <campaign> --runtime-condition ...`
7. **validate** — `portal_outcome_sealed_corpus.py validate --tuning-root <tuning> --freeze <campaign>/tuning-baseline-freeze.json --receipt <receipt> --heldout-materialization-root <heldout>`

> The final formal held-out corpus is separately custodied and is **not** created
> by these tests. The CLI sequence exercises the synthetic sealed lifecycle only;
> the authoritative held-out corpus remains under separate owner custody.
