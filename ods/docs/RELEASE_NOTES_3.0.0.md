# ODS V3 Pre-Release (3.0.0) release notes

V3 is in **public testing and refinement ahead of the official V3 launch**.
We welcome feedback as we validate fresh installs, improve everyday workflows,
and resolve remaining issues before launch.

Source snapshot: [V3 Pre-Release (`v3.0.0`)](https://github.com/Osmantic/ODS/releases/tag/v3.0.0), published
on September 24, 2026, at 13:20:25 UTC. The `v3.0.0` tag is a lightweight,
unsigned Git tag for
[`bec0c42e7c9885a5aecd419a166a6a81e0d37236`](https://github.com/Osmantic/ODS/commit/bec0c42e7c9885a5aecd419a166a6a81e0d37236).
A repository ruleset has blocked moving or deleting `v*` tags since October 3,
2026; this release predates GitHub immutable releases and is not one.
Full fleet qualification remains in progress; no full fleet green is claimed.

**Security:** this release is affected by
[GHSA-vqpg-pvjj-4cmq](https://github.com/Osmantic/ODS/security/advisories/GHSA-vqpg-pvjj-4cmq)
(critical): the dashboard admin API is reachable without sign-in from the
network and through DNS rebinding. The fix (`84f4a8a10`, September 24, 2026) is
on `main`, not in this tag, and the 3.0.0 fix for
[GHSA-v3hj-52g2-2hmc](https://github.com/Osmantic/ODS/security/advisories/GHSA-v3hj-52g2-2hmc)
is effective only together with it. Do not deploy `v3.0.0`.

The pre-release name describes this public testing phase. The existing Git tag,
numeric product versions, and GitHub update-discovery metadata are unchanged.
The GitHub Latest label does not mark the official V3 launch.

ODS V3 brings the accumulated Portal/Pixel, installer, lifecycle, model-routing,
and dashboard work into one product version. See the [changelog](../CHANGELOG.md)
and [September promotion record](PUBLIC_BETA_PROMOTION_2026-09.md) for scope and
known recovery limitations.

The manifest, Linux/WSL, macOS and Windows installer identities, CLI, Dashboard
API, Dashboard package, and desktop installer metadata all report `3.0.0`.
Third-party dependencies, API/schema versions, minimum supported ODS versions,
and the separately versioned Pixel runtime retain their own version numbers.

## Qualification and publication

A version number does not certify a successful user experience. Extensive
Pixel/Portal user journeys must pass across the required fleet before UI and
model-switching qualification. Fresh installs and all remaining release gates
must then pass against the same final commit. Source tests and CI alone do not
establish this acceptance.

At publication, zero of the six required machines had completed qualification
against one common head. The GitHub Latest designation records publication,
not successful Pixel/Portal, model-switching, installed upgrade, backup,
rollback, or reboot acceptance. Later fixes and test receipts belong to their
own commits; this tag will not move to absorb them.

Normal bootstrap commands continue following moving `main`. `ODS_REF=v3.0.0`
reproduces this snapshot for comparison only; it installs the
GHSA-vqpg-pvjj-4cmq vulnerability. For a pinned deployment use an audited
`main` commit at or after `84f4a8a10`, check the
[security advisories](https://github.com/Osmantic/ODS/security/advisories), and
read the qualification boundaries before relying on it. Publishing the release can advertise
an available update to older versions; it does not automatically install one.
The existing native source-update and recovery restrictions still apply.
