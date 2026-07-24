# GitHub stock-file and credential-surface audit

Audit date: 2026-07-17
Repository: `Osmantic/ODS`
Remote baseline: `main` at `e100a6f6ad99a44f4ab0c5e35a055fec8fbc3d98`
Access: `viewerPermission=READ`; no GitHub mutation performed

## Method

All GitHub CLI calls ran through the separate host GitHub profile:

```sh
op run --env-file="${HOME}/.config/platform/environments/github.env" -- \
  gh repo view Osmantic/ODS --json \
  nameWithOwner,defaultBranchRef,isPrivate,url,viewerPermission,description

op run --env-file="${HOME}/.config/platform/environments/github.env" -- \
  gh api 'repos/Osmantic/ODS/git/trees/main?recursive=1'

op run --env-file="${HOME}/.config/platform/environments/github.env" -- \
  gh api 'repos/Osmantic/ODS/contents/.github/workflows?ref=main'
```

The Git Tree response was not truncated: 1,344 entries, 1,167 blobs, and 177
trees. Its complete blob-path set exactly matched `git ls-tree -r origin/main`;
therefore every stock file was included in path and pattern classification.

## Results

| ID | Result | Evidence |
| --- | --- | --- |
| GH-001 | Pass | Remote metadata identifies public `Osmantic/ODS`, default branch `main`, and read-only viewer permission. |
| GH-002 | Pass | Tree API and freshly fetched `origin/main` have zero blob-path differences. |
| GH-003 | Pass | No stock file contains an `op://` reference. |
| GH-004 | Pass | No stock file contains `OP_SERVICE_ACCOUNT_TOKEN` or `OP_BIOMETRIC_UNLOCK_ENABLED`. |
| GH-005 | Pass | No stock file exports or assigns local `GH_TOKEN`, `FIRECRAWL_API_KEY`, or `BRAVE_API_KEY`; one installer line clears `BRAVE_API_KEY` for offline mode. |
| GH-006 | Pass with explicit exception | Nine workflow assignments use GitHub's ephemeral `${{ github.token }}` or `${{ secrets.GITHUB_TOKEN }}`. These are platform-issued, job-scoped GitHub credentials, not host provider tokens, and should not be replaced with a long-lived 1Password value. |
| GH-007 | Pass | Nineteen workflow files were enumerated independently by Tree and Contents APIs. All 85 third-party `uses:` references are pinned to 40-character commit SHAs. |
| GH-008 | Pass | Stock secret-security suite under Homebrew Bash reports 20 passed, 0 failed, 3 skipped; `.gitignore` covers all five sensitive-file pattern classes it checks. |
| GH-009 | Finding | `ods/tests/run-bats.sh` clones Bats repositories into the source tree when absent. This is a test-time network mutation and conflicts with pre-provisioned dependency governance. It was not triggered or repaired in this docs/schema branch because another dirty checkout already contains unrelated test-tooling work. |
| GH-010 | Finding | Directly validating stock `.env.example` against `.env.schema.json` rejects six `CHANGEME` placeholders whose length is below the runtime minimum. The example is intentionally non-runnable, but it is not itself schema-valid and should not be represented as such. |
| GH-011 | Informational | Secret-scan CI downloads gitleaks 8.28.0, verifies the vendor checksum, runs with redaction, and uploads a report. This is pinned CI setup rather than an application runtime download. |

## Stock credential-like files

The full tree path scan identified only these credential-shaped tracked paths:

- `ods/.env.example`
- `ods/.env.schema.json`
- `ods/config/openclaw/inject-token.js`
- `ods/extensions/library/services/dify/.env.example`
- `ods/tests/test-openclaw-inject-token.sh`

They are examples, schema, product token-injection code, or tests—not committed
credential stores. Existing product secret behavior is outside this
documentation-only change; the stock security suite remains the authoritative
focused regression lane.

`ods/.env.example` contains 69 assignments with no duplicate names and 23 names
classified as secret variables by the stock security suite. It deliberately
uses placeholders for required runtime secrets. Provider values are not moved
into that file by this change; host developer-tool credentials remain in
separate reference-only files outside the repository.

## Workflow inventory

The Contents API returned these 19 stock workflows:

- `ai-issue-triage.yml`
- `autonomous-code-scanner.yml`
- `claude-review.yml`
- `dashboard.yml`
- `issue-to-pr.yml`
- `lint-powershell.yml`
- `lint-python.yml`
- `lint-shell.yml`
- `matrix-smoke.yml`
- `nightly-code-review.yml`
- `nightly-docs-update.yml`
- `openclaw-image-diff.yml`
- `release-notes.yml`
- `secret-scan.yml`
- `test-linux.yml`
- `type-check-python.yml`
- `validate-catalog.yml`
- `validate-compose.yml`
- `validate-env.yml`

This branch does not modify any workflow. GitHub writes, secrets, variables,
environments, Actions runs, branch protection, pushes, and pull requests remain
outside the granted read-only scope.

## Governance response

The new repository policy prohibits future ad hoc credential wrappers,
repository `op://` references, biometric fallback, runtime tool downloads,
unvalidated scope changes, and provider-variable exports. The Bats bootstrap
finding and placeholder/schema behavior remain explicit stock findings; they
must be resolved in their existing test/configuration workstream rather than
silently mixed into this documentation branch.
