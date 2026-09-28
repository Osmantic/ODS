# Signed source release pipeline

This pipeline prepares release candidates. It does not publish them, change an
existing tag, or switch the current quick start to a different installation
channel. The historical `v3.0.0` tag is not retroactively signed by this change.

## Maintainer sequence

1. Finish review and CI on `main`, including the release validation receipt.
2. Create a **new annotated, signed version tag** at the reviewed commit, using
   the maintainer's existing signing key. GitHub must report that exact tag
   object's signature as verified with reason `valid`. A lightweight tag or an
   unsigned annotation is rejected. Do not move a published tag to satisfy this
   requirement.
3. Push that tag. `release-provenance.yml` runs only on version tags in
   `Osmantic/ODS`. A manual rerun must also select the tag, not a branch.
4. Review the generated **draft** release and verify its files. The job never
   updates an existing release and never publishes automatically. A failed
   upload or incomplete draft needs maintainer inspection; a rerun does not
   overwrite its assets.
5. After installation, update and rollback validation, a maintainer can publish
   the candidate. Protect release tags and use GitHub immutable releases where
   available before advertising a stable installation channel.

The package gate compares the local tag object with the live GitHub ref, checks
GitHub's signature-verification result for that exact object, and requires the
tag to point directly to the workflow source commit reachable from `origin/main`.
The gate uses GitHub as its signature verifier; it does not maintain a second
local GPG trust store. Repository write permissions, tag protection and review
of the release workflow remain part of the trust boundary.

## Artifacts

| File | Purpose |
| --- | --- |
| `ODS-<tag>-source.tar.gz` / `.zip` | Committed source only, including installer entrypoints; untracked and working-tree changes are excluded. |
| `source.spdx.json` | Syft SPDX 2.3 dependency inventory of the archived source tree. |
| `release-manifest.json` | Repository, full commit, signed tag object, file sizes and SHA-256 digests. |
| `SHA256SUMS` | Digests of both source archives, the SBOM and the manifest. |
| `provenance.sigstore.jsonl` | GitHub OIDC attestation bundle covering the archives, inventory, manifest and checksum file. |

The source SBOM is **not** an audit of the OS packages in every pulled image,
downloaded model weight, or optional binary fetched later by an installer.
Those dependencies need their own digest/lock and provenance checks.

Archives preserve reviewed repository attributes and executable file modes.
The build host's `core.autocrlf` setting cannot silently change source bytes.
The packager rejects symlinks, non-regular entries, traversal paths and paths
that collide on case-insensitive systems instead of producing incompatible
Windows/macOS packages. Supporting such entries later requires a reviewed
packaging policy rather than silently dereferencing them.

## Verify before extraction or execution

Use a current GitHub CLI obtained from its official distribution. Download the
release files to a new empty directory, then verify the selected archive using
the bundle, expected repository, release workflow, tag ref and full commit:

```sh
gh attestation verify ODS-vX.Y.Z-source.zip \
  --bundle provenance.sigstore.jsonl \
  --repo Osmantic/ODS \
  --signer-workflow Osmantic/ODS/.github/workflows/release-provenance.yml \
  --source-ref refs/tags/vX.Y.Z \
  --source-digest FULL_REVIEWED_COMMIT_SHA \
  --deny-self-hosted-runners
```

Replace the version and full commit with the reviewed candidate's identity.
Verify `release-manifest.json`, `source.spdx.json` and `SHA256SUMS` the same way
before trusting their contents. A matching checksum alone only establishes
file integrity; it does not authenticate who produced the checksum file.

This producer has offline archive/gate contracts in PR CI. End-to-end OIDC
signing and release-asset verification require the first new signed candidate;
they cannot be honestly reported as validated by mocked API responses. Until
that candidate and the automated consumer are verified, the stable bootstrap
transition remains an open remediation item. No fallback to an unverified
archive should be introduced to make that transition appear successful.
