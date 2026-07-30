# 1Password service-account governance

ODS repository automation uses a host-provisioned, non-biometric 1Password
service account. The repository contains policy and scope metadata only. It
never contains a service-account token, provider value, or `op://` item
reference.

## Contract

- Authentication is `SERVICE_ACCOUNT`, never a personal account, desktop app,
  SSO session, shell plugin, or biometric authorization.
- The approved principal has read-only access to exactly the `Development`
  vault (`dfncpo4o2wrkjf6f3zo6zu35ta`). Service-account vault access is
  immutable; a different access set requires a replacement account.
- GitHub and Firecrawl references live in separate mode-0600 host files under
  `${HOME}/.config/platform/environments/`.
- A provider value exists only in the child tree launched by
  `op run --env-file=... -- <command>`.
- ODS scripts, workflows, examples, and configuration must not duplicate a
  reference or export a provider variable.
- Missing, malformed, revoked, or offline authentication fails closed. ODS does
  not open 1Password or select another authentication mode.

The governing repository manifest is
[`ods/config/onepassword-scope.json`](../config/onepassword-scope.json), validated
by [`onepassword-scope.schema.json`](../config/onepassword-scope.schema.json).

## Machine, workspace, project, and namespace

| Scope | Meaning | Secret behavior |
| --- | --- | --- |
| Machine | User-wide 1Password CLI, shell, MCP, and tool policy | May name approved profiles; contains no provider value |
| Workspace | An organizational checkout family such as `Developer/Osmantic` | Shares ownership and policy, not secret values |
| Project | The ODS repository root, independent of checkout path | May request only the declared GitHub and Firecrawl profiles |
| Namespace | An explicitly selected runtime boundary such as `dev` or `test` | Has no implicit provider profile; production is not invented |

Host filesystem routing selects the longest matching root. Repository routing
then uses `repository-root` and, if supplied, an explicit namespace. Policy may
be inherited for explanation, but allowed profile lists and resolved values are
never inherited implicitly.

## Approved commands

The following commands are direct uses of the official 1Password CLI boundary:

```sh
op run --env-file="${HOME}/.config/platform/environments/github.env" -- \
  gh repo view Osmantic/ODS

op run --env-file="${HOME}/.config/platform/environments/firecrawl.env" -- \
  firecrawl map https://www.1password.dev --json
```

Application code and repository scripts do not need to know about 1Password.
An SDK or HTTP client reads its conventional provider variable only after its
whole process is launched under the corresponding `op run` boundary.

## Bootstrap and rotation

The host owns bootstrap. A service-account token cannot retrieve itself, so
initial provisioning and rotation require an external secure boundary. On the
governed macOS host, the token is entered once through the secure Keychain
prompt and is never passed in command arguments. This is the only intentional
human step.

For rotation:

1. Rotate the token with the same immutable permissions in 1Password.
2. Provision the new Keychain value through the secure local prompt.
3. Start a fresh shell and verify identity, exact vault inventory, GitHub, and
   Firecrawl child boundaries.
4. Expire the prior token only after all checks pass.

Revocation immediately removes access. A current shell may retain an old token
snapshot; restart it. Do not restore service by enabling desktop integration.

## Validation

```sh
mise exec -- ajv validate --spec=draft2020 \
  -s ods/config/onepassword-scope.schema.json \
  -d ods/config/onepassword-scope.json

git diff --check
```

Host release validation additionally proves `SERVICE_ACCOUNT`, the exact
single-vault inventory, mode-0600 reference files, quiet shell startup, MCP
handshakes, provider transport smokes, and no provider values in parent shells.

## Adding an integration

A change must include all of the following:

1. Primary official documentation and a pinned upstream tool version.
2. A new isolated host environment profile containing only a reviewed
   Development-vault secret reference.
3. A schema change and project/namespace allowlist decision.
4. A failing deterministic validation before behavior changes.
5. Focused and regression results, a secret scan, and rollback instructions.

Do not add an agent-authored credential client, parser, daemon, shell trampoline,
or provider-specific secret wrapper. If the official tool cannot consume an
environment variable under `op run`, stop and propose a reviewed architecture
change.

## Primary sources

- [1Password Service Accounts](https://www.1password.dev/service-accounts)
- [Service-account security](https://www.1password.dev/service-accounts/security)
- [Use service accounts with 1Password CLI](https://www.1password.dev/service-accounts/use-with-1password-cli)
- [CLI best practices](https://www.1password.dev/cli/best-practices)
- [Load secrets with `op run`](https://www.1password.dev/cli/secrets-environment-variables)
- [`op run` reference](https://www.1password.dev/cli/reference/commands/run)
- [CLI environment variables](https://www.1password.dev/cli/environment-variables)
- [App-integration security](https://www.1password.dev/cli/app-integration-security)
- [Manage rotation and revocation](https://www.1password.dev/service-accounts/manage-service-accounts)
