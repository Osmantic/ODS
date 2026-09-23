# OpenCode installer artifact review

Reviewed on 2026-09-23 for PB-018, using the pinned OpenCode v1.2.18 release.

The [upstream installer at v1.2.18](https://github.com/anomalyco/opencode/blob/v1.2.18/install#L89-L157) selects baseline binaries when AVX2 is unavailable, distinguishes Linux musl from glibc, and selects native ARM64 under macOS Rosetta. The [release build source](https://github.com/anomalyco/opencode/blob/v1.2.18/packages/opencode/script/build.ts#L57-L114) defines the baseline targets with `avx2: false` and passes those targets to Bun's compiler. ODS now consistently selects these baseline assets for x64, retains libc selection, and detects Rosetta using `sysctl.proc_translated`.

The hashes below were checked against the [publisher release API](https://api.github.com/repos/anomalyco/opencode/releases/tags/v1.2.18). Five archives were also downloaded and independently hashed before listing their contents. Each contained its expected executable at the archive root, plus three source maps; no enclosing directory is required by the installer. Linux ARM64 hashes were checked against publisher metadata, but their archives were not downloaded in this review.

| Asset | SHA-256 | Archive validation |
| --- | --- | --- |
| `opencode-linux-x64-baseline.tar.gz` | `55fd8ed686d4b897f2ff0972fac1ffc5e6d0632aec6f443e114aa93e84b720da` | Hash and root `opencode` verified; actual helper installation and version check passed in disposable Linux container |
| `opencode-linux-x64-baseline-musl.tar.gz` | `1a177993e137e4e71be94dc6bb3a2371fd6c461a2671c87b9151b1b054bb6940` | Hash and root `opencode` verified |
| `opencode-linux-arm64.tar.gz` | `cd8b3cd13bef12e29f32e0f32e5ca48e29159cf6cd32ddee8b3be97438a0242c` | Publisher metadata only |
| `opencode-linux-arm64-musl.tar.gz` | `354b60969c70cecf1db7be9fc8e68ed4a52d2e6247c61bfa0e5ac862fff99502` | Publisher metadata only |
| `opencode-darwin-x64-baseline.zip` | `262e1cd86e6df5ec1f6199454429b1d04542dc3e9f30281ae8d53553d0623de7` | Hash and root `opencode` verified |
| `opencode-darwin-arm64.zip` | `7dc1eb25b79a85ec882df9560a2b1d84927a6f9981165a56f562ab704a312529` | Hash and root `opencode` verified |
| `opencode-windows-x64-baseline.zip` | `fa6c3bcf13670fd7c404e875a026decc4df420d5fd0456bda08f7948a689de4e` | Hash and root `opencode.exe` verified; actual phase checksum guard and isolated extraction passed on Windows |

The Windows baseline digest replaces the earlier normal-x64 archive digest. Selection and checksum must be updated together when reviewing another release.

Validation completed:

- `test-opencode-artifact-selection.sh`: ten selections covering x64 baseline, glibc/musl, ARM64 aliases, unavailable/false/true Rosetta detection, and an unsupported target rejected before download. The musl fixture exits nonzero from `ldd --version`, exercising that compatibility case.
- `test-verified-download.sh`: changed bytes, insecure URLs, malformed digests and symlink destinations rejected before use.
- `test-macos-opencode-verified.sh`: the macOS integration uses the verified helper and retains an existing installation.
- `test-windows-opencode-integrity.ps1`: substituted bytes rejected before ZIP parsing; with `-ReviewedArchive` pointing to the downloaded baseline ZIP, the real phase checksum guard accepts and extracts the archive into a temporary test directory. The executable is never launched on the host.
- Existing Linux OpenCode path/opt-in tests and all 26 Windows OpenCode configuration checks passed. Bash syntax, PowerShell parsing and targeted `git diff --check` passed.
- In an automatically removed container using `node@sha256:48e4b67d85f87bd551df43704e24d252f56cc5f8e9718841aace50f19948f0f9`, the actual download verifier and install helper accepted the previously downloaded Linux baseline archive and `opencode --version` returned `1.2.18`. Network access was disabled; OpenCode emitted an expected models.dev connection warning before printing its version.

This verifies artifact selection, integrity, layout and a Linux version smoke test. It does not qualify every older CPU, establish support for all x64 instruction sets, or replace native macOS, musl, ARM64 and Windows runtime qualification. Broader PB-018 supplier/download acceptance remains open.
