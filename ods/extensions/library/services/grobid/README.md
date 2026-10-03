# GROBID

Extract citations, authors and structured TEI content from scientific PDF papers.

Install from ODS **Extensions**, configure the declared settings, then enable.

- Upstream: https://github.com/grobidOrg/grobid
- Code license: Apache-2.0
- Host port: `11012` (override with `GROBID_PORT`).
- Readiness: `http://grobid:8070/api/isalive`.
- Image: `grobid/grobid:0.9.0-crf@sha256:24ba90eb1c959f65d812bcdb2cf79c677fa5fd7b95235de616b8bc9fa1317849`.
- Registry manifest and configuration inspected on 2026-09-20. Runtime validation: pending.

CPU CRF edition with bundled models; does not enable the separate GPU deep-learning pipeline. Upload papers in the web console or call the API. Results are returned to the client, not archived by GROBID.

Disabling the extension preserves its named data volumes, when applicable.
