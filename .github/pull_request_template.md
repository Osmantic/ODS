## Summary

<!-- What changed, and why? -->

## AI Assistance

<!-- If AI tools helped draft code, docs, tests, reviews, or triage, say what they did. Use "None" when not applicable. The human author remains accountable for reading the diff, choosing the validation, and responding to review. -->

## Release Lane

<!-- Pick the lane before review. See ods/docs/RELEASE_CHANNELS.md. -->

- [ ] Stable hotfix targeting `release/2.5.x`
- [ ] Mainline change targeting `main`
- [ ] Next-minor work targeting the next feature/minor release
- [ ] Not sure; reviewer should help classify

Stable hotfix reason:

```text
<!-- If this targets release/2.5.x, explain the current-stable user problem it fixes. -->
```

## Changed Surface

<!-- Check every surface this PR can affect. -->

- [ ] Docs only
- [ ] Tests only
- [ ] Dashboard UI
- [ ] Dashboard API / host agent
- [ ] Installer / bootstrap / lifecycle
- [ ] Docker Compose / service manifests
- [ ] Model routing / Hermes / capabilities
- [ ] Network exposure / auth / proxy
- [ ] Dependencies / runtime wiring

## Risk And Validation

<!-- Use ods/docs/HIGH_RISK_CHANGE_MAP.md to pick the right level. -->

- Risk level: <!-- Low / Medium / High -->
- Validation run:
  - [ ] `git diff --check`
  - [ ] Markdown/link sanity for docs
  - [ ] Focused tests listed below
  - [ ] Dashboard lint/test/build
  - [ ] Extension audit / compose validation
  - [ ] Release-grade fleet or scoped hardware validation
  - [ ] Stable-lane patch validation, if targeting `release/2.5.x`
  - [ ] Not required because: <!-- explain -->

Commands/results:

```text
<!-- paste the important commands and results -->
```

## Operational Change Check

If this PR touches installer phases, bootstrap logic, compose stack generation,
service manifests, dashboard API control flows, Hermes, model routing, GPU or
runtime detection, lifecycle commands, host mutation, or network exposure, it
requires release-grade fleet validation before release unless the PR explains a
narrower equivalent.

- [ ] This is not an operational change.
- [ ] This is an operational change and validation is recorded above.
- [ ] This is an operational change and validation is intentionally deferred for:

## Notes For Reviewers

<!-- Call out skipped/deferred lanes, known limitations, or rollback notes. -->
