# ODS hand-rolled automation consolidation audit and implementation plan

> Date: 2026-07-17  
> Repository: `Osmantic/ODS` at `e100a6f`  
> Status: implementation complete; final validation evidence is recorded below  
> Principle: replace generic plumbing with maintained tools; retain product logic and the smallest unavoidable platform boundary.

## Executive finding

“No shell or Python files” is not a useful engineering target for ODS. The repository’s installers, host agent, service checks, and tests are product code. Replacing those files merely to change their extension would increase dependencies without removing behavior.

The evidence does support removing or consolidating four kinds of hand-rolled plumbing:

1. dependency installation performed by test runners;
2. duplicated and inconsistent lint configuration across local and CI entry points;
3. unpinned developer tools and unpinned CI package installation;
4. undocumented agent rules and inconsistent Codex app/CLI MCP availability.

The target is therefore **zero bespoke dependency bootstrap and zero duplicated lint policy**, not zero first-party code.

## Scope and safety constraints

- Source-of-truth changes belong in `/Users/daniellynch/Developer/Osmantic/ODS-main`, not the deployed copy at `/Users/daniellynch/ods`.
- The repository was clean and its local `main` SHA matched GitHub before this work.
- No secret values are written to the repository, shell startup files, launchd, MCP config, logs, or test output.
- Read-only research tools may be exercised. Mutation-capable GitHub, messaging, CRM, cloud, and deployment tools are out of scope and must not be invoked just to satisfy a tool count.
- Existing domain scripts are retained unless a standard replacement covers the same inputs, outputs, exit codes, platform matrix, and failure behavior.
- Type checking remains advisory until its existing baseline is triaged; changing `continue-on-error` directly would turn known debt into an unrelated blocking rollout.

## Evidence and capability coverage

