# PB-007 public documentation links — local evidence

Date: 2026-09-23. Worktree: `ODS-public-beta-audit-remediation`.

The public Pixel contributor pages now describe the unavailable upstream PR/CI
files as an export limit. Their metadata points to existing public sources; no
replacement workflow or private CI result is invented. The source map removes
the absent upstream workflow directory. The inventory still validates all
existing contracts and now expects 144 indexed documents and zero unreferenced
private release-audit documents for the explicitly recorded public export.

The repository gate inventories `git ls-files --cached --others
--exclude-standard`, retaining tracked ignored files and new nonignored files.
It excludes `output/`, `.git`, and `node_modules`, skips deleted tracked sources,
and rejects links whose targets are missing, outside the repository, or absent
from the public file inventory. Root, security, extension and vendored Markdown
are covered. There is no missing-target allowlist. The only additional broken
published link found by this gate was the old root `wall-of-heroes` fragment in
`ods/README.md`; it now points at `contributors-and-recognition`.

`ods/scripts/check-doc-links.mjs` shares the Pixel link checker. It checks local
paths, percent encoding, query strings, heading/explicit-anchor fragments,
duplicate headings, reference links, images and common HTML media/link tags.
Fenced and inline code, comments, front matter and URI schemes are treated as
their respective contexts. Nine regression tests include three independently
reported false negatives: an unmatched code span crossing into a heading,
backticks inside a destination, and a reference definition in a blockquote.
They also preserve raw reference labels, reject missing/private targets and
traversal, and ensure a malformed definition does not hide a following link.

The existing Bash entrypoint now runs those regressions and the broad gate,
retaining its CONTRIBUTING working-directory checks. The Linux integration-smoke
job sets up Node 22 with a pinned action before invoking that entrypoint.

Validation performed locally:

- `bash -n ods/tests/test-doc-links.sh`: passed using Git Bash.
- `bash ods/tests/test-doc-links.sh`: nine tests passed, public gate passed, and
  contribution work-directory checks passed.
- Latest coordinated gate result: 520 Markdown files and 2,594 local links;
  files added by other agents increased the inventory from the earlier 519.
- `node --test ods/vendor/pixel/tests/docs-inventory.test.mjs
  ods/vendor/pixel/tests/public-export.test.mjs`: 11 tests passed, including the
  full Pixel documentation contract.
- Pixel documentation checks report 80 metadata pages, all 80 navigation pages
  reachable, 48 source mappings, 83 snippets, 144 indexed Markdown documents,
  and zero unreferenced private audit documents.
- `git diff --check` for the edited gate, metadata and workflow files: passed.

Scope limits: this remains a scanner for the documented repository conventions,
not a complete CommonMark/GFM renderer. Deeply nested container syntax and
indented-code rendering are not fully modeled. It does not fetch external URLs
or validate fragments inside non-Markdown formats. New structural parsing issues
should be solved with explicit regressions or a locked established parser, not a
target allowlist. These limits do not exempt any published Markdown file from
the inventory.

After freezing the vendor source, the public bundle was regenerated at
`69f4ad0bd062fe006e9d5b473a04b9a38eff8533`. Source equality, one-commit history,
acquisition and tamper rejection passed. Installation uses this local bundle;
no private source was fetched. The rebuilt bundle's seven unchanged scanner
occurrences were individually matched to the preceding reviewed source, with
zero unreviewed findings after that review.

The final documentation inventory includes additional audit reports, so its
file/link totals exceed the earlier coordinated run recorded above. Remote CI
has not run as part of this local evidence.
