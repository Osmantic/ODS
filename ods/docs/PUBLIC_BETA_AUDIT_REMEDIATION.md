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
| PB-002: wallpaper rights | Removed the three former color JPEGs and integrated the approved circular AI variants. Recorded the individual Forest photograph source and license. | [Asset notices](../extensions/services/dashboard/ASSET-NOTICES.md) explicitly retain the unresolved rights boundary. Other individual image records and rights review are still needed. AI generation alone does not establish clearance. |
| PB-003: mixed licensing | Corrected blanket contribution/fork language and marked the old Pixel snapshot as historical; preserved existing license texts. | Rights holders must confirm inbound grants, the intended Pixel distribution terms, and any necessary license changes. No license grant was invented. |
| PB-004: Python/runtime advisories | Updated aiohttp/idna and APE dependencies; hash-locked APE requirements, pinned Python base images and removed build-only package tools from runtime images. Reviewed the Datasette advisory against its affected versions. Corrected credential fixtures: all 150 Pixel Edge tests and 39 subtests pass without relaxing runtime guards. | Distro high findings without published fixes require a reviewed applicability decision or upstream remediation. See [dependency evidence](DEPENDENCY_AUDIT_EVIDENCE.md) and [Edge test evidence](PIXEL_EDGE_TEST_EVIDENCE.md). |
| PB-005: internal operational records | Replaced identified public host/session diaries with product and acceptance documentation; removed personal operational identifiers from those records. | Repeat repository-wide sensitive-metadata review at the final candidate; sanitizing current files does not erase Git history. |
| PB-006: branch governance | Read-only inspection confirmed `public-beta` had no branch protection. | A repository administrator must configure and verify required checks/reviews and restricted bypass. No remote protection settings were changed by this work. |
| PB-007: documentation links | Repaired public links and the Pixel export generators together; omitted historical evidence is explicitly marked unavailable. Added the repository-wide local-link gate, nine parser regressions and 11 vendor documentation tests. Rebuilt and verified the matching single-commit local Pixel bundle. Added a checksum-pinned anonymous external-link checker and bounded CI job; corrected a confirmed LocalAI 404. See [documentation evidence](PUBLIC_DOC_LINK_EVIDENCE.md). | The broad external scan remains incomplete: rate limits, access denials and timeouts are not verified successes. Complete and review that check at the frozen candidate; no blanket domain/status exemptions were introduced. |
| PB-008: contributor records | Added an auditable [contributor ledger](CONTRIBUTOR-LEDGER.md) covering the named beta authors and preserved raw identity distinctions. | Maintainers/contributors must verify any identity aliases. No guessed mailmap or history rewrite was applied. |
| PB-009: unsupported claims | Removed unsupported recognition/growth claims from the identified public pages. | Editorial review of the final public tree; restore claims only with durable primary evidence. |
| PB-010: dashboard advisories | Updated React Router to 7.18.4; production npm audit returned no findings. Existing dashboard tests, build and six navigation regressions passed. | Run required CI at the frozen candidate. Development-tool advisories remain separate, documented dependency debt. |
| PB-011: mutable workflow inputs | Pinned the 11 reported Action refs and 12 CI image indices, with digest records and regression checks. Completed hash locks for all direct root-workflow pip installs, including the 13 remaining service/type-check commands. The new gate inventories 32 direct commands / 52 matrix invocations and rejects unverified installs. Eleven fresh Linux venvs passed installs, imports and dependency checks; affected focused service tests passed. See [Python inputs and limits](../../.github/requirements/README.md). | Direct pip coverage does not audit Action internals, arbitrary scripts, Dockerfiles or shipped nested workflow templates. Native macOS, hosted CI and live Postgres remain unexecuted. Dashboard: 4,173 passed / 3 skipped / 12 failed (five recipe assertions, seven PowerShell parser cases); Aider is confirmed at baseline `d046fa29`, and parser failures reproduce without the new locks. These changes do not make every supply-chain input immutable. |
| PB-012: whitespace/lint | Removed 162 whitespace findings from 11 Python files with equivalent ASTs; preserved required whitespace in two digest-bound format patches. Enabled React's JSX usage analysis, removed obsolete suppressions and genuinely unused bindings/imports. Dashboard lint reports zero errors/warnings and enforces `--max-warnings 0`; the latest local run passed all 1,602 tests and the production build. | Required CI and the full whitespace gate must pass again at the final candidate. No warning baseline or blanket suppression was introduced. |
| PB-013: recipe/model provenance | Corrected AudioCraft's noncommercial-model description. Backfilled the 34 legacy recipe records with notices and explicit gaps, added provenance validation, and removed XTTS's automatic terms acceptance. Added a [57-entry model evidence inventory](MODEL_TERMS_AUDIT.md), [catalog source records and read-only terms panel](MODEL_TERMS_CATALOG.md). Pinned 48 observed mutable URLs and repaired the two absent Gemma Q4_K_M artifacts with separately recorded source/size/checksum evidence, updating all 12 affected installer fallbacks. All 57 primary catalog download references are pinned. | These observations are not license approval or inference qualification. Reconcile conflicts, carry applicable notices and present/enforce applicable terms before downloads through all supported paths. All model release reviews remain unassessed; the optional panel does not close that acceptance requirement. Full downloads and runtime checks for the two replacement artifacts remain unexecuted. |
| PB-014: stale AI triage | Replaced stale prompt facts and model-directed command output with a bounded structured inference contract. | Verify the final deployed manual workflow and permissions. See [AI workflow boundaries](../../.github/AI-WORKFLOWS.md). |
| PB-015: paid AI/write authority | Public events no longer launch paid inference. Maintainer dispatch, bounded input/output, rate limits, revision binding, separated inference/publishing and deterministic label/comment application are implemented. Adversarial tests pass. | Live maintainer-controlled qualification remains; no paid API invocation was performed during local tests. Automated AI code-fix behavior was removed, not silently retained with the same authority. |
| PB-016: installer logs | Default logs now use private directories; safe overrides retain canonical paths. Guards reject special files, hardlinks, foreign ownership and unsafe ancestors, and cover derived installer logs. All 19 adversarial log checks and three local-build contracts passed on Linux/WSL, alongside the previously recorded 51 validator checks. Isolated user-bus and inherited-FD regressions pass. The CLI macOS empty-log regression was corrected, with all nine routing/log cases passing on Linux fixtures. See [log evidence and limits](INSTALLER_LOGS.md). | Native macOS filesystem/stat behavior and real installer execution still require qualification at the final candidate. The isolated macOS helper fixture ran on Linux; same-user/root interference and every long-lived service log are outside this guard's claim. |
| PB-017: beta identity | Added a commit-bound kit generator with archive/installer checksums, receipts and tamper-rejection tests. Beta guides distinguish the ordinary main bootstrap from a future frozen beta kit. | Publish a reviewed/signed immutable kit for the final candidate and collect real clean-install receipts on Windows, Linux and macOS. See [beta installation](PUBLIC_BETA_INSTALL.md). |
| PB-018: downloaded execution | Added a fail-closed SHA-256 downloader and reviewed immutable Docker/NodeSource scripts. Linux/macOS OpenCode use reviewed artifact pins; Windows verifies the pinned ZIP before parsing/extraction. Claude/Codex require explicit opt-in, exact versions, a complete npm integrity lock, disabled lifecycle scripts, and an installation receipt. [Python locks](../installers/python-deps/README.md) now cover host-agent/PyYAML recovery, both Linux phase 11 Hugging Face bootstraps, macOS/Windows download UI fallbacks, pre-download and the optional mDNS pip fallback. Actual pip rejected substituted wheels at all six extra boundaries; fresh Linux Python 3.12 and Windows Python 3.9 mDNS environments passed import/dependency checks. | Complete native macOS/Windows CLI qualification and the download inventory. Native macOS and Python 3.8 were not executed in the additional bootstrap validation; 3.8 compatibility is represented by reviewed resolution markers. OS package-manager inputs, container/model downloads and existing owner-installed tools remain outside these locks. See [optional CLI installation](../installers/ai-clis/README.md); PB-018 remains partial and no complete supply-chain qualification is claimed. |

## Evidence boundaries

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
