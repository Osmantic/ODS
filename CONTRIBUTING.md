# Contributing to ODS

Thanks for wanting to contribute. ODS is open source and we welcome help from everyone — whether you're fixing a bug, adding a service integration, or tackling a full feature.

## Quick Start

1. **Fork** this repository and **clone** your fork locally.
2. Create a **branch** for your work:
   ```bash
   git checkout -b my-change
   ```
3. Make your changes, test them locally, and commit.
4. Open a **pull request** against `main`.

Original Apache-licensed ODS contributions do not require a CLA. Contributions
to separately licensed components must follow the [component terms](#license).

## Forks and Custom Editions

Building on ODS for a hardware appliance, lab image, vertical bundle,
or downstream distribution? Start with
**[ods/docs/FORKABILITY.md](ods/docs/FORKABILITY.md)** and
**[ods/docs/BUILD-ON-ODS-SERVER.md](ods/docs/BUILD-ON-ODS-SERVER.md)**.
They explain the extension points, source-of-truth files, validation commands,
independent operation posture, and rebase-friendly patterns that keep custom
work easy to maintain.

For changes to installer, compose, lifecycle, auth, model routing, or host
mutation surfaces, use
**[ods/docs/HIGH_RISK_CHANGE_MAP.md](ods/docs/HIGH_RISK_CHANGE_MAP.md)**
to choose the right validation before opening a PR.

Every PR should make its changed surface obvious. The pull request template asks
contributors to classify the risk, list the checks they ran, and say whether the
change needs release-grade validation before a release. Docs-only changes do not
need the fleet; operational changes should not rely on "looks small" as the
validation argument.

## AI-Assisted Contributions

AI tools are welcome for drafting, review, test ideas, documentation, and
triage. They do not replace human authorship or maintainer judgment. If AI
helped with a PR, say what it helped with in the pull request template.

Human contributors are responsible for:

- reading the final diff;
- understanding the changed surface;
- removing secrets, local logs, private hostnames, and raw support bundles;
- choosing validation from
  [ods/docs/HIGH_RISK_CHANGE_MAP.md](ods/docs/HIGH_RISK_CHANGE_MAP.md);
- responding to review comments with project context, not tool output alone.

High-risk surfaces such as installer phases, `ods-cli`, Compose generation,
auth, proxy, model routing, host mutation, and GitHub workflows still require
human review and appropriate validation before release.

See
**[ods/docs/AI_WORKFLOW_GUARDRAILS.md](ods/docs/AI_WORKFLOW_GUARDRAILS.md)**
for the repository automation policy and the rules for AI-assisted PRs.

## Opening a Pull Request

- Target `main`. The `public-beta` branch was promoted into `main` on
  2026-09-24 and no longer accepts changes.
- Keep each PR to one focused change and link the issue it fixes. Batch closely
  related fixes into a single PR instead of opening one PR per line.
- Keep at most ten PRs open at a time, and rebase or close your own stale PRs.
  Outside-contributor PRs with no activity for 14 days may be closed; you are
  welcome to reopen or resubmit them rebased on `main`.
- The default branch requires the core CI checks (lint, secret scan, dashboard
  API and frontend, Linux integration smoke, install readiness, release-tree
  portability and runtime security policy) to pass.
- New tests must run in CI: add them to a workflow step or to
  `ods/tests/ci-suite.txt`. A service's own `tests/` directory runs in its
  service's workflow job; for a service with no other job, add it to
  `.github/workflows/test-service-suites.yml`. A check fails when a test under
  `ods/tests` or `ods/extensions/services/*/tests` is not executed by CI and not
  recorded, with a reason, in `ods/tests/ci-not-run.txt`. Only executions count:
  a Makefile target (no workflow runs `make`), a lint or syntax check, or a
  mention in another script does not.
- Report security issues privately through
  [Security → Report a vulnerability](https://github.com/Osmantic/ODS/security/advisories/new),
  not in PRs or issues.

## Full Contributor Guide

For current priorities, validation checklists, PR expectations, and style guidelines, see the detailed guide:

**[ods/CONTRIBUTING.md](ods/CONTRIBUTING.md)**

That's where we document what we need most, what gets merged fast, and what will get bounced back. Read it before your first PR — it'll save you a review cycle.

## Where to Ask Questions

Not sure about something? Open a thread in [GitHub Discussions](https://github.com/Osmantic/ODS/discussions) or an issue. We're happy to help you figure out the right approach before you write code.

## License

ODS is mixed-license. Contributions to original ODS code are offered under the
[Apache License 2.0](LICENSE), except where a file or component has a separate
notice. Pixel source in `ods/vendor/pixel/` has an
[ODS-specific source-available license](ods/vendor/pixel/LICENSE.md); the Apache
contribution statement does not apply to that component or relicense third-party
material. Before accepting a Pixel contribution, maintainers and the contributor
must establish the applicable inbound terms and the contributor's right to offer
the change. Do not assume that contributing Pixel code makes it Apache-licensed.

Retain upstream copyright and license notices. For copied code, artwork, fonts,
or model artifacts, identify the source, version, rights holder and applicable
terms in the PR. See [Licensing](ods/LICENSING.md) and the
[third-party review](ods/docs/THIRD-PARTY-LICENSING.md).
