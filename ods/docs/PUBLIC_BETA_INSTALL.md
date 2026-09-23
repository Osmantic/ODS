# Installing a verified public-beta candidate

The ordinary hosted bootstrap follows `main`. It is **not** an installation
command for this beta. A checkout of `public-beta` also moves as changes merge.
Until maintainers publish a verified install kit for a frozen commit, there is
no approved immutable public-beta quick-start to copy from this repository.

## Preparing a release candidate

After the candidate changes are committed and its checks pass, a maintainer can
prepare (without publishing or installing) an install kit:

```sh
python3 ods/scripts/build-beta-install-kit.py \
  --commit FULL_40_CHARACTER_REVIEWED_COMMIT \
  --release-url https://github.com/Osmantic/ODS/releases/download/REVIEWED_BETA_TAG \
  --output /path/outside-the-repository/beta-install-kit
```

The placeholders deliberately require a release decision. The tool rejects a
branch name or a non-ODS release URL. It archives the specified committed tree,
never uncommitted changes, and produces:

- One source ZIP bound to that commit.
- Linux/macOS and Windows launchers containing its URL and SHA-256 digest.
- `release.json` identifying the beta channel, commit, tree, source archive and
  installer/launcher checksums.
- `SHA256SUMS` covering all generated artifacts.

Review and sign the manifest/checksum set through the project's release process.
Publish the complete kit at the declared tag URL. Signing and publishing are
separate maintainer actions; generating a kit is not release approval.

## Testing the published kit

Download the launchers from the exact published release, and independently
verify them against the reviewed/signed checksum set before running them. A
checksum downloaded from the same untrusted location alone is not an independent
authenticity check. On Linux/macOS, run `bash install-beta.sh --verify-only`;
on Windows, run `./install-beta.ps1 -VerifyOnly` from PowerShell. Removing the
verify-only option invokes the platform installer after verification.

The launchers refuse modified archive or installer bytes before executing the
installer. They retain the extracted source and `ods/beta-install-receipt.json`
containing the exact source URL, requested and resolved commit, tree and
checksums. Preserve this receipt with install test results. Unix requires curl
and either unzip or Python 3; the existing platform prerequisites still apply.

The kit verifies the ODS source boundary. It does not claim that every downstream
package, container, model or tool is already pinned; those inputs remain a
separate supply-chain acceptance requirement.

## Required release evidence

Run the generated launchers on clean Windows, Linux and macOS machines, record
their receipts and complete the service/hardware checks at the exact candidate.
Offline regression tests execute a generated launcher with a disposable fixture
on each CI platform and verify that tampered bytes cannot reach its installer.
They are not a substitute for a real clean-machine install of the release.

## Current implementation evidence

Local verification on 2026-09-23 passed six fixture tests on Windows (two Unix
permission tests skipped) and all eight on WSL/Linux. Fixtures retain the real
root wrappers and `.gitattributes`: installer hashes cover the archived CRLF
PowerShell bytes, Python ZIP extraction preserves executable/source modes, and
private staging does not change the caller's installer umask. The tests also
check tampered downloads and verify-only use without the build checkout.

These runs used disposable stub installers and a mocked download. No native
product install, macOS execution, hardware qualification, release signing or
publication was performed. Windows download execution-policy behavior and the
permissions inherited from a user's TEMP directory still need clean-host
verification. The CI matrix declares Windows, Linux and macOS runs; its presence
alone is not evidence that those runs passed for a frozen release commit.
