# Installer Trust And Provenance

ODS installers set up Docker services, write local config, generate
secrets, and may install missing prerequisites. Treat them like any other
infrastructure installer: inspect the source, pin a release when you want
reproducibility, and keep the default localhost security posture unless you
intentionally expose services to your LAN.

## Install Paths

### Public Linux/macOS Bootstrap

The canonical README one-liner downloads the ODS bootstrap from the hosted
Osmantic endpoint:

```bash
curl -fsSL https://install.osmantic.com/ods.sh | bash
```

For the documented `curl` request, the endpoint serves the plain-text
`ods/get-ods.sh` bootstrap. It is a distribution URL, not a source-version
selector. The bootstrap:

- detects Linux, WSL, or macOS;
- installs or checks basic prerequisites where supported;
- clones `https://github.com/Osmantic/ODS.git` with sparse
  checkout for the `ods/` product tree;
- copies the runtime product files into `~/ods`;
- on Linux and macOS, renders the selected Docker Compose stack and rejects
  unowned host-port, container-name, network-name, and named-volume claims
  before Docker service launch;
- runs `./install.sh` from that copied runtime tree.

Without `ODS_REF`, Git uses the repository's default branch, currently `main`.
The hosted one-liner therefore tracks `main`; it does not pin the current stable
release.

`ODS_REF` can select a branch or tag only when that ref contains the current
`ods/` product-tree layout used by the bootstrap's sparse checkout. For example:

```bash
curl -fsSL https://install.osmantic.com/ods.sh | ODS_REF=main bash
```

The current stable tag, `v2.5.3`, predates that repository layout and must be
installed through the manual source path below. Do not pass `v2.5.3` through
`ODS_REF`. Use the manual source path for stable tags with the earlier layout
and for exact audited commits.

The direct raw GitHub URL,
`https://raw.githubusercontent.com/Osmantic/ODS/main/ods/get-ods.sh`, exposes
the bootstrap source from `main`. It is an alternate transport for the
bootstrap, not a separate stable release channel.

### Manual Source Install

For a stable release tag, clone the known ref yourself and run the installer
from the checked-out source:

```bash
git clone --depth 1 --branch v2.5.3 https://github.com/Osmantic/ODS.git
cd ODS
./install.sh
```

For an exact audited commit, use a full clone so Git can resolve the commit:

```bash
git clone https://github.com/Osmantic/ODS.git
cd ODS
git checkout AUDITED_COMMIT_SHA
./install.sh
```

Use the manual path when you want to review diffs, pin an exact commit, make
local modifications, or avoid trusting the hosted bootstrap delivery path.

### Windows PowerShell Install

Windows users should install from a normal user PowerShell, not an elevated
Administrator shell:

```powershell
git clone --depth 1 --branch v2.5.3 https://github.com/Osmantic/ODS.git
cd ODS
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

The PowerShell installer writes runtime state under
`$env:USERPROFILE\ods` by default, or `$env:ODS_HOME` if set.

### Desktop Installer

The Tauri desktop installer is a convenience wrapper around the source
installer flow. For maximum provenance control, prefer the manual source
install above until you have reviewed the desktop installer build you are using.

## Inspect Before Running

If you do not want to pipe a remote script directly into a shell, download and
inspect it first:

```bash
curl -fsSLo get-ods.sh https://install.osmantic.com/ods.sh
less get-ods.sh
ODS_REF=main bash get-ods.sh
```

On Windows, clone first and inspect `install.ps1` before running it:

```powershell
git clone --depth 1 --branch v2.5.3 https://github.com/Osmantic/ODS.git
cd ODS
notepad .\install.ps1
.\install.ps1
```

## Current Trust Boundary

ODS currently relies on:

- Osmantic-hosted bootstrap delivery, GitHub-hosted source, and HTTPS transport;
- release tags or explicit refs for reproducible source selection;
- local generated secrets instead of checked-in default credentials;
- localhost-first service binding by default;
- fail-closed Linux and macOS Docker install conflict detection with a
  structured local report;
- release validation across zero-prereq distro bootstrap, real hardware
  installs, product behavior, full-model capabilities, and lifecycle recovery.

ODS does not yet publish a full signed-release or checksum/SBOM chain
for every installer artifact. That is the next stronger trust model. Until then,
users who need strict provenance should install from a reviewed tag or internal
fork and record the exact commit or release tag they deployed.

## Provenance Roadmap

The current installer trust model is source-visible and ref-pinnable. The next
steps toward a stronger binary and release provenance chain are:

1. Publish checksums for release installer artifacts and document how to verify
   them before running installers.
2. Sign release artifacts and tags with maintainer-controlled signing keys.
3. Publish SBOMs for release artifacts and core container images.
4. Record build provenance for desktop installer artifacts.
5. Document the exact validation receipt tied to each release candidate.
6. Keep the inspect-first and manual source install paths available even after
   signed artifacts exist.

These are roadmap items, not current guarantees. Release notes should clearly
say which provenance pieces are present for a given release.

## Related Validation

- [Release Validation](RELEASE_VALIDATION.md) explains the User Green gates.
- [Validation Matrix](VALIDATION-MATRIX.md) summarizes the hardware, distro,
  capability, and lifecycle evidence.
- [Forkability](FORKABILITY.md) explains how downstream operators can fork,
  pin, and independently operate ODS.
- [Offline And Mirroring](OFFLINE_AND_MIRRORING.md) covers preserving release
  refs, images, model artifacts, and validation receipts.
- [Security](../SECURITY.md) documents localhost defaults, LAN tradeoffs, and
  disclosure guidance.
