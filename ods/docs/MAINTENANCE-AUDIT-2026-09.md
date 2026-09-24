# September 2026 documentation and repository hygiene audit

Reviewed 2026-09-23/24 against the post-promotion main baseline
`1bc5e1cbb24864f1c9efd551e0613202af324e3f`, with follow-up changes reviewed in
the PRs below. The earlier public-beta report was a guide, not an authority for
the current source. Upstream license files, model metadata, Git history and
current repository files were checked again.

Scope: documentation, licensing explanations, attribution, repository checks
and the separately authorized replacement wallpaper set. Runtime logic,
installers, model selection, dependency versions and the vendored Pixel bundle
were outside the change scope. No fleet/model execution or installed-host
qualification is claimed by this audit.

## Change record

| PR | Correction |
| --- | --- |
| [#6540](https://github.com/Osmantic/ODS/pull/6540) | Component-specific license and contribution boundaries; model terms and artwork evidence; Open WebUI, n8n, AudioCraft and XTTS guidance |
| [#6541](https://github.com/Osmantic/ODS/pull/6541) | Main promotion truth, qualified support statements, historical-plan labels, contributor ledger, sanitized public records and owned-document links |
| [#6543](https://github.com/Osmantic/ODS/pull/6543) | Content-bound Markdown link regression gate and accurate repository context for issue-triage automation |
| [#6546](https://github.com/Osmantic/ODS/pull/6546) | Evidence index, source/license references for 34 recipes, historical probe sanitization and retired link exceptions |
| [#6548](https://github.com/Osmantic/ODS/pull/6548) | Restore standard Gitleaks rules, remove broad file exclusions, exercise synthetic coverage and document exact historical exceptions |
| [#6551](https://github.com/Osmantic/ODS/pull/6551) | Replace all 12 wallpapers with documented AI-generated artwork, preserving IDs/paths/dimensions and changing only three visible names |

Each PR's current status and checks are available through its link. This document
does not substitute for the merge receipt or the exact-head CI result. The
maintainer explicitly authorized administrator merges after checks passed when
the two-review branch requirement prevented normal merging; the branch rules
were not changed.

## Evidence and limits

- The strict extension audit passes 203 service definitions. That is structural
  validation, not qualification of every extension or its upstream license.
- AudioCraft and Baserow descriptions now distinguish restricted model weights
  and premium/enterprise terms from their code/core licenses. The regenerated
  catalog changes only those two descriptions and its generation timestamp;
  service execution settings remain unchanged.
- The model terms inventory covers 57 curated entries. All 54 direct
  Hugging Face GGUF source records were retrieved; 52 declare license metadata
  and two do not. Metadata is not a substitute for original-model terms.
- All 34 library recipes without structured upstream records have a pinned
  source/license starting point in the recipe register. Image-to-source custody
  and complete binary-distribution notices still require separate work.
- The documentation gate reports 103 known missing targets in four unchanged
  vendored Pixel documents. Owned-document broken targets were repaired. The
  baseline is bound to exact document contents and cannot silently accept edits.
- Restored Gitleaks coverage passes the synthetic positive check and fails
  against the old configuration. The fetched branch/tag scan passed after 57
  individually reviewed additional fingerprints were acknowledged. This is
  not provider-side revocation evidence or a proof that no secret exists.
- The wallpaper set shrinks from 37.41 MiB to 9.00 MiB. Distribution hashes,
  generation-master hashes and dimensions are recorded. Dashboard build and
  18 focused wallpaper/video tests passed locally; PR CI covers the full suite.

## Dependency advisory snapshot

The dashboard lockfile was inspected without changing it. On 2026-09-24,
`npm audit --json` reported **17 affected package entries: seven high, seven
moderate and three low**. These counts are package entries, not distinct CVEs
or demonstrated exploits. `npm audit --omit=dev --json` reported three moderate
entries: `@remix-run/router`, `react-router` and `react-router-dom`; no high or
critical entries in that production-only result.

The full tree's high entries were `brace-expansion`, `browserslist`, `js-yaml`,
`nanoid`, `postcss`, `undici` and `vite`. They warrant a separately tested
dependency update after the current test freeze. Development-tool exposure and
the shipped static application have different attack surfaces.

Upstream React Router advisories include
[protocol-relative redirects](https://github.com/remix-run/react-router/security/advisories/GHSA-2j2x-hqr9-3h42),
[backslash navigation](https://github.com/remix-run/react-router/security/advisories/GHSA-wrjc-x8rr-h8h6),
[SSR hydration](https://github.com/remix-run/react-router/security/advisories/GHSA-337j-9hxr-rhxg)
and [redirect/XSS](https://github.com/remix-run/react-router/security/advisories/GHSA-jjmj-jmhj-qwj2).
The current dashboard uses `BrowserRouter`; the first advisory explicitly
excludes declarative mode. That observation does not close the other advisories
or establish whether attacker-controlled navigation input can reach them.
Review applicability and retest navigation when selecting the upgrade.

The scanner snapshot is not a full dependency/SBOM audit of all Python packages,
containers, vendored tools, fonts or downloaded models. A green PR is not a
clean vulnerability report.

## Work requiring separate changes or evidence

| Item | Remaining action |
| --- | --- |
| XTTS consent | Replace the hardcoded agreement with reviewed explicit operator acceptance; retain the noncommercial model/output terms |
| Model/recipe custody | Bind original-source terms, notices and acceptance needs to exact downloaded artifacts and image digests |
| Pixel private-reference debt | Repair the public export during a coordinated, verified source/bundle refresh; do not invent omitted private receipts |
| Dependency advisories | Review applicability, update deliberately and run affected regression/build checks after the freeze |
| Historical secrets | Confirm rotation privately for any legacy deployment using published defaults; source scanning cannot establish revocation |
| Brand records and old releases | Preserve ODS mark/favicon authorization; replacement wallpaper terms do not clear images retained in historical releases |
| Release qualification | Run the independent, exact-runtime user-journey acceptance process; documentation cleanup is not release approval |

The authorized maintenance work improves the public record and regression
checks. It does not certify the entire product as legally cleared, vulnerability
free, or qualified on every platform. The remaining items above must stay visible
until the relevant evidence or separately scoped change closes them.
