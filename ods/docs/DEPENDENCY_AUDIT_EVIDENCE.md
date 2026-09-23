# PB-004 / PB-010 remediation evidence — 2026-09-23

This evidence describes the working tree and isolated local images, not a frozen
release SHA. Raw scanner output is retained locally under `output/` and includes
workspace paths; this document is the sanitized summary.

## Dependency changes

| Finding | Change | Evidence / status |
| --- | --- | --- |
| PB-004 Pixel runtime | Both services now pin aiohttp 3.14.3 and idna 3.20. | OSV 2.6.0 reports no findings for either requirements file. [aiohttp release](https://pypi.org/project/aiohttp/3.14.3/) supports Python >=3.10. |
| PB-004 APE | FastAPI 0.141.1, Uvicorn 0.53.0, Pydantic 2.13.5, PyYAML 6.0.3; 19 exact transitive versions with distribution hashes. Docker installation enforces hashes. | OSV reports no findings in the resolved runtime; 81 existing tests pass. |
| PB-004 image tooling | Pin Python base image indices; update the build installer and remove pip/setuptools/wheel from final runtime images. | Trivy finds zero Python package vulnerabilities in all three final images. |
| Datasette | Retain latest stable 0.65.5, document the advisory applicability next to the pin. | The only OSV Python finding is PYSEC-2023-154. The [upstream advisory](https://github.com/simonw/datasette/security/advisories/GHSA-7ch3-7pp7-7cpq) explicitly limits impact to 1.0a0–1.0a3. GET /-/api against the built 0.65.5 image returns 404. No blanket scanner suppression was added. |
| PB-010 dashboard | React Router DOM 7.18.4 with regenerated npm lock; six navigation regression cases. | npm production audit: zero findings. Upstream [navigation advisory](https://github.com/remix-run/react-router/security/advisories/GHSA-wrjc-x8rr-h8h6) requires >=7.18.0; v6.30.6 is insufficient. |

## Remaining image findings

Trivy 0.74.0 ran against the actual final Linux/amd64 images with its current
database. These are package/advisory occurrences, not 44 independently confirmed
exploitable defects. No exploitability exception was assumed or suppression added.

| Final image | Python package findings | OS critical | OS high | High entries with a published fixed Debian version | High entries without a published fixed Debian version |
| --- | ---: | ---: | ---: | ---: | ---: |
| Pixel Edge | 0 | 0 | 44 | 0 | 44 |
| Pixel Model Relay | 0 | 0 | 44 | 0 | 44 |
| APE | 0 | 0 | 44 | 0 | 44 |

The remaining high groups cover util-linux and its binary packages (36
occurrences), ncurses (4), systemd libraries (2), libacl (1), and perl-base (1).
The scanner provides no fixed version for those Debian 13 packages. They still
require upstream remediation or documented, reviewed applicability analysis.
PB-004's image acceptance gate remains OPEN. Pixel Edge's unit/socket suite now
passes after its credential fixtures were aligned with the existing runtime guard.

The earlier cached Python 3.11 base contained three fixable critical Perl
findings plus additional fixable high entries. Refreshing and pinning the base
removed those. Fixable packaging-tool findings were removed with the build
tools; only the distro findings in the table remain in the final images.

## Reproducibility

Base image indices in the Dockerfiles:

- Pixel Edge / Model Relay: `python:3.11-slim@sha256:da047cb8f9d1d98e5c070f5300ba9f7274e33b8fc0e5be5ed88740aed1b95ba9`.
- APE: `python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9`.
- Trivy scanner: `aquasec/trivy@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969`.

Final local image IDs (not pushed registry digests):

- Pixel Edge: `sha256:d7c459bfdb210e3cdccded7d53efceb533f3e23a63055b07a88f659d48f550e4`.
- Pixel Model Relay: `sha256:4ed7c0697b696c575549a6aa2a384dc0699a6794dcac2bd02dcd537ecd873c90`.
- APE: `sha256:7ee32d3b823ffa0e6c3ca9ac11b76b355ca4783b71a5edfcc13746a43f18ffb8`.

OSV Scanner 2.6.0 Windows executable SHA-256 was verified against the official
release checksum file: `e0ed7644118b717b028c249ee9d3515024e55e8510747ca08906eb96765354d6`.

## Validation and limits

- Dashboard final suite after lint cleanup: 197 test files, 1,596 tests passed;
  production build passed. This includes the six added slash/backslash navigation
  security cases. The complete dashboard ESLint gate has no warnings/errors.
- Dashboard `npm audit --omit=dev --registry=https://registry.npmjs.org`: zero.
  The complete dependency graph still has 14 npm development-tool findings
  (7 high, 4 moderate, 3 low), reconfirmed after the ESLint plugin lock update;
  full OSV output is preserved separately.
- Pixel Relay: nine socket-level authorization/disconnect tests pass in the
  runtime image.
- APE: 81 tests pass; a separately started nonroot runtime serves HTTP 200 on
  /health. Test tooling is installed only into disposable test containers using
  ensurepip; the scanned runtime images do not retain pip.
- Pixel Edge: 150 tests and 39 subtests pass in the final runtime image after
  correcting credential fixtures and isolating their transition state. The
  equal-key rejection and real transition-gate coverage remain intact; runtime
  authentication code was not weakened. The earlier identical failures under
  old/new aiohttp are retained as baseline evidence, not the current test result.
- No deployed service, installed environment, or hardware fleet was changed.
- Required runtime files pass `git diff --check`.

Raw evidence: `dependencies-*-trivy.json`, `dependencies-runtime-osv.json`,
`dependencies-dashboard-osv.json`, `dependencies-dashboard-npm-audit.json`,
`dependencies-*-tests.log`, `dependencies-edge-pytest.log`,
`dependencies-edge-baseline-pytest.log`, `edge-fixture-remediation-pytest.log`,
and dashboard build/test logs.
