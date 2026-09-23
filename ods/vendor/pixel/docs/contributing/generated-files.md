---
title: Pixel generated and private files
doc_type: reference
audience: [contributor, maintainer]
feature_status: supported
owners: [documentation, maintainers, release]
sources_of_truth: [CONTRIBUTING.md, .gitignore, scripts/generate-release-files.mjs, scripts/docs/, scripts/configure.mjs]
last_verified_at: 2026-08-27
---

# Pixel generated and private files

Know which layer owns a value before editing it.

| Layer | Examples | Edit policy |
|---|---|---|
| Authored repository input | release manifest, compatibility JSON, schemas, examples, profiles, source | Edit under review; regenerate consumers |
| Generated repository file | release constants/env/tables, docs status/CLI/schema/config references | Never hand-edit; change generator or source, then regenerate |
| Private operator input | onboarding JSON, `.env`, broker policies, signer files, credentials | Keep outside Git with owner-only custody |
| Generated deployment output | `.generated/`, rendered config, units, plans | Recreate from authored/private input; do not treat as source |
| Runtime state | `.runtime/`, OpenClaw state, broker spools, projections, claims, receipts, logs | Mutate only through owning transaction/recovery commands |
| Evidence | attestations, qualification receipts, live audit summaries | Bind exact source/environment; include only approved sanitized records in Git |

## Release generation

Edit release pins only in `RELEASE-MANIFEST.json` and compatibility state only in `OPENCLAW-COMPATIBILITY.json`, then run:

```bash
node scripts/generate-release-files.mjs --write
node scripts/generate-release-files.mjs --check
```

Review every generated diff. Pin changes are release-contract changes and require their full gates.

## Documentation generation

```bash
node scripts/docs/generate.mjs --write
node scripts/docs/check.mjs
```

Generated documentation carries a notice and generator metadata. Review the generator and source diff rather than patching the rendered page.

## Configuration generation

`./pixel configure` turns private onboarding and repository contracts into ignored `.env` and `.generated` output. `./pixel plan` validates and summarizes that output. Never document a generated value as an authored input or copy private generated files into an issue.

## Never commit

Private onboarding, `.env`, `.generated`, `.runtime`, `.secrets`, OpenClaw state, broker state, credentials, approvals, messages, memory, session files, host identities, private policies, and live transcripts stay outside Git.
