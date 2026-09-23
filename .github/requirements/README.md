# Reviewed Python dependency inputs

These locks address the shared tooling downloads in active workflows. Every
package, including transitives, has an exact version and SHA-256 hashes; callers
use `--require-hashes --only-binary=:all:`. This rejects altered artifacts and
avoids an unpinned build backend or build dependency from an sdist. Ruff remains
at 0.15.5, the release used for the existing audit lint result. Test jobs retain
pytest 8.4.2 and the existing PyYAML/jsonschema ranges.

The four `.in` inputs resolve for Python 3.11 and newer, matching their CI
callers. Locks were generated from public PyPI on 2026-09-23 using uv 0.12.18 in
a temporary directory. To review an update, run for each input from the repo root:

```sh
uv --no-config pip compile .github/requirements/test-tools.in --default-index https://pypi.org/simple --universal --python-version 3.11 --generate-hashes --no-header --output-file .github/requirements/test-tools.txt
```

Use the corresponding basename for Ruff, Bandit and the scanner SDK. Review the
resolved diff and run `test-python-locks.yml` plus the affected workflow gates.
Do not pass `--no-deps`, weaken the original requirements, or accept a substituted
hash just to make an install succeed. Hashes identify the reviewed release files;
they do not prove that a supplier's code is safe.

The host-agent and compose-resolver inputs live under
[`ods/installers/python-deps`](../../ods/installers/python-deps/README.md), because
they are shipped to installations. Their Python floor differs from CI tooling.

This is a bounded first pass. The following active workflow installations still
need their own complete application/runtime locks: dashboard API and its tests,
Pixel Edge (including matrix-smoke and transition jobs), portal runtime,
remote-provider-egress, inference/model-router, token-spy/Postgres, and mypy with
the three service environments it checks. Container builds, OS packages and
nested workflow templates are separate. PB-011/PB-018 are not closed by these
tooling locks.
