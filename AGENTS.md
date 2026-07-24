# ODS repository instructions

These instructions apply to the entire repository. `CLAUDE.md` and the documents under `ods/docs/` provide additional architecture and release context; this file is the durable cross-agent policy.

## Source of truth

- Edit this repository, not a deployed copy under `~/ods`.
- Before changing behavior, read `ods/docs/HIGH_RISK_CHANGE_MAP.md` and run the validation lane for the affected surface.
- Preserve unrelated working-tree changes. Never use destructive Git cleanup to make a diff look clean.

## Dependency and automation policy

- Generic tools belong in `mise.toml`; project Python lint policy belongs in `pyproject.toml`.
- Use `mise install` and `mise exec -- <command>` for the pinned developer toolchain.
- Test runners and product scripts must not install tools, clone repositories, or use `curl | sh`. Provision dependencies explicitly through a package lock, a pinned Git submodule, an image digest, or CI setup.
- Do not add Task, just, another task runner, or a wrapper when the existing Make target or direct tool command is sufficient.
- Do not replace first-party installer, host-agent, service, or contract behavior merely to reduce `.sh` or `.py` file counts. A replacement must preserve inputs, outputs, exit codes, supported platforms, and failure behavior.
- Prefer upstream-maintained hooks and actions. Pin releases; pin GitHub Actions by immutable commit SHA.

## Change method

- For behavior changes and refactors, write a focused test first, run it to observe the expected failure, implement the smallest change, then rerun the focused and relevant existing suites.
- Do not auto-fix or format unrelated files. `shfmt`, full-warning ShellCheck, `ty`, and findings outside the scoped zizmor gate are advisory until their repository baselines are explicitly adopted.
- Use `rg`/`rg --files` for discovery. Use language-aware tools for validation: Ruff for Python, ShellCheck plus `bash -n` for shell, actionlint for workflows, and the configured language servers for editor diagnostics.

## Secrets and external tools

- Never commit, print, log, or place secret values in shell arguments, launchd variables, Codex config, fixtures, or documentation.
- Store only 1Password secret references such as `op://Vault/Item/Field`. Inject them into the smallest child process with `op run`.
- Noninteractive automation uses a least-privilege 1Password service account. Do not substitute the 1Password MCP server or shell plugins when a workflow forbids interactive desktop/biometric authorization.
- Exercise MCP/app functions only when they are relevant and within scope. Read-only search and repository inspection do not authorize GitHub writes, messages, deployments, cloud mutations, or CRM changes.
- Prefer primary official documentation for technical claims. Record a source when a version, security property, or integration decision depends on current external behavior.

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

Run from the repository root unless noted:

```bash
mise install
mise exec -- ajv validate --spec=draft2020 \
  -s ods/config/onepassword-scope.schema.json \
  -d ods/config/onepassword-scope.json
mise exec -- pre-commit run --all-files
mise exec -- make -C ods lint
mise exec -- make -C ods lint-workflows
bash ods/tests/run-bats.sh
git diff --check
```

The full Bats suite contains platform-specific tests; report pre-existing platform failures separately from failures introduced by the current change. Release claims require the release-grade lanes documented under `ods/docs/`.

Use the additional lanes documented in `ods/docs/TESTING.md` for affected runtime surfaces. A completion claim must state commands, exits, failures, and any lane that could not run; partial validation is never represented as full.
