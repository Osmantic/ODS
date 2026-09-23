# Proposed public-beta merge protection (PB-006)

Read-only inspection on 2026-09-23 found `public-beta` unprotected. This proposal
has **not** been applied to GitHub. An administrator must review it with the
actual candidate's check names before enabling it.

Proposed policy:

- Require pull requests and at least one independent approving review.
- Dismiss stale approvals after new commits and require resolution of review
  conversations; the author must not approve their own change.
- Apply protection to administrators, disable force pushes and branch deletion,
  and document any emergency bypass as a separate reviewed incident.
- Require the branch to be current with `public-beta` before merge.
- Retain the repository's existing merge strategy. Signed commits/linear history
  need an explicit team decision; existing merge commits are not silently banned.

Observed check names from the previously merged candidate include:

- `Scan for secrets`
- `frontend (ubuntu-latest)`, `frontend (windows-latest)`, `frontend (macos-latest)`
- `api`, `integration-smoke`, `linux-smoke`, `macos-smoke`
- `readiness (ubuntu-latest)`, `readiness (macos-14)`
- `pixel-inference-contracts (3.11)`, `pixel-inference-contracts (3.12)`
- `powershell-lint (ubuntu-latest)`, `powershell-lint (windows-latest)`

The release check set must also include the new installer-log, beta-kit and AI
boundary checks once their actual names are observed in CI, plus dependency,
license/asset and full documentation gates required by the audit. A proposed
name is not evidence that a check already exists or ran.

Do not make a path-filtered workflow a required check without ensuring it
produces a terminal result for every relevant PR. Otherwise an unrelated change
can wait forever for a skipped workflow. Prefer an unconditional aggregate
release gate with explicit successful/skipped-job handling.

After applying protection, query the branch/ruleset API to verify the effective
rules and allowed bypass actors. Record that response with the frozen candidate's
check results. A green historical PR does not enforce future merges.
