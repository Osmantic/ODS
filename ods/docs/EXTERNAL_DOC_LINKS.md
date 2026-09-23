# Public external documentation links

The `External documentation links` workflow checks anonymous HTTP(S) access to
links in published Markdown. It runs for documentation pull requests to `main`
and `public-beta`, and can be dispatched manually. A successful HTTP response
does not establish license terms, content accuracy, or the existence of an
external page fragment.

The checker is [Lychee 0.24.2](https://github.com/lycheeverse/lychee/releases/tag/lychee-v0.24.2).
Linux and Windows x86-64 release archives have fixed SHA-256 digests in
[the tool manifest](../../.github/lychee-tool.json). The installer verifies the
complete archive before writing its executable. Python uses only its standard
library. The [workflow](../../.github/workflows/check-external-doc-links.yml)
also runs the offline classification and integrity regressions and retains
the input list, raw log, JSON report when available, and interpreted findings
as a CI artifact for 14 days.

## Scope and limits

- Inputs come from Git's tracked files and untracked, nonignored files ending
  in `.md`, including vendor and hidden Markdown. Root `output/`, `.git`, and
  `node_modules` are excluded. Deleted files are omitted. The inventory is saved
  with each run; concurrent local edits can change the next run's input count.
- HTTP(S) links in rendered Markdown are checked. Code examples and email
  addresses are outside this gate. Local paths and Markdown anchors have their
  own [local link gate](PUBLIC_DOC_LINK_EVIDENCE.md).
- External fragments are **not validated**. The initial audit extraction found
  17 unique URLs with fragments; those anchors still need separate review.
- Checks are anonymous: no GitHub token, cookie jar, or authentication header is
  configured, and GitHub token environment variables are removed from the
  checker process. A review or historical evidence URL can require account
  access; that is not evidence of public availability.
- Only private/local addresses, single-label service hosts, and reserved
  example domains are excluded. Public GitHub and Hugging Face URLs remain in
  scope. No public domain is blanket-excluded.
- The [configuration](../../.github/lychee.toml) limits total concurrency to 6,
  concurrency per host to 2, and spacing per host to 500 ms. Hugging Face uses
  one request at a time with a 2-second interval. Each request has a 15-second
  timeout, zero retries, and at most 5 redirects. The process stops after
  15 minutes; a deadline or unusable report fails the gate as incomplete.
  These controls follow Lychee's [configuration options](https://lychee.cli.rs/guides/config/).

Only final 2xx responses count as successful HTTP checks. A 401 or 403 is
classified as access unconfirmed; 429, 5xx, and timeouts remain transient
failures. A 404 or 410 records a missing or not publicly exposed target; it
does not distinguish removal from a private resource. TLS and other transport
failures remain failures. Incomplete scans retain any observed failures without
claiming a total failure count or a passing result.

The [exception ledger](../../.github/external-link-exceptions.json) starts empty.
A reviewed temporary deferral must name one exact URL, failure categories,
reason, owner, and review expiry no more than 90 days away. Expired or malformed
entries fail validation. Deferrals stay visible as **not verified public links**;
they never turn into successful HTTP observations. Preserve historical evidence
and identify access requirements instead of inventing a public replacement.

## Local execution

Run from the repository root on Linux x86-64 (or use the Windows executable
printed by the same installer):

```sh
python .github/scripts/install_lychee.py --destination output/lychee-bin
python -m unittest discover -s .github/scripts -p test_external_links.py -v
python .github/scripts/check_external_links.py --lychee output/lychee-bin/lychee
```

Results appear in `output/external-links/`. The CLI returns 0 for a complete run
with no unreviewed failures, 1 for unresolved findings, and 2 for an incomplete
scan. Setup or integrity errors also exit unsuccessfully. Avoid immediate full
reruns after rate limits; review the preserved report first.

## Audit observation: 2026-09-23

The initial extraction contained 827 unique HTTP(S) URLs across 522 Markdown
files. A later online scan included 524 Markdown files and reached the earlier
480-second deadline before producing final JSON. Its log retained 37 unique
failure observations: 33 Hugging Face 429 responses, one Microbin timeout, one
Intel 403 response, and two 404 responses. This was an **incomplete** observation,
not a repository-wide pass; the final slower configuration has not yet completed
a full online run.

The obsolete LocalAI `/gallery/` link was corrected in its
[service README](../extensions/library/services/localai/README.md) using the
upstream [model setup guide](https://localai.io/docs/getting-started/models/).
A focused anonymous check passed all three external links in that README after
the edit. The other 404 was the ODS stargazers link; a separate anonymous API
probe returned 401, so public access remains unconfirmed. Intel's 403 and the
Microbin timeout also remain unresolved. Hugging Face evidence URLs were
preserved; 429 does not establish that their targets are wrong.

The metadata sweep of current nonvendor documentation found generic user-path
examples, container paths, and source or artifact hashes. No additional concrete
personal machine path or transient machine identifier was identified. Technical
evidence and contributor attribution were retained.
