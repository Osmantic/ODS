# Public-beta audit remediation

Review date: 2026-09-23. Audit source: `ODS-public-beta-audit-2026-09-23.md`.
The audit examined `0e01537095a46f312aa0b7cc392c6d8f0d730f2c`; these changes
started from `4099cffc874f80b45f589504ebb29d4dbdcbd5c6`. This is a working-tree
evidence record, **not release approval or evidence for a frozen candidate**.
Local fixture tests, image scans and documentation changes must not be presented
as three-platform clean-install qualification.

The latest local checkpoint includes the [legacy recipe evidence](EXTENSION_PROVENANCE_EVIDENCE.md),
[model catalog implementation](MODEL_TERMS_CATALOG.md) and
[external-link results](EXTERNAL_DOC_LINKS.md). The remaining acceptance column
below is the release checklist; a passing structural gate does not close it.

## Findings and remaining acceptance

| Finding | Work completed locally | Remaining acceptance |
| --- | --- | --- |
| PB-001: secret scanning | Enabled built-in Gitleaks rules, removed broad path exclusions, added canaries in previously excluded paths, and scan embedded Git bundles. Reviewed new findings individually. | Maintainer sign-off on the [redacted ledger](SECRET_SCAN_REVIEW.md), provider-side retirement evidence for historical LiveKit credentials, and scan at the final candidate SHA. Zero unreviewed findings is not proof of historical revocation. |
| PB-002: wallpaper rights | Removed the three former color JPEGs and integrated the approved circular AI variants. Recorded the individual Forest photograph source and license. The owner explicitly deferred further wallpaper replacements and requested keeping the other current versions. | [Asset notices](../extensions/services/dashboard/ASSET-NOTICES.md) retain the unresolved rights boundary. Individual provenance and rights review for the retained images remain pending; deferring replacement and AI generation do not establish clearance. |
| PB-003: mixed licensing | Corrected blanket contribution/fork language and marked the old Pixel snapshot as historical; preserved existing license texts. | Rights holders must confirm inbound grants, the intended Pixel distribution terms, and any necessary license changes. No license grant was invented. |
| PB-004: Python/runtime advisories | Updated aiohttp/idna and APE dependencies; hash-locked APE requirements, pinned Python base images and removed build-only package tools from runtime images. Reviewed the Datasette advisory against its affected versions. Corrected credential fixtures: all 150 Pixel Edge tests and 39 subtests pass without relaxing runtime guards. | Distro high findings without published fixes require a reviewed applicability decision or upstream remediation. See [dependency evidence](DEPENDENCY_AUDIT_EVIDENCE.md) and [Edge test evidence](PIXEL_EDGE_TEST_EVIDENCE.md). |
| PB-005: internal operational records | Replaced identified public host/session diaries with product and acceptance documentation; removed personal operational identifiers from those records. | Repeat repository-wide sensitive-metadata review at the final candidate; sanitizing current files does not erase Git history. |
| PB-006: branch governance | Read-only inspection confirmed `public-beta` had no branch protection. | A repository administrator must configure and verify required checks/reviews and restricted bypass. No remote protection settings were changed by this work. |
| PB-007: documentation links | Repaired public links and the Pixel export generators together; omitted historical evidence is explicitly marked unavailable. Added the repository-wide local-link gate, nine parser regressions and 11 vendor documentation tests. Rebuilt and verified the matching single-commit local Pixel bundle. Added a checksum-pinned anonymous external-link checker and bounded CI job; corrected a confirmed LocalAI 404. See [documentation evidence](PUBLIC_DOC_LINK_EVIDENCE.md). | The hosted external scan completed with unresolved links; rate limits, access denials and timeouts are not verified successes. Complete and review that check at the frozen candidate; no blanket domain/status exemptions were introduced. |
| PB-008: contributor records | Added an auditable [contributor ledger](CONTRIBUTOR-LEDGER.md) covering the named beta authors and preserved raw identity distinctions. | Maintainers/contributors must verify any identity aliases. No guessed mailmap or history rewrite was applied. |
| PB-009: unsupported claims | Removed unsupported recognition/growth claims from the identified public pages. | Editorial review of the final public tree; restore claims only with durable primary evidence. |
| PB-010: dashboard advisories | Updated React Router to 7.18.4 and patched development tools within compatible ranges, including Vite 7.3.6. Full and production official-registry npm audits return zero findings. All 1,626 dashboard tests, lint, build and a fresh npm 10 lock installation passed. See [dependency evidence](DEPENDENCY_AUDIT_EVIDENCE.md). | Run required CI and current advisory checks at the frozen candidate; zero reported findings is not a guarantee against future vulnerabilities. |
| PB-011: mutable workflow inputs | Pinned the 11 reported Action refs and 12 CI image indices, with digest records and regression checks. Completed hash locks for all direct root-workflow pip installs, including the 13 remaining service/type-check commands. The new gate inventories 32 direct commands / 52 matrix invocations and rejects unverified installs. Eleven fresh Linux venvs passed installs, imports and dependency checks. Test-only recipe/PowerShell corrections passed 187 recipe cases, 97 related contracts and 17 parser/fixture cases. The integrated API suite now passes 4,206 tests with three explicit skips against stable files. See [Python inputs and limits](../../.github/requirements/README.md). | Direct pip coverage does not audit Action internals, arbitrary scripts, Dockerfiles or shipped nested workflow templates. Native macOS, hosted CI and live Postgres remain unexecuted. These changes do not make every supply-chain input immutable. |
| PB-012: whitespace/lint | Removed 162 whitespace findings from 11 Python files with equivalent ASTs; preserved required whitespace in two digest-bound format patches. Enabled React's JSX usage analysis, removed obsolete suppressions and genuinely unused bindings/imports. Dashboard lint reports zero errors/warnings and enforces `--max-warnings 0`; the latest local run passed all 1,626 tests and the production build. | Required CI and the full whitespace gate must pass again at the final candidate. No warning baseline or blanket suppression was introduced. |
| PB-013: recipe/model provenance | Corrected AudioCraft's noncommercial-model description. Backfilled the 34 legacy recipe records with notices and explicit gaps, added provenance validation, and removed XTTS's automatic terms acceptance. All 57 primary model references are pinned, including two repaired Gemma artifacts. [Factual license review](MODEL_LICENSE_REVIEWS.json) and its [supplement](MODEL_LICENSE_REVIEWS_FOLLOWUP.json) cover 41 exact chains (40 permissive with conditions, one restricted); fourteen retained notice files and 28 source/evidence regressions preserve the binding. [Dashboard, Hugging Face import and direct host downloads](MODEL_TERMS_CATALOG.md) require acknowledgement of the current artifact and terms. [Installer GGUF downloads and background retries](../installers/MODEL_DOWNLOAD_REVIEW.md) enforce the same contract with explicit review receipts; Windows/WSL fixtures pass. | Sixteen model reviews remain unassessed; resolve conflicts and missing terms. Complete the embedding/speech/image download inventory and qualify the final candidate on native machines. Acknowledging pending information does not complete its review, grant rights or accept publisher terms. Full downloads and runtime checks for the two replacement artifacts remain unexecuted. |
| PB-014: stale AI triage | Replaced stale prompt facts and model-directed command output with a bounded structured inference contract. | Verify the final deployed manual workflow and permissions. See [AI workflow boundaries](../../.github/AI-WORKFLOWS.md). |
| PB-015: paid AI/write authority | Public events no longer launch paid inference. Maintainer dispatch, bounded input/output, rate limits, revision binding, separated inference/publishing and deterministic label/comment application are implemented. Adversarial tests pass. | Live maintainer-controlled qualification remains; no paid API invocation was performed during local tests. Automated AI code-fix behavior was removed, not silently retained with the same authority. |
| PB-016: installer logs | Default logs now use private directories; safe overrides retain canonical paths. Guards reject special files, hardlinks, foreign ownership and unsafe ancestors, and cover derived installer logs. All 19 adversarial log checks and three local-build contracts passed on Linux/WSL, alongside the previously recorded 51 validator checks. Isolated user-bus and inherited-FD regressions pass. The CLI macOS empty-log regression was corrected, with all nine routing/log cases passing on Linux fixtures. See [log evidence and limits](INSTALLER_LOGS.md). | Native macOS filesystem/stat behavior and real installer execution still require qualification at the final candidate. The isolated macOS helper fixture ran on Linux; same-user/root interference and every long-lived service log are outside this guard's claim. |
| PB-017: beta identity | Added a commit-bound kit generator with archive/installer checksums, receipts and tamper-rejection tests. Beta guides distinguish the ordinary main bootstrap from a future frozen beta kit. | Publish a reviewed/signed immutable kit for the final candidate and collect real clean-install receipts on Windows, Linux and macOS. See [beta installation](PUBLIC_BETA_INSTALL.md). |
| PB-018: downloaded execution | Added a fail-closed SHA-256 downloader and reviewed immutable Docker/NodeSource scripts. Linux/macOS OpenCode use reviewed artifact pins; Windows verifies the pinned ZIP before parsing/extraction. Claude/Codex require explicit opt-in, exact versions, a complete npm integrity lock, disabled lifecycle scripts, and an installation receipt. [Python locks](../installers/python-deps/README.md) cover the host/download bootstraps; substituted wheels were rejected at all six extra boundaries. The [Windows Lemonade MSI](../installers/windows/LEMONADE_MSI_INTEGRITY.md) verifies a reviewed digest before execution, with 16 checks on PowerShell 7 and 5.1. [Native llama.cpp archives](../installers/NATIVE_LLAMA_ARTIFACT_INTEGRITY.md) now use one reviewed four-artifact manifest, private staging and verification before extraction; 64 checks passed in each Windows shell and nine macOS-boundary/manifest tests passed under WSL. Integrity failures cannot silently fall back to Homebrew. | Complete native macOS/Windows CLI qualification and the download inventory. Native macOS and Python 3.8 were not executed in the additional bootstrap validation; 3.8 compatibility is represented by reviewed resolution markers. OS package-manager inputs, container/model downloads and reused owner-installed tools remain outside these locks. Source/asset hashes do not establish publisher trust or actual installation success. See [optional CLI installation](../installers/ai-clis/README.md); PB-018 remains partial and no complete supply-chain qualification is claimed. |

