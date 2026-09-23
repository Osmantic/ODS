# Bounded GitHub actions

Pixel's GitHub broker is a narrow mutation adapter for issue creation, pull-request
creation, and issue or pull-request comments. It is not a generic GitHub token proxy and
does not merge, push, create branches, edit repository settings, or infer repository
scope. Those are separate capabilities with separate authority.

The broker uses GitHub REST API version `2026-03-10`. The documented create endpoints do
not declare a provider idempotency header, so Pixel appends an invisible, deterministic
`pixel-action` marker to the reviewed Markdown body. The marker exists before the first
POST. If the response is lost, Pixel lists a bounded, time-scoped provider window and
settles only one object whose marker and semantic fields match. Zero or multiple matches
remain `unknown`; the original POST is never blindly replayed.

## Private inputs

Copy `deploy/github-broker/policy.example.json` to an owner-private file, name only the
allowed repositories and operations, then set `enabled` to `true`. Store a fine-grained
token in a separate owner-private file. Use the smallest repository permissions GitHub
documents for the selected endpoint: Issues write for issues/comments or Pull requests
write for pull requests.

The exact proposal follows `schemas/github-action-proposal-v1.schema.json`. Review its
bytes and SHA-256 outside the agent, then run:

```bash
./pixel github-action apply \
  --action-id github-... \
  --proposal /private/proposal.json \
  --proposal-sha256 EXACT_LOWERCASE_SHA256 \
  --policy /private/policy.json \
  --token /private/github-token \
  --journal-root /private/github-journal \
  --result /private/github-results/github-....json \
  --confirm
```

If the result is indeterminate, run the same command with `reconcile` instead of `apply`.
Reconciliation performs reads only. A second `apply` for an already-submitted action is
refused. The content-free journal can be audited without exposing proposal text or the
credential; the provider-bound result remains private.

`./pixel action-journal status --journal-root ... --action-id ...` shows the content-free
state and exact head hash. A still-unsent `proposed` action can be canceled with that
exact head hash and `--confirm`; submitting, unknown, reconciling, and terminal actions
cannot be canceled into a false claim that no provider effect occurred.

GitHub endpoint references:

- <https://docs.github.com/en/rest/issues/issues?apiVersion=2026-03-10>
- <https://docs.github.com/en/rest/issues/comments?apiVersion=2026-03-10>
- <https://docs.github.com/en/rest/pulls/pulls?apiVersion=2026-03-10>
