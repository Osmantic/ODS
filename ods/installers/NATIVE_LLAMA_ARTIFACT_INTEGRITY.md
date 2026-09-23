# Native llama.cpp archive integrity

The Windows Vulkan fallback and macOS Metal installer now resolve downloads
through [one committed manifest](native-llama-artifacts.json). It preserves the
existing versions: Windows `b8248`, macOS `b8210`, and each platform's Gemma
override `b9014`. An unknown selection, missing/malformed digest, read error,
empty/nonregular file or SHA-256 mismatch prevents archive extraction and
runtime startup. A version override must have its own reviewed manifest entry.

The expected digest is read from the checkout, never generated from the
candidate archive or obtained from live metadata during installation. Downloads
use new private staging directories; the former predictable temporary caches
are not consumed or deleted. Windows restricts staging to the current user and
rejects reparse files; macOS uses a private `mktemp` directory and rejects
symlinks and multiply linked archives. Staging is cleaned after success or
failure. Verification precedes ZIP/tar parsing, publication, executable mode
changes and quarantine removal.

The existing reuse policy for an already-present owner-installed binary is
preserved. Such a binary is **not reverified or attested by this archive
manifest**. The manifest does not qualify runtime behavior, CPU/GPU support,
model compatibility, installed-file state after publication or supplier trust.

On macOS, only selected curl transport/HTTP errors can retain the previous
Homebrew fallback; the installer reports that separate package-manager path.
They are curl codes 6, 7, 18, 22, 28, 52, 55 and 56. Missing/invalid pins, byte
substitution, TLS certificate errors, local write errors and extraction errors
stop the installer without silently switching to Homebrew. Homebrew content
remains outside this archive manifest.

## Reviewed metadata

On 2026-09-23, GitHub's public release/asset API reported the following records.
All four digests, names, URLs, sizes and IDs were also checked independently
against each asset's metadata. No release binaries were downloaded or executed
for this change.

| Platform | Tag | Asset metadata | SHA-256 |
| --- | --- | --- | --- |
| Windows Vulkan x64 | `b8248` | [asset 370031732](https://api.github.com/repos/ggml-org/llama.cpp/releases/assets/370031732) | `6548bcef5dd18453dd7b720217d0b9d46ee83d9d7ec2b116f1b68482cc3ce6ca` |
| Windows Vulkan x64 | `b9014` | [asset 411572760](https://api.github.com/repos/ggml-org/llama.cpp/releases/assets/411572760) | `6cd4bc7a44256e674458b0c5ea2ae3461dca29ee87876c8d410ecc78652a3b0f` |
| macOS ARM64 | `b8210` | [asset 367774262](https://api.github.com/repos/ggml-org/llama.cpp/releases/assets/367774262) | `8cc228499f05adb69b92462f8060448bec75a7ba406f03c1fca8e628b4ff5c91` |
| macOS ARM64 | `b9014` | [asset 411571704](https://api.github.com/repos/ggml-org/llama.cpp/releases/assets/411571704) | `565aecda0838daa433f363ae9a1c9ed6c94831de3fd093cb08179a5a3fd4f22d` |

The release API reports these releases as mutable (`immutable: false`). The
checked-in hash is the content-integrity boundary; no signature or publisher
identity verification is claimed. The manifest retains the asset and release
IDs, update timestamps, URLs and sizes for review. Updating a version requires
reviewing its artifact metadata and adding the matching platform/tag pin.

## Validation and limits

- PowerShell 7 and Windows PowerShell 5.1 each passed 64 local-file/AST checks,
  including both Windows versions, substitution, missing/invalid hashes,
  empty/missing/directory/reparse files, cleanup on failure, old cache isolation,
  and literal `ODS[12]` installation beside intact `ODS1`/`ODS2` sentinels.
  The reparse-file test simulates metadata so it needs no symlink privilege.
- The shared Windows ZIP helper now resolves its cleanup target, uses literal
  filesystem paths and the .NET ZIP API. `Expand-Archive`'s destination parameter
  still interpreted brackets even with a literal archive input. Both OpenCode
  substitution and Lemonade MSI fixtures also passed on PowerShell 5.1 and 7.
- Nine WSL/Python tests exercised the manifest and actual macOS download branch
  with fake network responses and inert tar archives. Both tags, hash mismatch,
  unknown selection, missing hash, symlink/hardlink rejection, TLS/local-I/O
  failures, malformed verified archive, explicit transport fallback, old cache
  isolation and owner-binary reuse are covered. No fixture binary was run.
- [The dedicated workflow](../../.github/workflows/test-native-llama-artifacts.yml)
  runs these offline fixtures on Linux/macOS and both Windows shells. A workflow
  definition is not evidence of a completed hosted run. Native macOS, actual
  archive layout/installation and GPU/runtime qualification remain unexecuted.

Run locally from the repository root:

```sh
python3 -m unittest discover -s ods/tests -p test_native_llama_artifacts.py -v
```

```powershell
pwsh -NoProfile -File ods/tests/test-windows-native-llama-integrity.ps1
powershell.exe -NoProfile -File ods/tests/test-windows-native-llama-integrity.ps1
```
