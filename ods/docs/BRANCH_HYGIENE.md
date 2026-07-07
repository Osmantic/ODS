# Branch Hygiene

Branches are coordination artifacts, not permanent records. The durable history
is `main`, release tags, merged PRs, issues, docs, and validation receipts.

This policy keeps a fast-moving repository easier to audit without deleting
active work or breaking contributor forks.

## Keep

- `main` and protected release branches;
- branches backing open pull requests;
- active maintainer branches with recent commits;
- long-lived integration branches that are documented in an issue or project;
- fork branches owned outside the organization.

## Clean Up

Candidates for cleanup are branches that are all of:

- merged, abandoned, or superseded;
- not referenced by an open PR;
- older than the agreed stale window;
- not a release, support, or protected branch;
- not needed for a documented investigation.

Prefer closing or merging the PR first, then deleting the branch through GitHub.
Do not bulk-delete remote branches without a dry-run list and maintainer review.

## Naming

Use names that make ownership and purpose obvious:

| Purpose | Pattern |
|---------|---------|
| Maintainer feature/fix | `fix/<short-topic>` or `feature/<short-topic>` |
| Codex/automation work | `codex/<short-topic>` |
| Documentation | `docs/<short-topic>` |
| Release/support | `release/<version>` or `support/<version>` |

Avoid branch names that encode secrets, customer names, private hostnames, or
local machine details.

## Dry-Run Audit

Use the dry-run helper to list possible stale branches. It does not delete
anything:

```bash
python ods/scripts/maintainers/list-stale-branches.py --days 45
```

By default the helper excludes `main`, `master`, `develop`, `release/*`,
`support/*`, and branches backing open PRs when the GitHub CLI is available.

Review the output before deleting anything. When in doubt, leave the branch and
link it to an issue describing why it should stay.
