# Installer Trust And Provenance

ODS installers set up Docker services, write local config, generate secrets,
and may install missing prerequisites. Treat them like any other infrastructure
installer: inspect the source, use a release or audited commit when you need
reproducibility, and keep the default localhost security posture unless you
intentionally expose services to your LAN.

## Install Paths

### Verified stable source (release gate)

The [verified installer preview](VERIFIED_INSTALL_PREVIEW.md) is separate from
the current public quickstart until qualification passes. It verifies an
immutable stable release archive before extraction or execution, checking the
annotated tag and its verified signature, then uses GitHub CLI to constrain the attestation to `Osmantic/ODS`,
the release workflow, the exact tag and full commit on a GitHub-hosted runner.
The inspectable command bodies are also in `installers/verified-release.sh`
and `installers/verified-release.ps1`; tests keep them identical to the preview.
Public HTTP downloads and local bundle verification do not require GitHub login.

**Rollout gate:** the historical `v3.0.0` release has neither those assets nor
GitHub's immutable-release flag. The verified installer refuses it without
touching an existing ODS installation. The first eligible release must be
produced, tested by the implementation team and explicitly authorized for
publication before advertising this channel as usable.
See [Signed Source Releases](SIGNED_SOURCE_RELEASES.md).

The main README retains the existing development-main commands, with their
unsigned-source limitation stated explicitly. Runtime security fixes can be
reviewed and merged before this channel changes; SEC-005's default-channel
requirement remains open until real release qualification and activation.

### Current Linux/macOS Bootstrap (development main)

The hosted development one-liner, used by the README quickstart, is:

```bash
curl -fsSL https://install.osmantic.com/ods.sh | bash
```

The Osmantic Worker proxies the current bootstrap from:

```text
https://raw.githubusercontent.com/Osmantic/ODS/main/ods/get-ods.sh
```

Canonical `/ods.sh` and explicit `/ods/main.sh` aliases serve the same mutable
repository `main` source. Script responses identify that contract with:

```text
X-ODS-Channel: main
X-ODS-Source-Ref: main
```

There is no separate hosted-bootstrap promotion. Reviewed changes to
`ods/get-ods.sh` become available after they merge to `main` and the edge cache
refreshes.

The Worker keeps the Osmantic domain, validates the response as a bounded Bash
script, preserves useful cache and provenance headers, redirects browser
documents to the ODS website section, and fails closed on invalid upstream
content.

The cache is fresh for five minutes. After that, the first request may receive
the last validated script while the Worker refreshes it in the background. If
GitHub is temporarily unavailable, the Worker may serve the last validated
script for up to one day. For a critical install, download and compare the
script before running it.

The bootstrap:

- detects Linux, WSL, or macOS;
- installs or checks basic prerequisites where supported;
- clones `https://github.com/Osmantic/ODS.git` with sparse checkout for the
  `ods/` product tree;
- copies the runtime product files into `~/ods`;
- runs `./install.sh` from that copied runtime tree.

The bootstrap source and installed checkout are separate selections. The
hosted script follows `main`; `ODS_REF` selects a compatible branch, tag, or
exact 40-character commit SHA for the repository checkout. Without `ODS_REF`,
the checkout also follows the repository default branch, currently `main`.

For example:

```bash
curl -fsSL https://install.osmantic.com/ods.sh | ODS_REF=main bash
```

`ODS_REF` can select only refs that contain the current `ods/` product-tree
layout used by the sparse checkout. To pin an audited commit:

```bash
curl -fsSL https://install.osmantic.com/ods.sh | ODS_REF=AUDITED_COMMIT_SHA bash
```

