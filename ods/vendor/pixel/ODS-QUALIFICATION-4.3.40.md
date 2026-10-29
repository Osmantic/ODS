# Portal runtime candidate: internal package 4.3.40

Functional source checkpoint: `ad8733c2ca757256c7b98ae6aec936a41d6aff74`.
Prepared on 2026-10-28 for the Portal integrations draft. The compatibility
entry names that separately committed functional source. The legacy package
and directory identifier remains `pixel`; Portal is the public ODS experience.
This is an ODS-maintained internal candidate, not an upstream release or
completed platform qualification.

Controlled MIME fixtures demonstrated that a text attachment included inline
in a Gmail `format=full` response could enter an email summary. The collector
now excludes body content for a MIME part with a nonempty filename or any
`Content-Disposition: attachment` header. Exclusion is inherited by the entire
subtree, including nested message and multipart descendants. Case, whitespace,
comments and duplicate disposition headers are handled conservatively. Bounded
attachment metadata can still appear; ordinary message-body text is retained.

The producer's sanitizer revision increases from 2 to 3. Revision-bound cached
records and incremental cursors cannot reuse an earlier email summary. The
Source reader also requires exactly revision 3 before returning any email
projection, closing the interval before a successful post-upgrade refresh.
Inbox, Sent, Search, Read and Thread all use this check. Missing, old, malformed
and unknown future revisions return a fixed refresh-required error without
echoing email contents. Calendar and social reader behavior is unchanged.

The broker/Gmail/Calendar/HTTP controlled suite passed 118 tests. Twenty-six
Source-plugin and OAuth controls passed with the real locked TypeBox 1.3.13
validator and pinned OpenClaw SDK 2026.6.33; 34 Google sign-in disclosure tests
passed. New regressions failed against the preceding implementation. An
independent review repeated nine focused Python controls with 23 subtests,
three selected Source/dispatcher controls and the disclosure control. These
fixtures cover ancestor exclusions, duplicate disposition headers, HTML body
with a plain-text attachment, summary/risk/source-hash stability when only
attachment content changes, old cache/cursor rejection, interrupted refresh,
all five email reader paths and unchanged Calendar/social compatibility.
These are controlled component and dispatch tests, not model or installed
gateway execution.

This is a summary-content boundary. Gmail can still deliver inline attachment
bytes to broker memory in a `format=full` response before the collector filters
them. This change does not rewrite earlier chats, scheduled-task results,
archives, backups or copies already sent to a selected cloud model. It does not
add scopes, change account consent, alter ordinary message-body summaries or
turn natural-language limits into an access-control boundary. Sent remains
metadata-only. The user disclosure distinguishes excluded attachment content
from possible attachment metadata and data passed to a selected cloud model.

At this preparation point no 4.3.40 bundle has been generated and no ordinary
installed 4.3.40 upgrade, real-model behavior, account or provider run has been
qualified for this repair. Earlier installed 4.3.39 evidence is not evidence
of this new MIME/cache revision.

The version increases from 4.3.39 because a changed held source requires a
strictly increasing package version. Dependency/npm integrities, OpenClaw,
TypeBox, image/runtime pins and trust anchors remain unchanged. All historical
compatibility rows and qualification documents retain their original contents;
the supported bridge-version lists retain all earlier identifiers. No release
signature is generated or claimed.

The official release-file generator and consistency check passed for 4.3.40.
The current status, platform-reference and document-inventory generators also
passed their independent checks. Four migration-schema cases and three
isolated migration/restore shell fixtures passed with the current 4.3.40
target. Their subprocesses use temporary state and fake privileged boundaries;
these are version-bound fixture checks, not an ordinary installed upgrade.
Byte comparisons confirmed the preceding compatibility rows and historical
qualification files were preserved, and only current version metadata changed.

The full upstream public-release contract still lacks the historical
`LIVE-AUDIT-4.0.0.md` and `LIVE-AUDIT-4.1.0.md` documents omitted from this export.
The full documentation generator also stops at the omitted historical
`LIVE-AUDIT-4.3.27.md`. These documents are not copied or fabricated and their
evidence requirements are not waived. Public release acceptance and required
native macOS and other platform/fleet lanes remain open.
