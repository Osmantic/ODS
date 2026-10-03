---
title: Pixel documentation contract
doc_type: policy
audience: [contributor, security-reviewer, maintainer]
feature_status: supported
owners: [documentation]
sources_of_truth: [scripts/docs/, tests/docs-inventory.test.mjs, .github/workflows/docs.yml]
last_verified_at: 2026-08-27
---

# Pixel documentation contract

Pixel documentation is part of the product boundary. It must say what source, configuration, and evidence prove without upgrading a Candidate, disabled surface, synthetic result, or historical record into a stronger claim.

## Page metadata

Every maintained Markdown page under `docs/` starts with the restricted YAML front matter used by `scripts/docs/check-metadata.mjs`.

Required fields:

| Field | Purpose |
|---|---|
| `title` | Unique rendered page title |
| `doc_type` | One of `tutorial`, `how-to`, `concept`, `reference`, `runbook`, `policy`, `assurance`, `release-evidence`, `plan`, or `historical` |
| `audience` | One or more of `owner`, `operator`, `contributor`, `security-reviewer`, or `maintainer` |
| `feature_status` | One of `supported`, `candidate`, `development-disabled`, `synthetic-only`, `historical`, `not-applicable`, or `mixed` |
| `owners` | Documentation ownership, not runtime authority |
| `sources_of_truth` | Repository paths that bound the page's claims |
| `last_verified_at` | An ISO date for authored pages or `generated` for generated pages |

Generated pages also declare `generated_by`, and the metadata check requires that generator path to exist.

The front matter intentionally uses single-line scalar and array values. Multiline YAML and aliases are rejected so validation remains dependency-free and deterministic.

## Status and version rules

- [Status](../status.md) is generated from `VERSION`, `RELEASE-MANIFEST.json`, `QUALIFICATION-MATRIX.json`, and `OPENCLAW-COMPATIBILITY.json`.
- Maintained guide prose links to that page instead of copying the current version, OpenClaw pin, compatibility status, qualified date, or evidence commit.
- A page with `feature_status: mixed` must label the status of each described surface where ambiguity would change an operator decision.
- `Supported` is reserved for exact evidence that uses that status. Implemented, test-green, schema-valid, packaged, signed-for-qualification, activated, and live-used are separate facts.
- `development-disabled` means source may exist while runtime authority remains disabled. It must not be softened to "available" or "ready."

## Authored and generated files

Authored guides explain decisions, warnings, procedures, recovery, and evidence boundaries. Generated references enumerate deterministic source surfaces.

Generated files:

- contain a generated-file notice;
- name their source files;
- are produced by `node scripts/docs/generate.mjs --write`;
- fail `node scripts/docs/generate.mjs --check` when stale;
- are reviewed through their generator and source changes, not edited directly.

The inventory baseline in `scripts/docs/inventory-baseline.json` is a review gate, not a second implementation source. A count change must update the source-derived reference and the baseline in the same review.

## Source and custody rules

- Every `sources_of_truth` path must exist in the checkout.
- Examples may contain public placeholders only. Never include credentials, private paths, user content, private policies, provider responses, browser sessions, or live evidence.
- Configuration docs distinguish authored inputs, generated output, private operator files, runtime state, and evidence.
- Release and qualification paths remain fixed until their generators, tests, inbound links, and downstream consumers are migrated together.
- Evaluation harness pins are documented as harness facts, not general provider promises.

## Change workflow

1. Identify the owner journey and the normative source paths.
2. Decide whether the change belongs in an authored guide, a generator, or both.
3. Update source, documentation, and coverage expectations in one reviewable commit where possible.
4. Run `node scripts/docs/generate.mjs --write`.
5. Run `node scripts/docs/check.mjs` and the documentation tests.
6. Review the rendered diff for status inflation, unsafe snippets, copied secrets, path drift, and missing recovery steps.

Do not move a root document merely to reduce a count. First prove the new path is contract-safe and update all consumers.
