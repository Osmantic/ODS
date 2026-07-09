# Installer Trust And Provenance

ODS installers set up Docker services, write local config, generate
secrets, and may install missing prerequisites. Treat them like any other
infrastructure installer: inspect the source, pin a release when you want
reproducibility, and keep the default localhost security posture unless you
intentionally expose services to your LAN.

## Install Paths

### Public Linux/macOS Bootstrap

The README one-liner downloads this bootstrap script from GitHub:

```bash
https://raw.githubusercontent.com/Osmantic/ODS/main/ods/get-ods.sh
```

That script:

- detects Linux, WSL, or macOS;
- installs or checks basic prerequisites where supported;
- clones `https://github.com/Osmantic/ODS.git` with sparse
  checkout for the `ods/` product tree;
- copies the runtime product files into `~/ods`;
- runs `./install.sh` from that copied runtime tree.

By default the bootstrap follows `main`. To select a published release tag or
another branch, set `ODS_REF` before running the script:

```bash
ODS_REF=main bash get-ods.sh
```

### Manual Source Install

For the most auditable path, clone a known ref yourself and run the installer
from the checked-out source:

```bash
git clone --depth 1 --branch main https://github.com/Osmantic/ODS.git
cd ODS/ods
./install.sh
```

Use this path when you want to review diffs, pin an exact release tag, or make
local modifications before install.

### Windows PowerShell Install

Windows users should install from a normal user PowerShell, not an elevated
Administrator shell:

```powershell
git clone --depth 1 --branch main https://github.com/Osmantic/ODS.git
cd ODS\ods
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
curl -fsSLO https://raw.githubusercontent.com/Osmantic/ODS/main/ods/get-ods.sh
less get-ods.sh
ODS_REF=main bash get-ods.sh
```

On Windows, clone first and inspect `install.ps1` before running it:

```powershell
git clone --depth 1 --branch main https://github.com/Osmantic/ODS.git
cd ODS\ods
notepad .\install.ps1
.\install.ps1
```

## Current Trust Boundary

ODS currently relies on:

- GitHub-hosted source and HTTPS transport;
- release tags or explicit refs for reproducible source selection;
- local generated secrets instead of checked-in default credentials;
- localhost-first service binding by default;
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