Do not pin `v3.0.0` for an installation: it is affected by
[GHSA-vqpg-pvjj-4cmq](https://github.com/Osmantic/ODS/security/advisories/GHSA-vqpg-pvjj-4cmq)
(critical) and does not contain the fix (`84f4a8a10`). Check the
[security advisories](https://github.com/Osmantic/ODS/security/advisories)
before pinning any ref; see [V3 release notes](RELEASE_NOTES_3.0.0.md).

Older tags that predate the current layout must be installed through the
manual source path below.

Maintainers can verify all twelve hosted Worker aliases against an exact Git
ref:

```bash
bash ods/scripts/verify-hosted-bootstrap.sh origin/main
```

The verifier requires `main` response metadata, compares exact
`ods/get-ods.sh` bytes, and runs `bash -n`. If verification occurs immediately
after a merge, wait for the five-minute freshness window or purge the edge
cache first.

Before installation, the bootstrap checks for an explicitly declared older
install path, sibling directories with install state, Compose, and the core
service signature, and existing Compose projects with the core service tuple.
This preserves automatic coexistence protection without depending on retired
product names. A dormant install in a custom nested path may not be
discoverable; set `ODS_LEGACY_INSTALL_DIR=/path/to/install` to check it
explicitly. Use `ODS_ALLOW_LEGACY_PARALLEL=1` only after assigning separate
ports and data paths.

### Manual Source Install

To install an exact audited commit, use a full clone so Git can resolve the
commit:

```bash
git clone https://github.com/Osmantic/ODS.git
cd ODS
git checkout AUDITED_COMMIT_SHA
./install.sh
```

Use the manual path when you want to review diffs, pin an exact commit, make
local modifications, or avoid trusting the hosted delivery path.

For an immutable copy of only the bootstrap file, replace
`AUDITED_COMMIT_SHA` in this URL:

```text
https://raw.githubusercontent.com/Osmantic/ODS/AUDITED_COMMIT_SHA/ods/get-ods.sh
```

A commit-specific bootstrap URL does not by itself make the complete install
immutable. Pair it with `ODS_REF=AUDITED_COMMIT_SHA` when the installed payload
must also be pinned, or use the audited source checkout when you want to review
or modify the tree before installation.

### Windows PowerShell Install

Windows has no qualified tagged release. `v3.0.0`'s `install.ps1` runs the
retired native Docker Desktop installer with Hermes, not the current Ubuntu on
WSL2 setup, and it is affected by GHSA-vqpg-pvjj-4cmq. Install from `main` with
the [Windows Quickstart](WINDOWS-QUICKSTART.md), or pin an audited commit from
a normal user PowerShell (not an elevated Administrator shell):

```powershell
git clone https://github.com/Osmantic/ODS.git
cd ODS
git checkout AUDITED_COMMIT_SHA
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

Setup installs ODS inside Ubuntu on WSL2; the runtime lives in `~/ods` inside
Ubuntu.

### Desktop Installer (removed)

The repository no longer carries the Tauri desktop installer that lived under
`installer/`. It was never a supported install path: CI did not build it, no
release shipped it, and its installer arguments no longer matched the current
installers. Use the commands above.

## Inspect Before Running

Download and inspect the hosted script instead of piping it directly into a
shell:

```bash
curl -fsSLo get-ods.sh https://install.osmantic.com/ods.sh
less get-ods.sh
ODS_REF=main bash get-ods.sh
```

Compare it to repository `main`:

```bash
curl -fsSLo main-get-ods.sh \
  https://raw.githubusercontent.com/Osmantic/ODS/main/ods/get-ods.sh
cmp get-ods.sh main-get-ods.sh
```

On Windows, clone the commit you intend to run and inspect the entry points
first: `install.ps1` hands off to `ods\installers\windows-portal.ps1` and
`ods\installers\windows\lib\wsl-portal-setup.ps1`.

```powershell
git clone https://github.com/Osmantic/ODS.git
cd ODS
git checkout AUDITED_COMMIT_SHA
notepad .\install.ps1
.\install.ps1
```

## Current Trust Boundary

ODS currently relies on:

- protected and reviewed repository changes reaching the hosted mutable
  bootstrap;
- Osmantic-hosted proxy delivery, GitHub-hosted source, and HTTPS transport;
- release tags or explicit refs for reproducible source selection;
- local generated secrets instead of checked-in default credentials;
- localhost-first service binding by default;
- release validation across zero-prerequisite bootstrap, real hardware
  installs, product behavior, full-model capabilities, and lifecycle recovery.

ODS does not yet publish a complete signed-release or checksum/SBOM chain for
every installer artifact. Users who need strict provenance should install from
an audited commit or internal fork and record the exact commit; no published
tag currently qualifies.

## Provenance Roadmap

The [signed source release pipeline](SIGNED_SOURCE_RELEASES.md) defines the
candidate producer and the separately staged verified consumer. It only creates draft
releases from new, verified signed tags. First-candidate signing, verification
and installation tests remain required before this chain can be called complete.

1. Publish checksums for release installer artifacts.
2. Sign release artifacts and tags with maintainer-controlled signing keys.
3. Publish SBOMs for release artifacts and core container images.
4. Document the exact validation receipt tied to each release candidate.
5. Keep inspect-first and manual source install paths available.

These are roadmap items, not current guarantees.

## Related Validation

- [Release Validation](RELEASE_VALIDATION.md) explains the User Green gates.
- [Validation Matrix](VALIDATION-MATRIX.md) summarizes hardware, distro,
  capability, and lifecycle evidence.
- [Forkability](FORKABILITY.md) explains how downstream operators can fork,
  pin, and independently operate ODS.
- [Offline And Mirroring](OFFLINE_AND_MIRRORING.md) covers preserving release
  refs, images, model artifacts, and validation receipts.
- [Security](../SECURITY.md) documents localhost defaults, LAN tradeoffs, and
  disclosure guidance.
