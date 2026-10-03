# Public documentation hygiene

The `Documentation hygiene` workflow checks file targets in all tracked `.md`
documents. Run it locally with Python's standard library:

```bash
python3 -m unittest discover -s .github/scripts -p test_doc_links.py
python3 .github/scripts/check-doc-links.py
```

Run these commands from the repository root. They read source files and Git's
tracked file list; they do not install dependencies, contact services or the
fleet, run models, or modify runtime state.

## What the check means

The scanner checks ordinary inline Markdown links and reference definitions for
existing local file or directory targets. It decodes escaped paths and ignores
URL schemes, in-page anchors, site-root links, and fenced/indented code examples.
It is not a full Markdown parser, HTTP link checker, or heading-anchor validator.
External accessibility and exact license/source assertions still need review.

The older `ods/tests/test-doc-links.sh` intentionally covers a smaller document
set. Passing that test alone does not establish repository-wide link health.

## Known debt and the release-testing boundary

The baseline review at `1bc5e1cbb24864f1c9efd551e0613202af324e3f` found 107
missing local file links outside code examples. Four are in owned documents
addressed by [PR #6541](https://github.com/Osmantic/ODS/pull/6541). The other 103
are in four files inside the verified Pixel source bundle:

| Document | Missing link occurrences |
| --- | ---: |
| `ods/vendor/pixel/OPENCLAW-COMPATIBILITY.md` | 33 |
| `ods/vendor/pixel/docs/reference/document-inventory.md` | 34 |
| `ods/vendor/pixel/docs/releases/evidence-index.md` | 34 |
| `ods/vendor/pixel/docs/status.md` | 2 |

Most targets are historical private audit files deliberately omitted from the
public export. Do not manufacture those receipts or publish private evidence to
make links resolve. Repair the public document wording in a coordinated Pixel
source/bundle refresh, and verify that visible source, bundle and source pins
still match. The documentation-only cleanup does not change that installed
artifact identity during runtime testing.

[The baseline](../../.github/doc-link-baseline.json) records each known file's
normalized-content SHA-256 and exact missing targets. Only unchanged known debt
is tolerated. An added broken link, a new document with a missing target, or an
edit to a document whose missing links remain unreviewed fails. Resolved targets
are not counted as debt. Remove obsolete baseline entries after fixes merge.

A green check means **no new or changed broken-link debt**, not zero missing
links. The job always prints the remaining known count. Do not increase the
baseline merely to make a failing change pass; document the underlying source
boundary and planned repair in review.

## Public-record conventions

- Bind release claims to an exact source revision and distinguish source tests,
  installed behavior and complete user-journey acceptance.
- Mark old plans, checklists and decisions as historical or superseded. Link
  current guidance rather than leaving transient instructions as live policy.
- Publish sanitized summaries instead of personal paths, session identifiers,
  private-only hyperlinks or raw workstation diaries.
- Preserve copyright and license text. Explain a historical notice's scope in
  surrounding documentation rather than silently editing its legal terms.
- Use verified, privacy-preserving commit identities. Confirm contributor aliases
  before adding `.mailmap` entries; preserve public history.
