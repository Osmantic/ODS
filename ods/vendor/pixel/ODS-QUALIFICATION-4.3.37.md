# ODS-maintained Pixel 4.3.37 candidate

Functional source checkpoint: `da05e4ddbb9bdefdecc154614c4d7ec33b2d9a3a`.
Prepared on 2026-10-28 for the ODS Portal integrations draft. This is an
ODS-maintained candidate, not an upstream release or completed platform qualification.

The previous refresh attempted Gmail's historical scan before Calendar or any
status publication. A real authorized local first sync remained in that path
for over 30 minutes. No provider content or credentials are included in this
record. A synthetic 400-Inbox/200-Sent reproduction made 1,010 serial requests
before the first Calendar call and had no status snapshot at that point.

Refresh now publishes status immediately, Calendar first, and each source
independently. Gmail interleaves listing/detail work across Sent, Inbox and
unread, with a default 64-request/90-second cooperative batch budget. Calendar
uses an 8-request/45-second budget with a query window frozen for each resumed
cycle. Private 0600 cursors bind credentials, queries and sanitizer revision.
Projection publication precedes cursor advancement; a crash may repeat reads
but cannot skip unpublished records. Account changes are rechecked before
publication; ODS must also quiesce readers/actuators around credential replacement.

Existing sanitized records survive partial scans and transient errors only
within the current credential/query generation. Pruning waits for a complete
scan. Partial coverage and retained observations stay explicit; configured page
limits do not imply complete coverage. Sent remains metadata-only, including
when an Inbox detail identifies the message as Sent before Sent listing finishes.

HTTP JSON reads check monotonic time between read1 chunks, detect truncated
bodies and enforce a 64 MiB resource ceiling within the 512 MiB broker service.
A cooperative budget may overrun by an already-started finite socket wait; it
is not an exact process deadline. The generated systemd refresh unit additionally
has TimeoutStartSec=300. Existing known errors remain visible during retries
until a source succeeds. These bounds do not promise all possible mailboxes
fit within the service's memory or complete in one run.

At the functional checkpoint, the exact CI unittest invocation passed all 114
source-broker, Gmail/Calendar incremental and loopback HTTP cases. The new cases
exercise resume, crash ordering, cache/pruning, credential/query drift, private
cursor custody, Sent body restrictions, independent Calendar/Gmail failure,
partial-page recovery, persistent error status, real slow-drip HTTP, short reads
and oversized responses. They use synthetic accounts and a local HTTP server.
The shared broker-header generator and diff checks also passed. They do not
prove real provider refresh/revocation or installed fleet behavior.

The version increases from 4.3.36 because a changed held source requires a
strictly increasing version. Native MCP preservation and earlier source/schema
fixes are inherited; see ODS-QUALIFICATION-4.3.36.md for their scoped evidence.
OpenClaw, dependency/image pins and trust anchors are unchanged. No release
signature is generated. Historical compatibility records are retained.

Before accepting this candidate, finish exact bundle/source and end-to-end
checks, ordinary installed upgrade and owner journeys, native macOS and
applicable fleet qualification. The existing comparison-file checksum release
gate documented for 4.3.36 is not waived or redefined by this candidate.