## Evidence boundaries

- September 23 live Windows/Ubuntu 24.04 WSL checkpoint: the Core Only + Pixel
  installer completed at `7c6128e0`, reusing the owner's existing 4B runtime.
  Docker and Ubuntu storage were migrated to D: with backups retained. This was
  a reinstallation on an existing computer, not a fresh operating-system image.
  Stale access-coordinator state from the former deployment required explicit
  preservation/recovery before the new sandboxed runtime proof succeeded.
- The real Portal returned `OK`, then created a Python program, executed two
  assertions and wrote its JSON result. The four tool calls reported no failures
  or blocks; an independent execution inside the sandbox passed both assertions.
  These two cases do not establish arbitrary-repository installation reliability.
- That live test exposed an external-runtime context mismatch (32,768 loaded
  versus 65,536 configured). The external installer now bounds its agent budget
  to the single selected llama.cpp runtime's reported per-slot capacity; it does
  not use training limits or attribute unscoped metadata to multi-model gateways.
  All 33 external-service fixtures pass, including smaller explicit budgets,
  malformed metadata and runtime capacity below the agent minimum. Other external
  providers without this metadata retain their existing context handling.
- At `7c6128e0`, 85 hosted checks passed, including Linux integration and the
  Windows/macOS jobs. Anonymous external links failed on three access-unconfirmed
  URLs (two Unsplash sources and the Intel driver page); no blanket exemption was
  added. The Portal reports its installed-release identity as unverified even
  though host access is verified. Neither that limitation nor native macOS/Linux
  hardware qualification is closed by the successful Windows/WSL smoke tests.