The audit used every relevant available research surface, but intentionally did not call every exposed function. The Codex session advertised 1,177 MCP/app functions, most of which mutate unrelated external systems or consume context without adding evidence. Codex’s current customization guidance distinguishes durable repository instructions, hooks, skills, plugins, and MCP servers and recommends selecting the mechanism that matches the task rather than loading everything indiscriminately ([Codex customization overview](https://learn.chatgpt.com/docs/customization/overview.md), [MCP in Codex](https://developers.openai.com/codex/mcp)).

| Surface | Actual use | Result |
|---|---|---|
| Codex local manual / official OpenAI docs | Read current customization, `AGENTS.md`, hooks, skills, plugins, and MCP documentation | Durable policy belongs in `AGENTS.md`; repeatable operational workflows belong in skills; external live data belongs in MCP. |
| GitHub CLI and REST API | Verified remote, default branch, HEAD SHA, workflow inventory, and workflow-run state | Local source is current; GitHub access is read-only for this identity. The supported CLI escape hatch is `gh api` ([GitHub CLI `api`](https://cli.github.com/manual/gh_api), [GitHub REST API guide](https://docs.github.com/en/rest/using-the-rest-api/getting-started-with-the-rest-api?tool=cli)). |
| GitHub connector | Verified authenticated profile, repository metadata, and latest commits | Connector and CLI agree on repository identity and SHA. |
| Exa MCP | Searched official repositories in the app and executed the hosted OAuth MCP through `codex exec` | The CLI query succeeded and returned the official Codex MCP reference ([Exa MCP server](https://github.com/exa-labs/exa-mcp-server)). |
| Tavily MCP | Ran app search/research and executed the hosted OAuth MCP through `codex exec` | The constrained CLI query succeeded. The earlier broad research response included secondary sources despite an official-source constraint, so its claims were discarded ([Tavily MCP](https://github.com/tavily-ai/tavily-mcp)). |
| Brave Search | Installed the official package at version 2.0.82 and executed its stdio MCP through `op run` and `codex exec` | The query succeeded and returned the official Codex MCP reference. Codex required its documented MCP-specific tool approval override in addition to CLI command approval ([Brave Search MCP server](https://github.com/brave/brave-search-mcp-server), [Codex MCP tool policy](https://developers.openai.com/codex/mcp)). |
| 1Password CLI and metadata-only item inspection | Verified CLI version, service-account vault access, and required field labels without reading values | Noninteractive MCP/search processes can receive references through `op run`; secret values must remain subprocess-scoped. |
| Systematic-debugging, writing-plans, TDD, verification skills | Applied to inventory, causality, plan, red/green behavior, and final evidence | Configuration changes must be validated by executable contract checks and fresh final commands. |

## Repository decomposition

Tracked first-party inventory at the audited SHA:

| Area | Shell/Python files | Lines | Interpretation |
|---|---:|---:|---|
| `.github` | 5 | 1,100 | CI helper logic; strong consolidation candidate. |
| dashboard API | 72 | 38,322 | Application and tests; not generic plumbing. |
| extension library | 17 | 1,206 | Product integration code. |
| `ods/bin` | 2 | 5,279 | Core CLI/host-agent behavior; decompose only behind characterization tests. |
| `ods/installers` | 45 | 15,375 | Cross-platform installation product; shell is an intentional runtime dependency. |
| `ods/scripts` | 54 | 19,514 | Mixed domain operations and generic validation; audit individually. |
| `ods/tests` | 160 | 27,231 | Regression assets; wrappers can be reduced, tests should not be deleted wholesale. |
| other ODS services | 32 | 9,686 | Service code. |
| other | 24 | 6,096 | Root bootstrap and miscellaneous code. |
| **Total** | **411** | **123,809** | 274 shell and 137 Python files. |

An exact-content hash scan found no meaningful duplicate implementation files. The only duplicates were empty `__init__.py` markers. Broad deduplication or deletion is therefore unsupported by evidence.

## Implemented consolidation

The implementation changes generic plumbing while leaving first-party installer, service, and contract behavior intact:

- `AGENTS.md` now records the repository-wide dependency, secret, MCP, and validation policy.
- `mise.toml` pins every directly invoked developer CLI; hook-owned tools such as Gitleaks remain pinned by their upstream pre-commit revision. `pyproject.toml` is the single Ruff policy; `ods/Makefile`, pre-commit, and the lint workflows delegate to those policies.
- Pre-commit now uses the official ShellCheck hook instead of the Python repackaging. Workflow validation is provided by native actionlint and a scoped zizmor security gate.
- `ods/tests/run-bats.sh` no longer clones or installs anything. Bats core/support/assert are immutable submodules, CI initializes them explicitly, and the missing-submodule path returns an actionable command.
- The workflow security pass removed broad default permissions and expression injection in `claude-review.yml` and replaced five floating container tags in `matrix-smoke.yml` with verified immutable registry digests.
- Exa and Tavily are configured as OAuth MCP servers. Brave is a pinned local stdio server whose only configured secret material is an `op://` reference; `op run` resolves it in the child process.
- The broken local Headroom route used by `codex exec` is now a native persistent user service. The proxy readiness check and Codex route pass; unrelated doctor warnings are not represented as green.

No new task runner, wrapper layer, application SDK, or custom Codex plugin was added. Those would duplicate maintained capabilities without removing a proven first-party contract.

### High-value candidates

| Current behavior | Gap | Standard replacement | Decision |
|---|---|---|---|
| `ods/tests/run-bats.sh` clones three repositories during every uninitialized test run | Test execution mutates the worktree, needs network, and owns dependency resolution | Pinned Git submodules, the setup recommended by the official Bats tutorial; runner only executes the pinned Bats binary ([Bats tutorial](https://bats-core.readthedocs.io/en/stable/tutorial.html)) | **Replace now**, with a regression test proving the runner contains no installer/network command. |
| CI installs distro ShellCheck while pre-commit embeds an older `shellcheck-py` | Different versions and policies can disagree | Pin ShellCheck 0.11.0; use the official ShellCheck pre-commit repository and the repository `.shellcheckrc` ([ShellCheck](https://github.com/koalaman/shellcheck), [ShellCheck pre-commit](https://github.com/koalaman/shellcheck-precommit)) | **Consolidate now**. Preserve the narrower error gate until warnings are triaged. |
| Ruff flags are inline in CI; several workflows run unpinned `pip install ruff` | Version and policy drift | Pin Ruff and put policy in `pyproject.toml`; invoke `ruff check ods/` everywhere ([Ruff configuration](https://docs.astral.sh/ruff/configuration/)) | **Consolidate now** for the primary lint workflow and local hook; inventory remaining automation workflows separately because they mutate generated PRs. |
| Makefile “lint” uses only `bash -n` and compiles two Python files | Command name overstates coverage and disagrees with CI | Delegate to ShellCheck and Ruff, while retaining `bash -n` for syntax | **Consolidate now** after tools are pinned. |
| Developer tools are installed ad hoc | Contributors can run different versions | Checked-in `mise.toml` for non-runtime developer tools ([mise configuration](https://mise.jdx.dev/configuration.html)) | **Add now**; do not add Task or just because the repository already has a Makefile. |
| Custom AWK heredoc/backtick hook | Potentially bespoke policy logic | ShellCheck SC2006 plus a fixture comparison | **Retain until proven redundant**. ShellCheck’s parser does not necessarily treat generated unquoted heredoc bodies as the project-specific policy expects. Removing it without a failing fixture would weaken a security guard. |
| Python type-check workflow uses unpinned mypy and every check is advisory | It reports but cannot gate regressions | Pin mypy first; establish per-package baselines; optionally pilot `ty` | **Stage later**. `ty` remains beta as of the audit and is not a safe blind replacement ([ty documentation](https://docs.astral.sh/ty/), [ty repository](https://github.com/astral-sh/ty)). |
| Repeated workflow fragments | Installation and validation drift | Reusable GitHub workflows or composite actions ([GitHub reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)) | **Design next**, after actionlint/zizmor baseline. Do not combine workflows solely to reduce file count. |
| Task/command wrappers | Some are only a few lines | Direct commands, Make targets, or reusable workflows | **Replace only when semantics are identical**. A short dispatcher that encodes a supported platform or failure contract is product behavior, not automatically waste. |

### Tool and package decision ledger

| Capability | Adopted tool | Scope and rationale |
|---|---|---|
| Shell syntax and defects | Bash `-n` plus ShellCheck 0.11.0 | Syntax remains explicit; the maintained analyzer replaces duplicated shell heuristics. The existing warning baseline is measured instead of auto-fixed ([ShellCheck](https://github.com/koalaman/shellcheck)). |
| Shell formatting | shfmt 3.13.1 | Used on changed shell/Bats files only; repository-wide formatting remains advisory to avoid an unrelated rewrite ([shfmt](https://github.com/mvdan/sh)). |
| Shell editor diagnostics | bash-language-server 5.6.0 | Pinned for editor/LSP use; it does not replace runtime tests or ShellCheck ([bash-language-server](https://github.com/bash-lsp/bash-language-server)). |
| Python lint | Ruff 0.15.22 | One parser/linter and one root policy replace inline CI flags ([Ruff](https://docs.astral.sh/ruff/)). |
| Python editor diagnostics | basedpyright 1.39.9 | Pinned for LSP/editor use. It does not silently replace the repository’s existing mypy workflow ([basedpyright](https://docs.basedpyright.com/)). |
| Python beta checker | ty 0.0.60 evaluated, not adopted | The executable was evaluated, but the beta checker was removed from committed tooling because no owned baseline or direct invocation justified keeping it ([ty](https://docs.astral.sh/ty/)). |
| Hook orchestration and secrets | pre-commit 4.6.0 plus Gitleaks 8.30.1 hook | Uses upstream revisions; no local hook installer or secret scanner was written ([pre-commit](https://pre-commit.com/), [Gitleaks](https://github.com/gitleaks/gitleaks)). |
| Workflow semantics | actionlint 1.7.12 | Native workflow checks gate at zero. Its 249 existing embedded-shell findings are recorded separately instead of disabled invisibly ([actionlint](https://github.com/rhysd/actionlint)). |
| Workflow security | zizmor 1.27.0 | High-severity, medium-or-higher-confidence offline findings gate at zero; lower-confidence debt remains explicitly outside the gate ([zizmor](https://github.com/zizmorcore/zizmor)). |
| Developer tool provisioning | mise plus the pinned `mise-action` | Direct CLIs resolve from `mise.toml`; CI and local Make targets consume the same versions ([mise](https://mise.jdx.dev/configuration.html), [mise-action](https://github.com/jdx/mise-action)). |

Hadolint, uv, ty, Task, just, a custom Codex plugin, and a 1Password SDK were deliberately not left in the committed toolchain. None replaced an exercised contract in this change, so retaining them would have been inventory rather than consolidation.

## 1Password and global shell differential

### Supported official options

| Option | Noninteractive | Secret lifetime | Fits “no biometric prompt” | Decision |
|---|---:|---|---:|---|
| 1Password CLI service account + `op run` | Yes | Child process | Yes | Primary mechanism. Official CLI docs require `OP_SERVICE_ACCOUNT_TOKEN` and support `op run`, `op read`, and `op inject` ([service accounts with CLI](https://www.1password.dev/service-accounts/use-with-1password-cli), [`op run`](https://www.1password.dev/cli/reference/commands/run)). |
| 1Password SDK | Yes after bootstrap | Application runtime | Yes | Use for native application secret access, not shell/bootstrap replacement. SDKs are version 0 and still need the service-account token bootstrap ([1Password SDKs](https://www.1password.dev/sdks), [environment decision guide](https://www.1password.dev/environments/read-environment-variables)). |
| 1Password MCP server | No for each interaction | Mediated by desktop app | **No** | Reject for this requirement: official docs require explicit desktop authorization for each interaction ([1Password MCP server](https://www.1password.dev/environments/mcp-server)). |
| 1Password shell plugins | Interactive | Shell command | **No** | Reject for this requirement: fingerprint, Apple Watch, or system authentication is the product behavior ([shell plugins](https://www.1password.dev/cli/shell-plugins), [plugin repository](https://github.com/1Password/shell-plugins)). |
| 1Password agent environment hook | Depends on host agent | Agent subprocess | Not applicable | Current official support lists Claude, Cursor, Copilot, and Windsurf, not Codex ([agent environment hook](https://www.1password.dev/environments/agent-hook-validate)). |

The unavoidable boundary is retrieval of `OP_SERVICE_ACCOUNT_TOKEN` from an OS-protected store before `op` can authenticate. The standard 1Password SDK, CLI, MCP, and plugins cannot bootstrap themselves without either that token or an interactive desktop authorization. On macOS, a small Keychain-to-environment bridge is therefore justified. It must remain idempotent, must never print the token, and must inject it only into the current process tree. Publishing the token with `launchctl setenv` is prohibited.

The previous implementation was not adequately disciplined because it edited deployed/home surfaces before confirming the source repository, added approximately 355 lines of shell and a bespoke test harness, and did not first compare the official service-account, MCP, SDK, Environments, and shell-plugin constraints. Its good security property—subprocess-scoped service-account use without biometric prompts—was preserved. The safe minimization removed the unused `--export` mode and both Python one-liners, changed principal verification to the purpose-built `op whoami` command plus system `jq`, and added explicit ShellCheck source directives. Live files and their chezmoi source now match exactly.

## Codex app versus CLI capability gap

| Capability | Codex app session | Local Codex CLI | Target |
|---|---|---|---|
| GitHub | Hosted connector callable | `gh` works through the 1Password-backed wrapper; no GitHub MCP configured | Keep both: connector for app workflows, `gh api` for reproducible CLI reads. Do not duplicate with another local GitHub MCP until a CLI use case needs it. |
| Exa | Callable hosted MCP | Hosted OAuth MCP configured; read-only query passed | Complete. No API key is stored in Codex config. |
| Tavily | Callable hosted MCP | Hosted OAuth MCP configured; read-only query passed | Complete. No API key is stored in Codex config. |
| Brave | Not exposed as a hosted app tool in the audited session | Pinned stdio MCP through `op run`; read-only query passed | Complete for local Codex clients. Restart the desktop client if it was already open so it reloads the shared config. |
| 1Password MCP | Installed binary is present | Not configured | Do not enable because mandatory desktop authorization conflicts with the requirement. |
| Headroom provider | App works independently | Native persistent user service is ready and the Codex route passes | Critical path repaired. Keep Kompress/savings/budget warnings separate from provider readiness. |

Codex MCP configuration is shared by the desktop app, CLI, and IDE extension on the same machine, while hosted app integrations can still differ. The authoritative commands, config formats, allow/deny lists, and per-server/per-tool approval modes come from [MCP in Codex](https://developers.openai.com/codex/mcp).

## Scenario matrix

Every implemented change must be checked against these scenarios:

1. clean clone with submodules initialized;
2. clean clone without submodules initialized (clear actionable failure, no implicit network mutation);
3. local macOS developer with mise activated;
4. GitHub Actions Ubuntu runner;
5. Bash 3.2 product-runtime compatibility where explicitly supported;
6. Zsh interactive and login startup without stdout/stderr noise;
7. Bash interactive, login, and noninteractive (`BASH_ENV`) startup;
8. service-account token present, absent, invalid, and vault access denied;
9. Codex app hosted connector present while CLI MCP is absent;
10. MCP server startup with reference present, missing, and `op` unavailable;
11. offline test execution after dependencies are provisioned;
12. no secret value in process command arguments, repository diff, logs, or diagnostics.

## Implementation sequence

### Phase 1 — durable policy and pinned tooling

Files:

- create `AGENTS.md`;
- create `mise.toml`;
- create `pyproject.toml`;
- update `.pre-commit-config.yaml`;
- update `ods/Makefile`;
- update `.github/workflows/lint-python.yml` and `.github/workflows/lint-shell.yml` only where policy/version drift is removed.

Acceptance:

- all directly invoked CLI versions resolve from `mise.toml`, while hook-owned versions resolve from `.pre-commit-config.yaml`;
- Ruff flags exist once in `pyproject.toml`;
- pre-commit uses upstream-maintained hooks;
- local `make lint` and CI execute the same core policies.

### Phase 2 — remove Bats runtime installer

Files:

- add a failing Bats tooling contract first;
- add pinned `bats-core`, `bats-support`, and `bats-assert` submodules under `ods/tests/bats/`;
- reduce `ods/tests/run-bats.sh` to validation plus execution;
- update checkout/docs so submodules are provisioned explicitly.

Acceptance:

- the regression test fails on the old self-installing runner and passes on the replacement;
- the runner contains no `git clone`, `curl`, `wget`, package installation, or source mutation;
- the pinned suite introduces no failures beyond the measured pre-existing/platform baseline;
- missing submodules produce a concise `git submodule update --init --recursive` instruction.

### Phase 3 — Codex search parity

Machine-level changes (not repository secrets):

- add Exa hosted MCP to Codex CLI;
- attempt Tavily OAuth and retain it only if login validates;
- add the pinned official Brave stdio MCP through an `op run` reference;
- execute one read-only query through each configured server;
- record app-versus-CLI results without exposing keys.

Acceptance:

- `codex mcp list` reports only validated servers;
- queries return results in the intended client;
- no API key value appears in `~/.codex/config.toml` or command arguments;
- failed/unauthenticated entries are removed rather than left as configuration bloat.

### Phase 4 — shell service-account minimization

Completed safe slice in both the live files and chezmoi source:

- characterized the existing nine startup/authentication scenarios before and after the change;
- removed the unreferenced `--export` behavior and all Python use from the loader;
- replaced user lookup/JSON parsing with `op whoami --format=json` and `/usr/bin/jq`;
- retained only the native Keychain bootstrap needed to supply `OP_SERVICE_ACCOUNT_TOKEN` without an interactive prompt;
- verified that launchd contains `BASH_ENV` and the non-secret biometric policy but zero service-token bytes;
- ran Bash, POSIX shell, Zsh syntax checks and the ShellCheck error gate.

The standalone 101-line shell matrix is retained for now because it exercises real Bash/Zsh process modes and disposable macOS Keychains without adding three Bats repositories to the dotfiles source. Moving it to Bats is justified only when that source repository adopts a shared test dependency. Installation/doctor behavior may be split from startup later, but not by rewriting unrelated `MM` chezmoi targets in the same change.

## Validation commands

Run fresh before any completion claim:

```bash
git diff --check
mise install
mise exec -- pre-commit run --all-files
mise exec -- shellcheck --version
mise exec -- ruff check ods/
mise exec -- make -C ods lint-workflows
bash -n ods/tests/run-bats.sh
bash ods/tests/run-bats.sh
mise exec -- make -C ods lint
git status --short
```

Advisory baselines (`shfmt -d`, `ty check`, full-warning ShellCheck) must be reported separately from gating checks so existing debt is not mislabeled as a regression.

## Final validation evidence

The following evidence was collected fresh after implementation:

| Surface | Result |
|---|---|
| Base/remote identity | GitHub REST `main`, local `HEAD`, and `origin` agree on `e100a6f6ad99a44f4ab0c5e35a055fec8fbc3d98` and `Osmantic/ODS`. |
| Pinned tools and LSPs | `mise install` reports all tools installed. Verified actionlint 1.7.12, bash-language-server 5.6.0, basedpyright 1.39.9, pre-commit 4.6.0, Ruff 0.15.22, ShellCheck 0.11.0, shfmt 3.13.1, and zizmor 1.27.0. |
| Pre-commit | All nine configured hooks pass on all files, including Gitleaks, both ShellCheck policies, Ruff, actionlint, zizmor, and the retained heredoc guard. |
| Repository lint | `make -C ods lint` passes Bash syntax, the ShellCheck error gate, and Ruff. The broader ShellCheck run reports 90 warning-or-higher findings as pre-existing advisory debt. |
| Workflow validation | Native actionlint findings: 0. Scoped zizmor findings: 0. Full actionlint still reports 249 findings, all from ShellCheck analysis of embedded workflow scripts; this is an explicit baseline, not a hidden gate. |
| Bats bootstrap contract | 3/3 focused tests pass: no runtime installer, immutable Git submodules, and actionable missing-submodule failure. |
| Full Bats suite on macOS | 382/404 pass; 22 fail. The failure names are identical under Bats core 1.11.1 and 1.13.0 and consist of existing GNU/Linux portability assumptions plus DRAFT user-extension cases. The suite is therefore not represented as green. |
| Submodules and diff hygiene | Bats core 1.13.0, support 0.3.0, and assert 2.2.4 resolve to the audited gitlinks; `git diff --check` passes. |
| Global shell authorization | 9/9 Zsh/Bash/service-account scenarios pass. The doctor returns `SERVICE_ACCOUNT`; syntax and ShellCheck error gates pass; no Python or `--export` path remains. |
| launchd secret boundary | `BASH_ENV` points to the managed bootstrap, `OP_BIOMETRIC_UNLOCK_ENABLED=false`, and `OP_SERVICE_ACCOUNT_TOKEN` contributes zero bytes to the user launchd environment. |
| Codex MCPs | Exa, Tavily, and Brave each completed an actual read-only query through `codex exec`. Brave required the documented per-server tool approval override; the override was invocation-scoped. |
| Codex runtime | `codex doctor --json` passes auth, config, four-server MCP consistency, Headroom HTTP/WebSocket reachability, Git, state DB, and runtime checks. Overall status is warning only because the update probe received HTTP 403. |
| Headroom | `/readyz` is healthy/ready and upstream is healthy; Codex routing and the deployment pass. Claude/shell-wide routing and budget warnings remain explicit; Kompress is unhealthy but does not make the provider readiness check fail. |

No commit, push, pull request, GitHub mutation, deployment, message, CRM action, or cloud mutation was performed.

## Deferred work and explicit non-goals

- Do not rewrite the 4,898-line host agent merely to reduce Python file count; first create module-level characterization tests and an ADR.
- Do not introduce Task or just alongside Make without first deleting/replacing Make; two task runners are more bloat.
- Do not enable 1Password MCP or shell plugins when the governing requirement forbids interactive authorization.
- Do not auto-fix or reformat hundreds of unrelated files during the tooling rollout.
- Do not make advisory mypy checks blocking until their baseline is measured and owned package by package.
- Do not invoke mutation-capable MCP functions against GitHub, cloud providers, Slack, email, CRM, or deployment systems without a task-specific authorization.
