# Contributing to Dream Server

Thanks for wanting to contribute. Dream Server is open source and we welcome help from everyone — whether you're fixing a bug, adding a service integration, or tackling a full feature.

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

Building on Dream Server for a hardware appliance, lab image, vertical bundle,
or downstream distribution? Start with
**[dream-server/docs/FORKABILITY.md](dream-server/docs/FORKABILITY.md)** and
**[dream-server/docs/BUILD-ON-DREAM-SERVER.md](dream-server/docs/BUILD-ON-DREAM-SERVER.md)**.
They explain the extension points, source-of-truth files, validation commands,
independent operation posture, and rebase-friendly patterns that keep custom
work easy to maintain.

For changes to installer, compose, lifecycle, auth, model routing, or host
mutation surfaces, use
**[dream-server/docs/HIGH_RISK_CHANGE_MAP.md](dream-server/docs/HIGH_RISK_CHANGE_MAP.md)**
to choose the right validation before opening a PR.

Every PR should make its changed surface obvious. The pull request template asks
contributors to classify the risk, list the checks they ran, and say whether the
change needs release-grade validation before a release. Docs-only changes do not
need the fleet; operational changes should not rely on "looks small" as the
validation argument.

## Full Contributor Guide

For current priorities, validation checklists, PR expectations, and style guidelines, see the detailed guide:

**[dream-server/CONTRIBUTING.md](dream-server/CONTRIBUTING.md)**

That's where we document what we need most, what gets merged fast, and what will get bounced back. Read it before your first PR — it'll save you a review cycle.

## Where to Ask Questions

Not sure about something? Open a thread in [GitHub Discussions](https://github.com/Light-Heart-Labs/DreamServer/discussions) or an issue. We're happy to help you figure out the right approach before you write code.

## License

By contributing, you agree that your work will be licensed under the [Apache License 2.0](LICENSE).
