# ODS V3 (3.0.0) candidate notes

Status: development candidate on `main`; not a published stable release.

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

The published stable baseline remains `v2.6.0`. The `v3.0.0` tag is reserved for
the final qualified commit and must not be moved after publication. This change
does not create a Git tag, publish a GitHub release, or claim installed upgrade,
backup, rollback, or reboot acceptance. Pin an audited commit while evaluating
V3; normal bootstrap commands follow moving `main`.