- Scanner findings are package/advisory occurrences, not confirmed exploits.
  Conversely, absence of a published fix is not an applicability waiver.
- The repository retains Pixel source locally. Tests must verify source/bundle
  consistency without contacting a private repository.
- The rebuilt bundle is paired with upgrade compatibility: Linux migrates only
  the previous bundled ref inherited from installer settings, preserving explicit
  pins and local checkouts. Previously prepared public clients remain readable.
  Eight upgrade scenarios, 185 Python tests, 318 host-install checks and 55
  integration checks passed, as did acquisition/tamper tests for the real bundle.
  The macOS results are fixtures run on Linux, not native-machine qualification.
- Fixture installers deliberately do not install Docker, model runtimes or ODS
  onto the developer's machine. Their checks prove only their stated contracts.
- [Source-update activation](SOURCE_UPDATE_ACTIVATION.md) now rebuilds local
  images and verifies host-agent/service readiness before recording completion.
  Twenty-nine source-update and 17 lifecycle fixtures passed under WSL, along
  with the existing CLI and rollback regressions. Configuration recovery is not
  a rollback of Git source, images or migration effects; native qualification
  remains open.
- Raw logs remain outside the published evidence set because they can contain
  local paths. Publish only reviewed summaries and redacted candidate receipts.
- A candidate must be committed and frozen before CI, signatures, scans and
  installation receipts can be bound to the same identity.

## Maintainer decisions still required

1. Review the secret adjudication and attach provider-side retirement evidence.
2. Confirm licensing and remaining artwork/model/recipe provenance decisions.
3. Configure and verify branch protection with the actual required check names.
4. Select the immutable beta tag and candidate commit, publish its reviewed kit,
   and collect clean-machine qualification evidence before release approval.

This document intentionally leaves unmet gates open. Code changes or local green
tests alone must not be used to mark the entire audit complete.
