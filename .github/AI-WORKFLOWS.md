# Maintainer-requested AI triage and review

The **AI Issue Triage** and **Claude Code Review** workflows run only through
**Actions → Run workflow**. Enter the open issue or PR number. GitHub dispatch
access and an API permission check both require repository write, maintain, or
admin access. Opening an issue, commenting `@claude-review`, synchronizing a PR,
or applying `ai-fix` does not start either paid workflow.

Triage adds up to eight labels from the enum in
[`scripts/bounded_ai.py`](scripts/bounded_ai.py). Review posts an advisory comment
on a same-repository PR with at most 100 changed files and 1,000 changed lines.
Fork PRs are rejected using their live API head repository. No PR code is checked
out or executed; only the scripts from the workflow's own commit are loaded.

Both workflows share a serialized concurrency group and conservative rolling
limits: eight dispatches per repository per 24 hours, four per maintainer per
24 hours, and one per maintainer per hour. Failed, cancelled and queued requests
count too. An incomplete run ledger fails closed. Reruns are rejected in every
stage; request a fresh run after the cooldown. Only these two workflows share
this budget; it is not a provider-wide account spending limit.

Each accepted request permits one Anthropic Messages API call using the fixed
model in the script, with at most 48,000 bytes of JSON input and 2,048 output
tokens. There are no automatic retries, model tools, shell commands, agents, or
model-directed GitHub calls. The inference job has only `contents: read`, the
checkout does not retain credentials, and its Python step receives only the
model API key. It uses the Python standard library without downloaded packages.
The input/output token ceilings and request limits bound consumption; provider
billing limits remain an independent operator control.
The request uses standard capacity and a closed
[JSON output schema](https://platform.claude.com/docs/en/build-with-claude/structured-outputs);
the trusted publisher independently validates every response again.

Preparation uses GitHub read permissions, inference runs in another job, and
only the final trusted job receives `issues: write`. It validates the exact
repository, issue/PR number, workflow run, output schema and unchanged target
revision before using a fixed API operation. Triage can only add allowlisted
labels; title, body, assignment, milestone, repository and target-number changes
are not representable in model output. Review output is escaped as inert text.
JSON artifacts replace untrusted multiline `GITHUB_OUTPUT` values. Architecture
context describes stable paths and avoids stale source-line or service counts.

The old automatic `ai-fix` agent and paid review on every PR update have been
removed from Claude Code Review. Human reviewers implement fixes through the
ordinary PR process. The independent Issue to PR, nightly, release and scanner
workflows are not covered by this policy or its spending limits.
Issue to PR separately verifies that the actor applying `ai-implement` has
repository write access and limits repository write credentials to publication.

Run the offline regression tests with:

```sh
python3 -m unittest discover -s .github/scripts -p test_bounded_ai.py -v
```

These tests mock both APIs and cover prompt delimiters, additional metadata
fields, target substitution, disallowed labels, credentials, fork provenance,
rate limits, retries and changed target revisions. A paid live run is not part of
the test suite; maintainers should validate one manual run after merge and check
the configured Anthropic account budget separately.

Root workflow Actions are commit-pinned; Dependabot already proposes weekly
updates. The distro matrix and PostgreSQL service images are pinned by manifest
digest with their original tags retained for context. The resolution ledger is
[`ci-image-pins.json`](ci-image-pins.json). Each digest was checked against both
the registry's `Docker-Content-Digest` header and the SHA-256 of the downloaded
manifest bytes. Multi-platform index digests are used when available; the Mint
image publishes a single-platform manifest. When refreshing these pins, resolve
and verify the new manifest, update the ledger and workflow together, and rerun
`python3 -m unittest discover -s .github/scripts -p test_workflow_pins.py -v`.
Mutable Python package installs elsewhere in CI and nested workflow templates
remain outside this remediation.
