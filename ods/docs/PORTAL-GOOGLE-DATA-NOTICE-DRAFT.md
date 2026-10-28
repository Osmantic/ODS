# ODS Portal: Google data notice

**Maintainer draft. This is not a published or approved privacy policy, Google
verification, or security-assessment acceptance. Resolve the review items below
before publishing it.**

## Proposed notice

Connecting Google lets Portal use the Gmail and Calendar features you select.
Reading email and events is separate from optional permissions for creating
Gmail drafts or changing Calendar events. The draft feature creates drafts for
you to review and send in Gmail; it does not send them for you. If you enable
direct Calendar changes, eligible private changes may run without separate
approval for each action.

Your ODS installation connects to Google and keeps authorization credentials
locally for background synchronization. It also stores your account identifier,
selected permissions, email summaries and metadata, and Calendar data. The local
broker may read Inbox message-body content to create bounded summaries; Sent
mail is handled as metadata in this release. Synchronization follows the
installation's configured queries and windows and may be incomplete.

Portal can use a local model or a cloud model you select. With a local model,
model processing runs on your installation. With a cloud model, connected-data
tool results and conversation context used by Portal may be sent to that
provider. Connecting Google does not mean all subsequent processing stays on
your computer. Review the selected provider's terms before using it with
private information.

Scheduled tasks can use the active model and granted permissions while ODS is
running, including when the browser is closed. Conversations, task instructions,
results and local history may retain connected information separately from the
synchronization cache.

Disconnecting Google stops synchronization, attempts to revoke ODS's grant at
Google, and removes the locally stored grant and connector caches, pending
actions and broker results. If Google cannot confirm revocation, local removal
can still finish; remove the app's access in your Google Account settings if
needed. Disconnect does not delete your Gmail messages or existing Calendar
events, erase conversations or scheduled-task history, remove exports/backups,
or delete copies already held by a cloud provider.

**Support:** [Insert the maintainer-approved ODS contact and public policy/home
links before publication.]

## Internal review items

- The project site already lists [contact@osmantic.com](https://osmantic.com/).
  Confirm its use and administration as ODS support and its selectability in
  Google's project. Do not substitute a contributor's personal email.
- Confirm the responsible operator, retention/deletion instructions and actual
  provider data-use terms. Add the applicable Google Limited Use commitment only
  after the maintainers can support it.
- Do not add encryption, attachment exclusion, human-access, model-training or
  complete-deletion guarantees without validating the implementation and
  provider conditions. File permissions alone do not establish encryption.
- This disclosure covers the Google integration. It does not replace the site's
  broader policy/terms. The [Formspree policy](https://formspree.io/legal/privacy-policy/)
  currently linked by the contact form is not the Portal app's privacy policy.
- Publish the approved notice on the maintained project domain, link it from
  the app's home and consent surfaces, and verify that domain for the project.
  This file does not establish any of those steps.

See [ODS Google app setup](PORTAL-GOOGLE-APP.md) and Google's
[homepage/privacy requirements](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification),
[API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy)
and [restricted-scope review](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification).
