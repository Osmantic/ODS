# Reviewed Python dependency inputs

These locks cover every direct `pip install` in active root workflows. Every
package, including transitives, has an exact version and SHA-256 hashes; callers
use `--require-hashes --only-binary=:all:`. This rejects altered artifacts and
avoids an unpinned build backend or build dependency from an sdist. Ruff remains
at 0.15.5, the release used for the existing audit lint result. The shared
test-tools input retains pytest 8.4.2 and its existing PyYAML/jsonschema ranges.

The `.in` inputs resolve for Python 3.11 and newer, matching their CI
callers. Locks were generated from public PyPI on 2026-09-23 using uv 0.12.18 in
a temporary directory. To review an update, run for each input from the repo root:

```sh
uv --no-config pip compile .github/requirements/test-tools.in --default-index https://pypi.org/simple --universal --python-version 3.11 --generate-hashes --no-header --output-file .github/requirements/test-tools.txt
```

Use the corresponding basename for each input. Service inputs include the
existing runtime/test requirements through `-r`; regenerate the lock whenever
one of those included files changes. Review the resolved diff and run
`test-python-locks.yml` plus the affected workflow gates.
Do not pass `--no-deps`, weaken the original requirements, or accept a substituted
hash just to make an install succeed. Hashes identify the reviewed release files;
they do not prove that a supplier's code is safe.

The host-agent and compose-resolver inputs live under
[`ods/installers/python-deps`](../../ods/installers/python-deps/README.md), because
they are shipped to installations. Their Python floor differs from CI tooling.

The remaining 13 direct install commands were consolidated into these seven
complete environments without weakening the source requirements:

| Lock | Inputs and consumers | Existing Python/platform coverage |
| --- | --- | --- |
| `dashboard-tests.txt` | Dashboard API runtime plus test requirements, installed together | Linux 3.11 |
| `pixel-edge-tests.txt` | Edge runtime, pytest 8, HTTPX and PyYAML; transition, portal and native matrix jobs | Linux 3.11/3.12, macOS 3.13 |
| `egress-tests.txt` | Remote-provider-egress runtime plus pytest | Linux 3.12 |
| `inference-tests.txt` | Pixel inference and model-router runtimes plus pytest 8 | Linux 3.11/3.12 |
| `token-spy-tests.txt` | Token Spy runtime plus pytest 8 | Linux 3.12 |
| `postgres-tests.txt` | Existing psycopg2-binary range plus pytest 8 | Linux 3.12 |
| `type-check.txt` | Mypy plus dashboard API, Token Spy and privacy-shield runtime requirements | Linux 3.11 |

The type-check environment is resolved and installed once, before the existing
steps that tolerate type errors. Dependency install failures therefore fail the
job. Portal tests now use Edge's existing exact aiohttp/IDNA pins. The original
dashboard pytest-asyncio/pytest-cov bounds and all service version ranges remain
in the inputs; resolution found no conflict between the required constraints.

`python .github/scripts/check_workflow_python_locks.py` emits the complete JSON
inventory. The 2026-09-23 inventory contains 32 direct commands and 52 invocations
after expanding literal matrices, with zero violations. The gate visits every
`.github/workflows/*.yml` and `*.yaml`, resolves working directories and literal
matrix paths, checks lock contents, and rejects missing hashes, source builds,
loose packages, extra indexes and unsupported dynamic install syntax. Its 13
regression tests include malicious/mistaken flags, environment overrides,
multiline commands and changed paths. CI runs it with the locked test tools.

Local evidence for the seven added locks: seven fresh WSL/Linux Python 3.12
venvs and four fresh Linux Python 3.11 venvs installed with hashes, imported
their dependencies and passed `pip check`. Edge (150 tests), inference (41),
router (151), Token Spy pricing/receipts (29), egress headers (10) and its 15
service contracts passed. Portal host tests passed 700 cases with 10 explicit
skips. Privacy-shield's 14 HTTP/streaming/WebSocket cases
passed after adding only the existing hashed test-tools lock to its temporary
type-check venv. The 15 lock-policy/substitution tests, two action/image pin
tests, actionlint and Ruff also passed. Python 3.13 wheels for macOS arm64 and
x86_64 were fetched and checked against the Edge lock; this did not execute them.

The broad dashboard run finished with 4,173 passed, 3 skipped and 12 failed:
five curated-recipe assertions and seven real PowerShell parser cases. The
Aider test expects a healthcheck absent from its compose file, also absent at
baseline commit `d046fa29`; the seven parser failures also reproduce in the
pre-existing Linux Python 3.12 environment without these new locks. This is not
a clean dashboard acceptance result. Native macOS, hosted CI and a live Postgres
service were not run here. Existing tolerated mypy errors are not cleared by this work.
The gate checks direct workflow commands, not arbitrary shell programs, Action
internals, Dockerfile installs or nested workflow templates. Those surfaces and
other supply-chain inputs remain separate; PB-011/PB-018 are not fully closed.
