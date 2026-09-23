# Secret scan review (PB-001)

Reviewed on 2026-09-23 against public-beta base `4099cffc874f80b45f589504ebb29d4dbdcbd5c6`.

Gitleaks 8.30.1 now extends all built-in rules and the two custom Langfuse rules. No whole path is excluded. Historical exceptions below bind a commit, path, rule and line; the same location in a new commit is scanned again. Synthetic GitHub/GitLab/private-key/Langfuse canaries also run inside a previously excluded installer path.

The redacted full-history scan produced 41 candidates. Each exact source occurrence was inspected and classified below. The seven bundled occurrences were matched to the visible source; `verify-pixel-bundle.py` confirms source/bundle equality. Re-scanning after these exceptions returned zero **unreviewed** candidates in both histories. This does not prove that credentials have never leaked.

Repository maintainers still need to sign off this adjudication and attach provider-side retirement evidence for the historical LiveKit incident documented in SECURITY_AUDIT.md. Existing incident exceptions are not proof of revocation. No credential values are included here.

The documentation repair regenerated the single-commit public bundle at
`69f4ad0bd062fe006e9d5b473a04b9a38eff8533`. Its seven scanner occurrences
match the same rule/path/line records from the preceding bundle
`817214d5ec3d8aa583fe50c1dc7561f3c1a16dff`; all seven containing files are
byte-identical to their previously reviewed public source. Separate exact
fingerprints were recorded for the new commit. No file, rule, or bundle-wide
exception was added, and source/bundle equality was verified again.

