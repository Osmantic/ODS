# ODS-maintained Pixel 4.3.28 candidate

Qualification date: 2026-09-30. Public project: https://github.com/Osmantic/ODS.
Functional ODS source checkpoint: `62ac4f546d356c3897c2587381e2686a1393f6e9`.

This is an ODS-maintained candidate assembled from the visible public ODS vendor
source. It is not a claim of an upstream/private Pixel release, upstream audit,
signed release envelope, or a completed physical installation qualification.
No private repository or signing credential was used. Historical compatibility
records and the historical 4.3.27 reactivation bridge remain unchanged.

The version increment permits the existing strictly increasing release-update
contract to distinguish reviewed source from the previously installed 4.3.27.
It does not bypass source identity checks by replacing a same-version release.
OpenClaw 2026.6.33, its official plugin versions, dependency versions, container
image digests and trust anchors are unchanged.

## Qualified functional scope

The public ODS source handoff keeps admission held while the exact source is
staged, copied and reconciled. It preserves the owner's existing Sandbox or
Full Access choice and requires fresh coordinator verification before release.
The root-only source-begin operation binds its token and baseline under the
coordinator lock; ordinary model finish cannot release an incomplete source
plan. A source-only rollback retains the new protected guard. Once downstream
configuration/service work starts, recovery resumes the same reviewed installer
instead of claiming a source-only rollback restores the entire installation.

Temporary publication files use exact root-custodied staging outside the
inventoried source trees. Deterministic process-death tests cover apply and
rollback at creation, partial write, fsync and both sides of rename. Unknown
scratch entries, substituted directories, symlinks and mount splits fail closed;
no prefix-based deletion or stale receipt adoption is permitted. Completed
updates do not freeze future owner extensions or later reinstallations.

## Evidence and boundaries

The frozen functional candidate passed **687 tests, with 9 explicit skips and
13 subtests** across source upgrades, access/release coordination, protocols,
marker custody, and macOS planning contracts. This includes 10 real unprivileged
SIGKILL cases in temporary directories, real Unix-socket peer rejection for the
new source operation, and literal installer-handoff tests. An independent
focused rerun reported **87 passing tests** before this packaging checkpoint.

The nine skips were eight real root/unprivileged Unix-peer combinations and one
isolated-root macOS planning fixture. They were not forced by changing the live
machine's permissions. These checks are not a full privileged installation,
Windows reboot, real macOS upgrade, GPU qualification or migration test on every
previous ODS release.

Packaging consistency is checked with the repository's release-file generator
and public bundle verifier. The maintained bundle is generated twice from the
visible bytes and tracked Git modes, then independently fetched and compared
file by file. Its synthetic single-root commit has no upstream/private ancestry;
packaging metadata is reproducibility evidence, not an approval or signature.
Exact bundle identity and final packaging results live in ODS's public
`vendor/PIXEL-SOURCE-PROVENANCE.md` and installer pins, outside this bundle to
avoid a self-referential hash.

The complete upstream `check-release-contract.mjs` already fails on this public
export's reviewed comparison-prompt hash and requires historical audit files
not present in the export. No audit document or signature is fabricated to make
that checker appear green. Focused public consistency and source-byte/mode
verification are reported separately. This candidate remains `candidate` until
the complete disposable installation and intended physical acceptance finish.
