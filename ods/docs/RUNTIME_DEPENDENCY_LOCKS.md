# Runtime dependency locks

The nine Python API/relay images install `requirements.lock` with pip's
`--require-hashes`. `requirements.txt` is the human-maintained input, not the
production installer input. Locks include all transitive distributions and
upstream SHA-256 hashes for the supported platform wheels.

After changing an input, regenerate its adjacent lock from the repository root:

```sh
python -m pip install uv==0.12.19
uv pip compile ods/extensions/services/dashboard-api/requirements.txt \
  --universal --python-version 3.10 --generate-hashes --no-header \
  --no-annotate --index-url https://pypi.org/simple \
  -o ods/extensions/services/dashboard-api/requirements.lock
```

Replace `dashboard-api` with the service being updated. Existing versions are
retained where they still satisfy the inputs; use `--upgrade-package NAME` for
a reviewed transitive update. Commit the input and lock together. Do not edit
hashes or ignore advisories to make a build pass.

`security-runtime.yml` verifies regeneration without drift, installs each lock
using its image's Python minor version, checks dependency consistency, and
runs `pip-audit --require-hashes` on the production lock. The weekly run catches
new advisories even when no dependency file has changed. Dashboard production
dependencies are audited separately from development tools.

Hashes verify downloaded package bytes; they do not establish that an upstream
package is benign. Security review, vulnerability audits and integration tests
remain necessary. Docker OS packages and release provenance are separate checks.
