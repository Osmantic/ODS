# AI Workflow Guardrails

Human maintainers are responsible for merges, release decisions, security
posture, and validation in ODS. AI tools can help contributors and maintainers,
but they are never maintainers of record.

## Status: AI automation workflows retired (2026-10-03)

The repository no longer runs AI-driven GitHub workflows. These were removed:
`ai-issue-triage.yml`, `claude-review.yml`, `issue-to-pr.yml`,
`autonomous-code-scanner.yml`, `nightly-code-review.yml`,
`nightly-docs-update.yml` and `release-notes.yml`.

Reasons:

- **They did nothing.** The repository holds no model API secrets, so every run
  either skipped its AI step while reporting success, or failed.
- **A skipped review that shows green misleads reviewers.**
- **One design was unsafe.** `release-notes.yml` would have let untrusted PR
  titles steer an agent that could run `gh release edit` with write access.

`.github/scripts/test_security_workflows.py` fails if a workflow references
`claude-code-action` or `ANTHROPIC_API_KEY`. Reintroducing AI automation is a
deliberate policy change: follow [Reintroducing automation](#reintroducing-automation)
and update that test in the same reviewed PR.

The default branch requires the core CI checks to pass.

## AI-Assisted Contributions

AI-assisted PRs are welcome when they are reviewable, tested, and owned by a
human contributor:

- **One focused change per PR.** Link the issue it fixes, and batch closely
  related fixes into one PR rather than opening one PR per line.
- **Disclose AI help.** Say whether AI helped draft code, docs, tests, or
  analysis, and summarize what you verified yourself.
- **Keep your queue small.** Have at most ten open PRs at a time. Rebase or
  close your own stale PRs.
- **Stale or bulk PRs may be closed without review.** Maintainers may close
  duplicate, conflicting, or stale bulk-generated PRs with a note. You are
  welcome to resubmit a focused, rebased version.

Human reviewers should treat AI-assisted changes like any other changes, with
extra attention to:

- whether the diff is smaller than the problem it claims to solve;
- whether high-risk surfaces are called out explicitly;
- whether validation matches the changed surface;
- whether generated prose introduced stale claims or duplicate docs;
- whether secrets, private hostnames, raw fleet logs, or support bundles were
  included in prompts, commits, or PR bodies.

The merge decision belongs to maintainers, not automation.

## Protected Surfaces

Treat these as high-risk for any automated or AI-assisted edit:

- installer entrypoints, phases, and shared installer libraries;
- `ods-cli` and lifecycle commands;
- Docker Compose base files, hardware overlays, and service manifests;
- authentication, magic-link, OAuth, proxy, network exposure, and secret code;
- GitHub workflows, rulesets, Dependabot config, and release tooling;
- `.env` templates, generated config writers, and support-bundle redaction;
- files that seed user workspaces, prompts, or agent memory.

## Reintroducing Automation

Before adding any AI-driven workflow:

1. Confirm the trigger is explicit for write-capable jobs, and that fork PRs
   cannot cause privileged writes.
2. Have the model produce text or a patch only. A fixed workflow step applies
   it to an allowlisted target, and a model never receives a token that can edit
   releases, tags, or workflows.
3. Block or revert edits to the protected surfaces above.
4. Make missing secrets or skipped AI steps report as skipped or neutral, never
   as a passing review.
5. Keep secrets out of model prompts and subprocess environments.
6. Review the workflow change like any other CI or security change, and update
   `.github/scripts/test_security_workflows.py` in the same PR.

## Relationship To Validation

AI tools help find issues. They do not replace tests, fleet validation, or
release judgment. If an AI-assisted PR touches an operational surface listed in
[HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md), it needs the same focused
or release-grade validation as a human-authored PR.