| Commit | Path and line | Rule | Classification |
|---|---|---|---|
| `1d6038ef9679b89632f5fa4765ad1d6347786711` | `ods/extensions/library/services/keycloak/compose.yaml:19` | `generic-api-key` | Database connection URL without credentials |
| `1d6038ef9679b89632f5fa4765ad1d6347786711` | `ods/vendor/pixel/RELEASE-MANIFEST.json:306` | `generic-api-key` | Authentication-cache policy description |
| `1d6038ef9679b89632f5fa4765ad1d6347786711` | `ods/vendor/pixel/scripts/generated/release.env:17` | `discord-api-token` | Published package SHA-256 integrity digest |
| `1d6038ef9679b89632f5fa4765ad1d6347786711` | `ods/vendor/pixel/security-evals/agent-comparison/task-battery-v1.json:179` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `1d6038ef9679b89632f5fa4765ad1d6347786711` | `ods/vendor/pixel/tests/test_source_audit.py:178` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `1d6038ef9679b89632f5fa4765ad1d6347786711` | `ods/vendor/pixel/tests/test_ops_broker.py:177` | `curl-auth-header` | Deliberate test/scanner fixture or non-secret test identifier |
| `1d6038ef9679b89632f5fa4765ad1d6347786711` | `ods/vendor/pixel/tests/test_frontier_broker.py:1309` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `1d6038ef9679b89632f5fa4765ad1d6347786711` | `ods/vendor/pixel/tests/test_mesh_peer.py:2879` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `24c8b714a4fa1eb022b9c5ff5a4e1109c29ef630` | `ods/extensions/services/dashboard/src/lib/pixelConversationWriter.test.js:31` | `generic-api-key` | Browser localStorage key, not authentication material |
| `bb21a9cf1fb2df67dfac195f721662f57fd230b4` | `ods/extensions/services/dashboard/src/pages/Pixel.legacy-history.test.jsx:6` | `generic-api-key` | Browser localStorage key, not authentication material |
| `bdd6f35727c2beb44f3952491cc57e7a07d3ded9` | `ods/extensions/services/dashboard/src/lib/pixelConversations.test.js:11` | `generic-api-key` | Browser localStorage key, not authentication material |
| `bdd6f35727c2beb44f3952491cc57e7a07d3ded9` | `ods/extensions/services/dashboard/src/lib/pixelConversations.test.js:43` | `generic-api-key` | Browser localStorage key, not authentication material |
| `c611570c493ca448bd08a45c936dd5e03ccffc86` | `ods/extensions/services/dashboard/src/lib/pixelSavedPrompts.js:1` | `generic-api-key` | Browser localStorage key, not authentication material |
| `54292586f9da910e6a3e3121781a317f69e864cc` | `ods/extensions/services/dashboard/src/components/PixelCommandSearch.test.jsx:56` | `generic-api-key` | Browser localStorage key, not authentication material |
| `5ed81190fb88e545fdc138b726f62279b6cc0ae4` | `ods/extensions/services/dashboard/src/lib/pixelConversations.js:2` | `generic-api-key` | Browser localStorage key, not authentication material |
| `ef5f6e60cad0bce05a965811d6dc59bfaf217649` | `ods/tests/pixel_inference/test_advice_process.py:100` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `7963d4f799873c32756e06483022bb4d766f753b` | `ods/extensions/services/model-router/tests/test_router.py:343` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `4b257f2ee04bd6aa4f0ec9ae98a5daee4a0dffcf` | `ods/extensions/services/model-router/tests/test_router.py:946` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `39164d8c5bc3306dd4e7ac3e884dcc8574b9930a` | `ods/tests/test-perplexica-entrypoint.py:203` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `0fba8b3cd452afa3575128e427f989d90e15e142` | `dream-server/extensions/services/ape/tests/conftest.py:19` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `676dc575c623a32114b77d1f9c4e3252111dd673` | `dream-server/extensions/services/dreamforge/rust/crates/runtime/src/secret_scanner.rs:200` | `jwt` | Deliberate test/scanner fixture or non-secret test identifier |
| `dd97e51445ea4d01fd6f291ce384e46988d1d584` | `resources/dev/extensions-library/services/gitea/README.md:36` | `curl-auth-header` | Documentation command with a named placeholder |
| `bc47367431025198fcbea19a54abcde8c30847fd` | `dream-server/extensions/services/privacy-shield/tests/test_pii_scrubber.py:125` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `bc47367431025198fcbea19a54abcde8c30847fd` | `dream-server/extensions/services/privacy-shield/tests/test_pii_scrubber.py:131` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `bc47367431025198fcbea19a54abcde8c30847fd` | `dream-server/extensions/services/privacy-shield/tests/test_pii_scrubber.py:181` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `4ba4a3779264f44b247af06fbad391c1f4d38269` | `dream-server/extensions/services/litellm/README.md:89` | `curl-auth-header` | Documentation command with a named placeholder |
| `9a1e87ce94083fb9696ec1df6c926ec77397b38e` | `resources/dev/extensions-library/services/privacy_shield/pii_scrubber.py:149` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `9a1e87ce94083fb9696ec1df6c926ec77397b38e` | `resources/dev/extensions-library/workflows/comfyui/README.md:32` | `curl-auth-header` | Documentation command with a named placeholder |
| `9a1e87ce94083fb9696ec1df6c926ec77397b38e` | `resources/dev/extensions-library/workflows/comfyui/README.md:64` | `curl-auth-header` | Documentation command with a named placeholder |
| `9a1e87ce94083fb9696ec1df6c926ec77397b38e` | `resources/dev/extensions-library/workflows/flowise/README.md:30` | `curl-auth-header` | Documentation command with a named placeholder |
| `9a1e87ce94083fb9696ec1df6c926ec77397b38e` | `resources/dev/extensions-library/workflows/langflow/README.md:30` | `curl-auth-header` | Documentation command with a named placeholder |
| `1f9e6dae1a91ff274dac2f580996ae9f8d60f414` | `resources/dev/extensions-library/services/privacy_shield/pii_scrubber.py:149` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `1f9e6dae1a91ff274dac2f580996ae9f8d60f414` | `resources/dev/extensions-library/workflows/comfyui/README.md:32` | `curl-auth-header` | Documentation command with a named placeholder |
| `1f9e6dae1a91ff274dac2f580996ae9f8d60f414` | `resources/dev/extensions-library/workflows/comfyui/README.md:64` | `curl-auth-header` | Documentation command with a named placeholder |
| `1f9e6dae1a91ff274dac2f580996ae9f8d60f414` | `resources/dev/extensions-library/workflows/flowise/README.md:30` | `curl-auth-header` | Documentation command with a named placeholder |
| `1f9e6dae1a91ff274dac2f580996ae9f8d60f414` | `resources/dev/extensions-library/workflows/langflow/README.md:30` | `curl-auth-header` | Documentation command with a named placeholder |
| `9539512e06bef98b9276e339ef3cb2d6273e8f1f` | `dream-server/installers/macos/lib/env-generator.sh:80` | `generic-api-key` | Empty API key next to a comment; no embedded value |
| `5c5151fe050ac570fb022ccb99d86bda93dfedb2` | `dream-server/installers/windows/lib/env-generator.ps1:115` | `generic-api-key` | Empty API key next to a comment; no embedded value |
| `48a1d87d3d155359fb23d7a65d1db55b036b7508` | `dream-server/config/searxng/settings.yml:3` | `generic-api-key` | Historical deterministic development placeholder; not an installation secret |
| `6a5047fd3f7cc0369416d14289740b8c3f02dd06` | `dream-server/privacy-shield-offline/pii_scrubber.py:146` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |
| `6a5047fd3f7cc0369416d14289740b8c3f02dd06` | `dream-server/privacy-shield/pii_scrubber.py:149` | `generic-api-key` | Deliberate test/scanner fixture or non-secret test identifier |

Reproduce:

```sh
python3 .github/scripts/check-secret-scan.py
gitleaks git . --config .gitleaks.toml --log-opts HEAD --redact --report-format json --report-path gitleaks-report.json
python3 .github/scripts/scan-git-bundles.py
python3 ods/scripts/verify-pixel-bundle.py
```

Scanner configuration reference: https://github.com/gitleaks/gitleaks#configuration
