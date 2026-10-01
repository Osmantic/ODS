# Managed Portal project runtimes

`pixel_ods_project_build` submits a workspace snapshot to the installed project
service. The service checks current owner authority before accepting work,
before each stage, and before importing results. Installing a runtime does not
enable Full Access or permit arbitrary host commands.

Submit with exactly `{"action":"submit","project":"Playground/my-site","outputDirectory":"dist"}`.
The manifests select npm or Python; `runtime` is only a diagnose/capabilities
parameter. `outputDirectory` is the basename inside the project, not a full
workspace path. Invalid parameters return a field and correction hint with
`executionStarted:false`; fixing them still requires an admitted owner call.

If a diagnostic cannot start, its durable `failure` contains only a closed
phase/code pair (for example `storage-reservation` / `operation-timeout`). It
does not expose command arguments, exception text, paths or subprocess output.
This preserves failure classification through final receipt writing without
claiming that a later successful check explains an earlier transient failure.

## npm projects

Existing projects keep the original behavior: `package.json` and a matching npm
v3 `package-lock.json`, registry tarballs with SHA-512 integrity, `npm ci` with
lifecycle scripts disabled, then `npm test` and `npm run build` offline.

## Python projects

Create `ods-project.json` containing exactly `{"runtime":"python"}`, plus:

- `requirements.lock`: each dependency, including transitive dependencies, must
  use `name==version --hash=sha256:<verified wheel digest>`. Multiple hashes and
  backslash line continuations are accepted. Obtain hashes from the published
  PyPI release metadata; never invent them. An empty file supports projects
  using only the standard library.
- `tests/test_*.py`: tests discovered by Python's `unittest`. At least one test
  must run and all must pass.
- `main.py`: the program that generates the selected output directory, such as
  `out`. Tests should check behavior without pre-creating final build outputs.

Submit the project and output directory using the same tool used for npm.
The installer supplies the immutable Python 3.11 image; tool calls cannot choose
an image, interpreter, command, package index, or host mount.

The acquisition stage downloads only the exact locked wheels from public PyPI.
It receives the two manifests, not project code, and does not resolve or follow
transitive dependency URLs. The offline test stage creates a private environment,
installs the downloaded wheels with hash verification, and checks dependency
closure while installing. A missing dependency fails instead of downloading it
or silently installing a different version. Tests and `main.py` run without
network access, as an unprivileged user, in a container with a read-only root
filesystem and bounded CPU, memory, process count, time, and captured logs.

Only verified, bounded output files are imported into a new workspace directory.
Observe the existing job after a lost response; do not resubmit an unknown
outcome. Cancel requests require confirmed terminal evidence. Service restarts
mark interrupted jobs unconfirmed and never replay them automatically.

This profile does not support source distributions/native compilation,
editable/Git/URL dependencies, custom indexes, Conda/Poetry/uv locks, notebooks,
arbitrary interpreter versions, or persistent servers. A requested capability
outside that scope must be reported accurately. A generated artifact is not a
published website until a separate publication succeeds.

## Installation and tests

Linux/WSL provisioning builds the npm and Python images and verifies both before
enabling the service. Previous npm-only configuration and saved jobs remain
readable. macOS project-service provisioning is not implemented by this change;
it does not alter existing inference or model selection on any platform.

The `Portal project build` CI job enables `ODS_TEST_PROJECT_NODE=1` and
`ODS_TEST_PROJECT_PYTHON=1` to run the actual Docker lifecycle tests in addition
to protocol, controller, service, and installer tests. Python coverage includes
hashed `humanize` acquisition, artifact import, invalid hashes, failing/empty
tests, offline execution, cancellation, and no replay after service recreation.
