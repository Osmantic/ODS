# Portal runtime candidate: internal package 4.3.38

Functional source checkpoint: `1e40a5add3d5725372ef27ec987bd8e650e9bd5f`.
Prepared on 2026-10-28 for the Portal integrations draft. The legacy source
directory/package identifier remains `pixel`; Portal is the public ODS
experience. This is an ODS-maintained candidate, not an upstream release or
completed platform qualification.

An authorized installed Portal request read one bounded Gmail projection and a
covered seven-day Calendar window. The read tools worked, but the model called
the partial email index's `totalMatches` an Inbox total. A native scheduled
follow-up acknowledged partial coverage but used pagination's `hasMore` as the
explanation. Neither statement establishes the total number of messages in the
account. No personal record, subject, sender, account identifier or credential
is included in this candidate or record.

Gmail Inbox, Sent, search and thread results now put a structured `counting`
object before their records. It always reports `mailboxTotal: null` and
`canAnswerMailboxTotal: false`. It names the local index, configured queries,
freshness and whether coverage is partial, stale, unknown or complete within
those queries. Existing page counts, indexed matches, offsets and `hasMore`
retain their meanings. Exhausting one page/index never proves full mailbox
coverage; projected-field searches do not search every raw email field.

Complete configured-query coverage requires explicit fresh data, a globally
completed scan with zero retained records, no earlier-scan record flags and
named complete/untruncated relevant queries. One finished Inbox folder cannot
certify a cache still retained during the global scan. Pagination and source
synchronization remain separate. No permission, credential handling, provider
query, raw-body access or action capability is added.

Seven unit cases with mutation matrices and eighteen plugin cases passed with
the exact TypeBox 1.3.13 in disposable QA dependencies. The existing plugin-entry
fixture remains in that lane; this is not native gateway execution. Main
independently repeated those twenty-five cases. Existing OAuth plugin controls
also passed. These results prove the result contract and its projection scope.

A separate synthetic HTTP benchmark used the existing local Qwen3.5-9B engine,
one baseline/candidate pair for each of five scenarios, without retries or best
run selection. Candidate answers marked the account total unknown in all five,
but one incorrectly called stale data current and another opened ambiguously
before its partial-index qualification. The stale notice was subsequently
clarified and unit-tested, without replacing or selectively rerunning the
recorded benchmark. Model answer consistency remains an open acceptance gate;
correct tool metadata is not enforcement that the model will describe it right.

The version increases from 4.3.37 because a changed held source requires a
strictly increasing package version. Incremental synchronization and native app
preservation are inherited. OpenClaw, dependency/image pins and trust anchors
are unchanged; no release signature is generated or claimed. Historical
qualification records retain their original artifact identifiers.

Before release acceptance, verify the exact public bundle/source/digest/modes,
ordinary installed upgrade and final Portal behavior, native macOS and relevant
fleet lanes. The existing comparison-file checksum release gate documented for
earlier candidates remains open; it is not waived by this change.
