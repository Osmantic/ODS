# ODS V3 Pre-Release (3.0.0) release notes

V3 is in **public testing and refinement ahead of the official V3 launch**.
We welcome feedback as we validate fresh installs, improve everyday workflows,
and resolve remaining issues before launch.

Source snapshot: [V3 Pre-Release (`v3.0.0`)](https://github.com/Osmantic/ODS/releases/tag/v3.0.0), published
on September 24, 2026, at 13:20:25 UTC. The immutable `v3.0.0` tag points to
[`bec0c42e7c9885a5aecd419a166a6a81e0d37236`](https://github.com/Osmantic/ODS/commit/bec0c42e7c9885a5aecd419a166a6a81e0d37236).
Full fleet qualification remains in progress; no full fleet green is claimed.

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

Normal bootstrap commands continue following moving `main`. Use `ODS_REF=v3.0.0`
or the exact tagged commit for reproducibility, and read the qualification
boundaries before relying on a deployment. Publishing the release can advertise
an available update to older versions; it does not automatically install one.
The existing native source-update and recovery restrictions still apply.
