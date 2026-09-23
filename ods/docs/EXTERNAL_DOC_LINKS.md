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
also runs classification, integrity, and local HTTP retry regressions and retains
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
  one request at a time with a 3.5-second interval. Each request has a 15-second
  timeout, at most one retry for a retryable failure, and at most 5 redirects.
  The process stops after 20 minutes; a deadline or unusable report fails the
  gate as incomplete. The workflow allows 25 minutes including setup, tests,
  and result upload.
  These controls follow Lychee's [configuration options](https://lychee.cli.rs/guides/config/).

Hugging Face documents an anonymous page quota of 100 requests per five-minute
window, subject to change; API and file resolver quotas differ. The 3.5-second
interval allows about 86 requests per window, including retries, below the
published page limit. The observed inventory of 285 Hugging Face URLs needs
about 994 seconds of spacing alone, so the previous 900-second deadline had
insufficient margin for this pacing. See the [official rate-limit policy](https://huggingface.co/docs/hub/rate-limits).
Lychee 0.24.2 applies [host backoff and `Retry-After`](https://github.com/lycheeverse/lychee/blob/lychee-v0.24.2/lychee-lib/src/ratelimit/host/host.rs) (capped at 60 seconds), but
does not parse Hugging Face's structured `RateLimit` reset field. One retry is
bounded recovery, not a guarantee against a shared-IP quota or a long cooldown;
exhausted retries remain findings. No token, status-code waiver, persistent
success cache, or replacement URL is used to turn a rate limit into a pass.

Only final 2xx responses count as successful HTTP checks. A 401 or 403 is
classified as access unconfirmed; 429, 5xx, and timeouts remain transient
failures. A 404 or 410 records a missing or not publicly exposed target; it
does not distinguish removal from a private resource. TLS and other transport
failures remain failures. Incomplete scans retain any observed failures without
claiming a total failure count or a passing result.
An empty, zero-link, malformed, or inconsistent JSON report is incomplete even
when the checker exits with code 0. Required counters must be nonnegative
integers, terminal counts must add up, and result maps must have valid shapes.

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
LYCHEE_BINARY="$PWD/output/lychee-bin/lychee" python -m unittest discover -s .github/scripts -p test_external_links.py -v
python .github/scripts/check_external_links.py --lychee output/lychee-bin/lychee
```

Results appear in `output/external-links/`. The CLI returns 0 for a complete run
with no unreviewed failures, 1 for unresolved findings, and 2 for an incomplete
scan. Setup or integrity errors also exit unsuccessfully. Avoid immediate full
reruns after rate limits; review the preserved report first.
`LYCHEE_BINARY` enables five loopback-only tests with the reviewed binary:
429 recovery after `Retry-After`, 429 followed by 404, persistent 429, direct
404, and spacing between distinct URLs. Without it, those integration tests
are explicitly skipped; the workflow always supplies it.

## Audit observation: 2026-09-23

The initial extraction contained 827 unique HTTP(S) URLs across 522 Markdown
files. A later online scan included 524 Markdown files and reached the earlier
480-second deadline before producing final JSON. Its log retained 37 unique
failure observations: 33 Hugging Face 429 responses, one Microbin timeout, one
Intel 403 response, and two 404 responses. This was an **incomplete** observation,
not a repository-wide pass. At that checkpoint, the slower configuration had
not yet completed a full online run.

The subsequent [PR CI run](https://github.com/Osmantic/ODS/actions/runs/35844320298)
completed its inventory of 562 Markdown files with 16 unresolved findings and
zero deferrals. Eight were Hugging Face rate limits; the remainder comprised
two occurrences of one Discord invite, the repository stargazers route, Intel,
two Unsplash pages, Orthanc and MicroBin. This completed scan still failed the
gate; completion is not a public-access pass.

A bounded anonymous recheck of those findings returned 200 for the eight
Hugging Face URLs, both Discord occurrences and Orthanc. The repository homepage
and public metadata were accessible while `/stargazers` still returned 404, so
the stars badge now links to the repository homepage. Intel returned 403, the
two Unsplash pages returned 401 through an access challenge, and MicroBin still
timed out. Their original links remain pending; browser-readable content is
not presented as a successful anonymous HTTP check. Historical evidence and
the empty exception ledger remain intact. The final candidate still requires
a fresh complete scan and review of unresolved links.

The obsolete LocalAI `/gallery/` link was corrected in its
[service README](../extensions/library/services/localai/README.md) using the
upstream [model setup guide](https://localai.io/docs/getting-started/models/).
A focused anonymous check passed all three external links in that README after
the edit. During that initial check, the stargazers route returned 404 and an
anonymous API probe returned 401; the later bounded recheck and badge correction
are recorded above. Intel's access restriction and the MicroBin timeout remain
unresolved. Hugging Face evidence URLs were preserved; 429 does not establish
that their targets are wrong.

A follow-up scan of 563 Markdown files completed in 825.99 seconds with 18
findings: 13 Hugging Face 429 responses, one GitHub 503, one MicroBin timeout,
one Intel 403, and two Unsplash 401 responses. A single anonymous probe of one
of those Hugging Face URLs then returned 200 with a page policy of 100 requests
per 300 seconds. This supports adjusting pacing and retries; it does not
resolve the other findings or establish a passing complete scan.

Intel now links to its current official Windows driver download page, whose
anonymous HTTP response still returned 403. MicroBin now links to the official
release 2.1.0 configuration file, which returned 200 anonymously. The Intel and
two Unsplash access challenges remain explicit, with no deferrals added. The
new pacing and retry policy has local fixture coverage; the next CI scan must
establish the complete online result.

The metadata sweep of current nonvendor documentation found generic user-path
examples, container paths, and source or artifact hashes. No additional concrete
personal machine path or transient machine identifier was identified. Technical
evidence and contributor attribution were retained.
