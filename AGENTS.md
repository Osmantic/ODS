# ODS repository instructions

These instructions apply to the entire repository. `CLAUDE.md` and documents
under `ods/docs/` provide additional architecture and release context.

## Source and change discipline

- Edit this repository, never a deployed copy under `~/ods`.
- Preserve unrelated worktree changes and use an isolated worktree for scoped
  feature work.
- Read `ods/docs/HIGH_RISK_CHANGE_MAP.md` before behavior changes and run the
  required validation lane.
- Use `rg`/`rg --files` for discovery. Do not auto-format unrelated files.
- Behavior changes require a focused failing test first, the smallest supported
  change, and fresh focused plus regression evidence.

## 1Password and credential governance

- Never commit, print, log, fixture, or place a secret value in source, argv,
  workflow YAML, launchd state, MCP config, examples, or documentation.
- Do not commit `op://` references to this repository. Approved references live
  once in mode-0600 host files under
  `${HOME}/.config/platform/environments/`; repository manifests reference the
  profile path only.
- Noninteractive automation uses the reviewed least-privilege 1Password service
  account. Desktop integration, SSO/manual sessions, shell plugins, interactive
  prompts, and biometric fallback are prohibited.
- Inject one provider profile into the smallest child process using the
  documented `op run --env-file=... -- <command>` form. Never export provider
  credentials globally or combine unrelated providers in one file.
- Do not add an agent-authored credential wrapper, parser, client, daemon,
  bootstrap script, or Python secret utility. Compose pinned upstream commands
  and cite the exact official source.
- Do not install or download tools from a test or runtime script. Pin and
  provision them before validation.
- Every secret-related manifest must set `additionalProperties: false`, have a
  matching JSON Schema, validate before use, and include owners, scope,
  provenance, tests, failure behavior, rotation, and rollback.
- Machine, workspace, project, and namespace policy are distinct. Filesystem
  selection uses the longest root; namespace selection is explicit; policy may
  inherit but secret values and profile allowlists do not.
- GitHub inspection is read-only unless the user separately authorizes a write,
  push, pull request, workflow change, secret change, or deployment.

See `ods/docs/ONEPASSWORD-SERVICE-ACCOUNT.md` and
`ods/config/onepassword-scope.json` for the normative project contract.

## Validation entry points

Run from the repository root:

```sh
mise exec -- ajv validate --spec=draft2020 \
  -s ods/config/onepassword-scope.schema.json \
  -d ods/config/onepassword-scope.json
make -C ods lint
git diff --check
```

Use the additional lanes documented in `ods/docs/TESTING.md` for affected
runtime surfaces. A completion claim must state commands, exits, failures, and
any lane that could not run; partial validation is never represented as full.
