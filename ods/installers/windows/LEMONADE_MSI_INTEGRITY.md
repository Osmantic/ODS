# Lemonade MSI integrity record

The AMD installer retains Lemonade **10.0.0**, server-only MSI, and compares the downloaded file with the SHA-256 committed in `ods/config/backends/amd.json` before passing it to Windows Installer. A missing/invalid digest, failed download, missing/empty/reparse-point file, read error or digest mismatch leaves Lemonade uninstalled and follows the existing llama-server fallback. Staging uses a unique name and is removed after verification failure or the MSI process attempt. The installer remains per-user.

Reviewed on 2026-09-23:

- [Official release](https://github.com/lemonade-sdk/lemonade/releases/tag/v10.0.0), release ID `295729846`.
- [GitHub release asset metadata](https://api.github.com/repos/lemonade-sdk/lemonade/releases/assets/371629848), asset `lemonade-server-minimal.msi`, created/updated `2026-03-11T15:41:04Z`, size **4,702,208 bytes**.
- [Official artifact](https://github.com/lemonade-sdk/lemonade/releases/download/v10.0.0/lemonade-server-minimal.msi).
- GitHub's separately retrieved asset `digest` was `sha256:a02bfe9a3809e0709098143a003ed755b7ea4aafd813c9fd71137745dbc6422e`. A local SHA-256 of the downloaded bytes matched it exactly.
- `Get-AuthenticodeSignature` inspected those bytes without running them and returned `Valid`, subject `CN=SignPath Foundation, O=SignPath Foundation, L=Lewes, S=Delaware, C=US`.

The GitHub release reports `immutable: false`, and its six published assets include no separate signed checksum manifest. The checked-in digest is the install-time trust anchor; it is not downloaded or regenerated from the candidate MSI during installation. The local signature result supplements the review, but is not an enforced signer policy or a claim that the release asset is immutable. These checks do not qualify the MSI's own behavior, later runtime downloads, installation success or native AMD operation.

`ods/tests/test-windows-lemonade-msi-integrity.ps1` runs only the local-file verifier against inert fixture bytes, including substitution and malformed hashes. It inspects the real installer's AST to bind the contract, staging path, failure guard and process ordering. It never executes the installation branch, `msiexec`, a service, or a network request.

Local results: **16 checks passed on PowerShell 7.6 and Windows PowerShell 5.1**; PSScriptAnalyzer 1.25.0 with repository settings reported no warnings/errors for the changed installer. The complete WSL `test-installer-hardening.sh` contract gate and `git diff --check` also passed. Reparse-point metadata and hash-read failure are simulated in the pure verifier tests; the missing/empty/directory/substituted-file cases use real temporary files. No MSI installation or AMD runtime qualification was performed.
