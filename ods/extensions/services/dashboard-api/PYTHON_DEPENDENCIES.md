# Dashboard API runtime dependencies

The container installs `requirements-runtime.lock` with pip's `--require-hashes`
and `--only-binary=:all:` against public PyPI. It includes the complete runtime
dependency graph, including the `uvicorn[standard]` and `qrcode[pil]` extras.
Source distributions and their unpinned build dependencies are rejected.
`requirements.txt` remains the input used by development and CI resolution.

The runtime lock was generated on 2026-09-23 with uv 0.12.18, constrained to
`.github/requirements/dashboard-tests.txt`. All 37 package versions and accepted
hashes match that reviewed test environment; 36 packages apply to Linux (the
remaining entry is Windows-only colorama). Test tools such as pytest and coverage
are not installed in the runtime image. This lock targets CPython 3.11; wheel
availability was checked for Linux amd64 and arm64.

To regenerate from the repository root, use the reviewed uv tool and an existing
Python 3.11 interpreter:

```sh
uv --no-config pip compile ods/extensions/services/dashboard-api/requirements.txt \
  --constraint .github/requirements/dashboard-tests.txt \
  --default-index https://pypi.org/simple --universal --python-version 3.11 \
  --python /path/to/python3.11 --generate-hashes --no-header \
  --output-file ods/extensions/services/dashboard-api/requirements-runtime.lock
```

For a version update, review and regenerate the dashboard CI lock first, then
regenerate this runtime lock with its versions as constraints. Run both the API
tests and this offline test, which checks version/hash alignment and executes
the Dockerfile's pip command against an approved then substituted fixture wheel:

```sh
python ods/extensions/services/dashboard-api/tests/test_runtime_dependency_lock.py -v
```

Before accepting an update, create a fresh Linux Python 3.11 venv, install using
the Dockerfile's hash/binary flags, run `python -m pip check`, and import the
runtime dependencies. Also verify wheels for each container architecture without
executing foreign code; for example, using the venv's Python:

```sh
python -m pip --isolated download --index-url https://pypi.org/simple \
  --require-hashes --only-binary=:all: --implementation cp --python-version 3.11 \
  --abi cp311 --abi abi3 --abi none \
  --platform manylinux_2_28_aarch64 --platform manylinux_2_17_aarch64 \
  --platform manylinux2014_aarch64 --dest /tmp/dashboard-arm64-wheels \
  -r ods/extensions/services/dashboard-api/requirements-runtime.lock
```

Repeat with `x86_64` platform suffixes for amd64. Do not use `--no-deps`, remove
hash verification, add an alternate index, or accept a newly observed hash merely
to make installation succeed. A dependency change requires review of both locks.

The Python base is pinned to the previously reviewed official image index
`python:3.11-slim@sha256:da047cb8f9d1d98e5c070f5300ba9f7274e33b8fc0e5be5ed88740aed1b95ba9`.
The registry response hash and its Linux amd64/arm64 manifests were rechecked.
See [dependency evidence](../../../docs/DEPENDENCY_AUDIT_EVIDENCE.md) for the
existing base-image findings; this pin does not resolve those distro issues.

Validation on 2026-09-23: fresh Linux x86_64 Python 3.11.16 installation, runtime
imports and `pip check` passed; hash-verified downloads resolved 36 wheels on each
architecture. The three offline tests passed on Linux Python 3.11 and native
Windows Python 3.11, including rejection of substituted bytes before installation.
No arm64 code was executed. No dashboard container was built, started or scanned
in this change. Apt packages remain resolved from Debian repositories at build
time; image findings, reproducible OS packages and other services remain separate.
