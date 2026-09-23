# Extension readiness criteria

This page defines how to assess a selected ODS revision. It is not a live
deployment report, a merge-order instruction, or evidence that every catalog
application works. Historical workstation test totals and PR dependency state
have been removed because they did not establish readiness of a release SHA.

## Source checks

| Surface | Evidence needed for the selected revision |
| --- | --- |
| Catalog | Unique service IDs; catalog generation matches manifests; every source definition has an explicit inclusion or exclusion reason. |
| Recipes | Staging and validation pass; immutable upstream identity, build context, source provenance, and required configuration are recorded. |
| Catalog chat requests | Request ownership, prerequisites, write-only credential input, cancellation, and duplicate prevention pass focused tests. |
| GitHub projects | Repository, revision, recipe digest, and prepared package remain bound through promotion, retries, and repair. |
| Project association | A successful observed install is required before continuation; saved history alone cannot submit a mutation. |
| Credentials | Public responses exclude secret values; projection refresh does not replace unrelated configuration. |
| Platform transports | Linux/WSL and native macOS paths receive separate checks; Windows-native support is not inferred from POSIX results. |

Use [Portal harness acceptance](PORTAL-HARNESS-MIGRATION.md) for lifecycle cases
and [Extensions](EXTENSIONS.md) for integration contracts. The
[catalog](../extensions/CATALOG.md) describes available definitions; availability
is distinct from verified application execution.

## Runtime acceptance

For each claimed platform/application combination, record fresh installation,
required configuration, an actual application operation, cancellation/recovery,
restart, update, and rollback. Verify the serving model and backend where the
application depends on inference. Schema validation, an image build, a health
endpoint, and mocked host tests do not establish these outcomes.

Attach a sanitized receipt to the release: exact ODS SHA, upstream/image/model
identities, hardware class and OS, commands, pass/fail/skip outcomes, artifact
digests, and known limits. Raw workstation logs and private run-store links are
not public evidence. Follow [Release validation](RELEASE_VALIDATION.md) and
[the validation matrix](VALIDATION-MATRIX.md); only current published results
can establish which gates passed.
