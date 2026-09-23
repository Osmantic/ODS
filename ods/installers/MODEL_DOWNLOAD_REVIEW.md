# Installer model download review

Linux phase 11, native macOS launch and Windows launch require explicit review
before a new GGUF transfer. The helper reuses `project_terms` and
`download_review_error` from the dashboard. It identifies one catalog entry by
the complete filename, URL and SHA-256; an optional model ID further restricts
the match. Missing hashes, mutable URL aliases, ambiguous matches, invalid
terms records, unobserved artifacts and split GGUFs are rejected. Split
artifacts remain available through the dashboard model library.

The terminal displays source revisions, publisher declarations, pending issues,
commercial restrictions, conditions, license documents and attribution notices.
A reviewed record can still restrict commercial use. Acknowledgement does not
establish permission, complete legal review or accept terms on an upstream
website. Required upstream acceptance must first be completed with the publisher
and then separately confirmed by the operator.

Interactive confirmation names the model and asks whether to download after
reviewing its sources, terms and unresolved issues, with a `[y/N]` default of
no. Only a complete `y` or `yes` answer (case insensitive) confirms. A separate
`[y/N]` question confirms required upstream acceptance. The displayed digest
is bound to those answers and the catalog is re-read after the prompt. A global
yes flag, tier selection, piped input, empty response or EOF never authorizes a
transfer. Windows `-NonInteractive` and macOS `--non-interactive` require an
explicit acknowledgement file. Cancelling stops that installer invocation; it
does not shut down previously installed services.

## Unattended installation

Inspect the current catalog's exact artifact and terms. For example, from ODS:

```sh
python3 scripts/review-model-download.py \
  --file Qwen3.5-2B-Q4_K_M.gguf \
  --url https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/f6d5376be1edb4d416d56da11e5397a961aca8ae/Qwen3.5-2B-Q4_K_M.gguf \
  --sha256 aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223 \
  --show-json
```

`--show-json` does not authorize a download or write a receipt. After reviewing
the displayed record, prepare JSON with the exact current values:

```json
{
  "schema_version": 1,
  "acknowledgements": [
    {
      "modelId": "exact catalog ID",
      "artifact": {
        "file": "exact filename",
        "url": "exact immutable URL",
        "sha256": "exact artifact SHA-256"
      },
      "termsAcknowledgement": {
        "termsDigest": "current termsDigest from --show-json",
        "acknowledged": true
      }
    }
  ]
}
```

Add `"upstreamAccepted": true` inside `termsAcknowledgement` only after
completing required publisher acceptance. Set `ODS_MODEL_TERMS_ACK_FILE` to
the absolute path before running the installer. Multiple records may cover the
full model and the distinct fast-start bootstrap model; each needs its own
identity and digest. The helper checks this file directly with
`--ack-file <path> --non-interactive` and the same artifact arguments. Unknown
or stale digests fail closed.

The full-model bootstrap review occurs in the foreground and writes
`data/model-download-review.json`. Detached launches and retries validate it
against the installed catalog before contacting the artifact source and before
each transfer attempt. Model or terms changes require a new review. The receipt
is not an upstream access token and does not bypass gated access. A completed
file that passes the existing integrity check is reused without retroactive
acknowledgement or a remote size request.

## Changed legacy behavior and scope

All literal GGUF triples in the three tier maps match catalog entries. Thirty
fallback/bootstrap occurrences were aligned to six existing Qwen identities,
including missing hashes for the 2B and Windows Coder Next fallbacks. Existing
model names and already recorded content hashes were preserved. Custom or
preserved selections outside this catalog stop before a new download and need
selection from the supported model library.

`scripts/pre-download.sh` previously fetched mutable whole snapshots for legacy
tiers `nano`, `edge`, `pro`, `cluster` (Qwen2.5/AWQ), optionally Whisper/Kokoro.
Those snapshots lack corresponding exact artifact/terms records in this GGUF
catalog. Downloads now return an explicit error pointing to the installer or
dashboard; `--list` and local cache verification remain. No unreviewed snapshot
fallback is provided.

This covers installer-managed GGUF foreground/bootstrap transfers. It does not
claim review of service-owned embedding, speech, image or other model downloads,
arbitrary direct calls to low-level download helpers, or external
Ollama/LM Studio/Lemonade installations. Those need separate provenance and
review contracts. A service's software license never establishes weight rights.

Tests use temporary catalogs, synthetic receipts, pseudo-terminals and stubbed
transfers; no production installation, weights or inference. The helper uses
standard-library Python 3.8 syntax. Runtime validation covers Windows Python
3.11 and Linux Python 3.12, not native macOS/Python 3.8.
