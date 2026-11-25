# ODS-maintained Pixel 4.3.33 candidate

Functional source checkpoint: `fdac76a831a163860e864a9d34f395a604bed7a3` in
public ODS PR #7849. This record does not claim an upstream Pixel release or
completed fleet acceptance.

Web Courier's hash-required dependency lock advances certifi from 2026.7.22
to 2026.10.19 and urllib3 from 2.7.0 to 2.8.0. The version advances from
4.3.32 to 4.3.33 because the existing installer rejects different source at
the same version. OpenClaw, Node, direct Courier dependencies, base-image
digests, policies and trust anchors are unchanged. Generated Pixel plugin
version metadata follows the new source version; plugin behavior is unchanged.

The baseline lock reproduced the certifi advisory and three urllib3 findings.
The patched lock audits clean with pip-audit 2.10.1, installs with required
hashes on Python 3.11, and passes pip check and all 14 Courier tests. Real
TLS-verified urllib3 retrieval and trafilatura extraction of Python documentation
also passed in that clean environment.

The earlier official laptop install at the functional checkpoint did not prove
this change reached Courier: its bundled source still held the older lock and
Courier was disabled. The source and bundle mismatch is corrected by this new
candidate. Source packaging verification is recorded in the outer
`PIXEL-SOURCE-PROVENANCE.md`; installed acceptance is still pending.

Before accepting this release, run official Linux/WSL and native macOS upgrades,
verify the installed source and dependency versions, and exercise an enabled
Courier search/fetch journey through Portal. Test failure recovery as well as
successful switching. The release remains a candidate until that evidence is
recorded. No qualification signature or private upstream source was used.
