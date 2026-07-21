# Installer Trust And Provenance

ODS installers set up Docker services, write local config, generate secrets,
and may install missing prerequisites. Treat them like any other infrastructure
installer: inspect the source, use a release or audited commit when you need
reproducibility, and keep the default localhost security posture unless you
intentionally expose services to your LAN.

## Install Paths

### Public Linux/macOS Bootstrap

The canonical one-liner is:

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
layout used by the sparse checkout. The current stable tag, `v2.5.3`, predates that repository layout and must be installed through the manual source path below.
Do not pass `v2.5.3` through `ODS_REF`.

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

For the stable release tag, clone the known ref and run the installer from the
checked-out source:

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

Windows users should install from a normal user PowerShell, not an elevated
Administrator shell:

```powershell
git clone --depth 1 --branch v2.5.3 https://github.com/Osmantic/ODS.git
cd ODS
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

The PowerShell installer writes runtime state under `$env:USERPROFILE\ods` by
default, or `$env:ODS_HOME` if set.

### Desktop Installer

The Tauri desktop installer is a convenience wrapper around the source
installer flow. For maximum provenance control, prefer the manual source
install until you have reviewed the desktop installer build you are using.

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

On Windows, clone first and inspect `install.ps1` before running it:

```powershell
git clone --depth 1 --branch v2.5.3 https://github.com/Osmantic/ODS.git
cd ODS
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
a reviewed tag or internal fork and record the exact commit or release tag.

## Provenance Roadmap

1. Publish checksums for release installer artifacts.
2. Sign release artifacts and tags with maintainer-controlled signing keys.
3. Publish SBOMs for release artifacts and core container images.
4. Record build provenance for desktop installer artifacts.
5. Document the exact validation receipt tied to each release candidate.
6. Keep inspect-first and manual source install paths available.

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
