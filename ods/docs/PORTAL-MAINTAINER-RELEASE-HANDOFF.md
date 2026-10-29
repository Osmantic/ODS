# Portal integrations: maintainer release handoff

This is the release coordination checklist for the integrations draft. It does
not request a merge, register a production OAuth client, publish a policy or
claim Google approval. Code qualification can continue independently of these
maintainer decisions.

## Google connection for ordinary users

The initial **ODS Portal** identity has been saved in a separate Google project
with an External audience in **Testing**. It is not the personal Desktop client
used for local read-only qualification. The repository distributes neither that
personal client nor any user's grant. Testing branding alone does not make the
normal Connect Google experience available to everyone.

The maintainers need to:

1. Name the maintainer responsible for the production project and keep project
   access under maintained accounts. Confirm a maintained support/developer
   contact for the public consent screen.
2. Have someone with control of `osmantic.com` verify the domain in Search
   Console using an account associated with the Google API project.
3. Approve and publish a Portal integration homepage and the actual Google data
   notice on that domain, link the policy from the homepage, and confirm the
   operator and support contact. The [data-notice draft](PORTAL-GOOGLE-DATA-NOTICE-DRAFT.md)
   is review material, not a published policy.
4. Approve the permissions and features being released. Provision the official
   Desktop client and required APIs in the maintained project, and complete the
   applicable Google brand/scope review. Include optional cloud-model data
   handling in that review; local token storage alone does not determine whether
   a security assessment applies.
5. Provide the reviewed client through ODS's release/deployment process and
   qualify Connect Google on a clean installation. The ordinary user should
   consent in Google, without creating a Cloud project or downloading client
   JSON. Advanced owner-provided clients remain a separate option.

See [Google app setup](PORTAL-GOOGLE-APP.md), Google's
[brand and domain requirements](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification)
and [restricted-scope review](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification).

## Integration and release coordination

- Obtain Coffee's actual custom-MCP/Home Assistant/ntfy/Obsidian PR and review
  shared Settings navigation, native server IDs, grants and pending transitions.
  His reported features are not automatically qualified by this consolidation.
- Agree how to land this consolidation with the earlier connected-app PRs,
  retaining their authorship. A passing checkpoint CI is not approval to merge
  overlapping stacks in arbitrary order.
- Assign native macOS and the applicable platform/release acceptance. Local
  Windows/WSL qualification and simulated provider lanes do not replace them.
- Review the remaining limitations in the [integration status](PORTAL-INTEGRATIONS-STATUS.md),
  especially real-provider recovery, connected-data/model behavior and installed
  withdrawal/update/rollback. Scope or release acceptance must be explicit.

No password, account token, DNS credential or OAuth grant needs to be posted in
the PR or chat to coordinate this work.
