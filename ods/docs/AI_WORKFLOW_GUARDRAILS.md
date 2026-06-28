# AI Workflow Guardrails

ODS uses AI-assisted GitHub workflows, but they are treated as
automation around the project, not maintainers of record. Human maintainers are
responsible for merges, release decisions, security posture, and validation.

This document explains the safety model so contributors and auditors can review
the agentic CI surface without reading every workflow from scratch.

## Operating Rules

- AI workflows may review, label, summarize, or open PRs only through explicit
  workflow triggers.
- Code-writing jobs must be label-gated, scheduled, or manually dispatched.
  They should not run as implicit write access on arbitrary fork content.
- Generated PRs must be reviewable as normal PRs. They do not bypass branch
  protection, release validation, or human review.
- High-risk paths must be protected from automated patch generation unless a
  maintainer deliberately changes the guardrail policy.
- Secrets, tokens, local run logs, private fleet hostnames, and raw support
  bundles must not be sent to model prompts.
- AI output is never release evidence by itself. Use
  [RELEASE_VALIDATION.md](RELEASE_VALIDATION.md) for release gates.

## Contributor Expectations

AI-assisted PRs are welcome when they are reviewable, tested, and owned by a
human contributor. PR authors should disclose whether AI helped draft code,
docs, tests, or analysis, and should summarize what the human author verified.

Human reviewers should treat AI-assisted changes like any other changes, with
extra attention to:

- whether the diff is smaller than the problem it claims to solve;
- whether high-risk surfaces are called out explicitly;
- whether validation matches the changed surface;
- whether generated prose introduced stale claims or duplicate docs;
- whether secrets, private hostnames, raw fleet logs, or support bundles were
  included in prompts, commits, or PR bodies.

The merge decision belongs to maintainers, not automation.

## Workflow Classes

| Class | Examples | Allowed behavior | Required guardrails |
|-------|----------|------------------|---------------------|
| Advisory | issue triage, PR review, nightly review summaries | Labels, comments, summaries, review suggestions | Fork awareness, no secret exposure, no automatic merge |
| Label-gated writer | issue-to-PR, AI fix jobs | Open or update PR branches after an explicit maintainer label | Protected file checks, secret scan, size limits, human review labels |
| Scheduled maintainer aid | nightly docs/update scanners | Open low-risk maintenance PRs | Budget limits, generated labels, no release authority |
| Manual operator tool | manually dispatched scanners | Run on maintainer request | Same protected-path and secret rules as scheduled jobs |

## Protected Surfaces

Treat these as high-risk for automated edits:

- installer entrypoints, phases, and shared installer libraries;
- `ods-cli` and lifecycle commands;
- Docker Compose base files, hardware overlays, and service manifests;
- authentication, magic-link, OAuth, proxy, network exposure, and secret code;
- GitHub workflows, branch protection, Dependabot config, and release tooling;
- `.env` templates, generated config writers, and support-bundle redaction.

When a new high-risk path is added, update the blocked or protected path checks
in the relevant AI workflows before relying on automated code-writing jobs.

## Maintainer Checklist

Use this checklist when changing AI workflow behavior:

1. Confirm the trigger is explicit for write-capable jobs.
2. Confirm fork PRs cannot cause privileged writes.
3. Confirm protected paths are blocked or reverted.
4. Confirm generated PRs are labeled for human review.
5. Confirm the workflow cannot read or prompt with secrets unnecessarily.
6. Confirm failure modes create issues/comments instead of silently passing.
7. Confirm the change itself is reviewed like any other CI/security change.

## Relationship To Validation

AI workflows help find issues. They do not replace tests, fleet validation, or
release judgment. If an AI-generated PR touches an operational surface listed in
[HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md), it needs the same focused
or release-grade validation as a human-authored PR.
