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

No CLA, no hoops.

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
for the repository automation policy.

## Full Contributor Guide

For current priorities, validation checklists, PR expectations, and style guidelines, see the detailed guide:

**[ods/CONTRIBUTING.md](ods/CONTRIBUTING.md)**

That's where we document what we need most, what gets merged fast, and what will get bounced back. Read it before your first PR — it'll save you a review cycle.

## Where to Ask Questions

Not sure about something? Open a thread in [GitHub Discussions](https://github.com/Osmantic/ODS/discussions) or an issue. We're happy to help you figure out the right approach before you write code.

## License

By contributing, you agree that your work will be licensed under the [Apache License 2.0](LICENSE).
