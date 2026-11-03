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

- Review Coffee's actual [#8324](https://github.com/Osmantic/ODS/pull/8324) with
  [#8352](https://github.com/Osmantic/ODS/pull/8352) and
  the [composition acceptance record](PORTAL-COFFEE-INTEGRATION.md), including
  shared Settings navigation, native tool IDs, grants and pending transitions.
  The local composition is not automatically merge or release acceptance.
- Agree how to land this consolidation with the earlier connected-app PRs,
  retaining their authorship. A passing checkpoint CI is not approval to merge
  overlapping stacks in arbitrary order.
- Assign native macOS and the applicable platform/release acceptance. Local
  Windows/WSL qualification and simulated provider lanes do not replace them.
- Distinguish Coffee's implemented native Mac integrations transport from Google
  and the fixed reviewed-app journey in Connected apps. The latter still returns
  unavailable on Darwin and depends on Linux/WSL Source Broker custody and
  transactions. Its Mac path needs implementation, including lifecycle and
  interruption recovery, before physical testing can qualify it. Removing the
  transport refusal alone would not provide that support.
- Review the remaining limitations in the [integration status](PORTAL-INTEGRATIONS-STATUS.md),
  especially real-provider recovery, connected-data/model behavior and installed
  withdrawal/update/rollback. Scope or release acceptance must be explicit.

The assembled source includes the scheduler/Darwin fixes, explicit future-task
policy, native Mac schedule wiring and the local concrete-operation review
interface. Gabriel's physical Windows installation remains `60534c7b2`.
The published draft checkpoint `d18e2a1de` has 93 passing hosted checks and one
skip; earlier disposable Linux upgrade evidence retains its recorded scope.
Neither result qualifies the newer local code or another installed update.

SDK12 cancellation repairs passed native cold controls, not real-model benefit.
Previous semantic experiments remain failed evidence. The latest operations-only
interface failed its 9B threshold: 48/56 valid cells qualified, with zero
false-eligible negatives or extra canonical scopes. This is a conservative
miss, not reliable continuation acceptance. Later catalog and consistency
prototypes also failed their frozen gates and were reverted. A seventh concrete
candidate experiment admitted no incorrect action but refused 22 of 24
legitimate positive cells and left eight negative cells unexercised. It failed
too. The local catalog-derived semantic operation projection has passed code
review and mechanical controls; fresh real-model qualification is still open.
A real chat-created
task exposed future-policy defaults and a never-run status error; source repairs
are structurally qualified, with installed retakes still pending.
Before landing, complete final ordinary
Windows/WSL installation, real-model safety/continuation and the physical Mac
matrix. The Google project, verified domain, published data notice and official
client work above remain maintainer publication tasks; local QA consent does not
fulfill them. The ODS-maintained Source candidate advances to 4.3.42 to fix
broker-readable proposal publication under the gateway's private umask. Its
paired bundle identity changed coherently and old prepared-client identities
are retained. See [source provenance](../vendor/PIXEL-SOURCE-PROVENANCE.md)
for the deterministic artifact checks and the preexisting historical audit-file
gate that remains failed. Ordinary installed adoption is still required.
Dependency, image and trust pins remain unchanged; the separate 25 managed SDK
compatibility repairs retain their own custody and runtime qualification
requirements.

No password, account token, DNS credential or OAuth grant needs to be posted in
the PR or chat to coordinate this work.
