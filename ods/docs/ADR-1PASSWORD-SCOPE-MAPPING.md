# ADR: schema-validated 1Password project and namespace scope

Status: accepted
Date: 2026-07-17

## Context

ODS may be checked out under different absolute paths, while the developer host
also contains broader machine and workspace policy. Embedding provider values
or duplicating `op://` references per script would make ownership ambiguous and
cause project, workspace, and namespace configuration to drift.

The service account currently has one immutable accessible vault. The design
must not invent production access or imply that a namespace grants a different
vault.

## Decision

Commit a declarative ODS scope manifest and draft 2020-12 JSON Schema.

- The host manifest selects machine/workspace/project policy by longest
  absolute root.
- The repository identifies itself with the portable `repository-root`
  selector rather than a developer-specific checkout path.
- An explicit namespace is selected after the project. Initial namespaces are
  `dev` and `test`; both use the same declared Development vault and allow no
  provider profile by default.
- Project-level documentation and repository inspection may request the
  `github` and `firecrawl` profiles. Their paths point to host-owned,
  reference-only files.
- The repository contains no `op://` reference. The host owns each reference
  exactly once.
- A child scope may reduce policy but cannot expand the machine service
  account's immutable access set.

## Alternatives

| Alternative | Decision |
| --- | --- |
| Hard-code one developer's absolute ODS path | Rejected; worktrees and other contributors would drift. |
| Put provider references in every project directory | Rejected; duplicates ownership and expands review surface. |
| Infer namespace from branch or environment | Rejected; untrusted or accidental state could select credentials. |
| Give each namespace a fictional vault | Rejected; the current principal cannot access those vaults. |
| Add a routing program | Rejected; declarative policy and upstream commands are sufficient. |
| Use one combined dotenv file | Rejected; every tool would inherit unrelated provider values. |

## Consequences

- Scope intent is portable, reviewable, and schema-valid.
- Host and repository manifests have different responsibilities without
  duplicating values.
- Namespace selection is explicit and fail-closed.
- The schema is policy evidence, not an operating-system sandbox; same-user
  processes remain inside the service token's vault-level blast radius.
- A future staging or production namespace requires a separate reviewed
  principal/access set, schema update, and validation evidence.

## Validation

```sh
mise exec -- ajv validate --spec=draft2020 \
  -s ods/config/onepassword-scope.schema.json \
  -d ods/config/onepassword-scope.json
```

Any new profile or namespace must fail the old schema first, cite its official
consumer documentation, add deterministic tests, and pass the repository secret
scan before merge.
