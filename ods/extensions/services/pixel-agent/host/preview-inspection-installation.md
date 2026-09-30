# Preview inspection installation

The installer builds `Dockerfile.inspection` from a private four-file context:
the Dockerfile, hash-locked Python requirements, protocol and capsule. The
multi-platform Python base is pinned by manifest digest; Playwright 1.62.0 and
its dependencies are pinned by version and wheel hash. Only Chromium's
headless shell is installed. The installer validates Linux architecture,
non-root user, fixed entrypoint and protocol labels before recording the
immutable image ID. Runtime never resolves a tag or pulls an image.

Linux and WSL install a separate root broker, reachable only through
`/run/ods-pixel-inspection/control.sock`. The directory is root:ods-pixel mode0750
and the socket root:ods-pixel mode0660; the broker authenticates the exact owner UID
using the kernel peer identity. `CAP_DAC_READ_SEARCH` lets it read the owner's
0700 immutable preview tree. The publisher has no new Docker authority. The
broker receives no model commands, scripts, host paths or image arguments.

Native macOS installs the fixed helper and secret-free image configuration in
the existing protected, approved service bundle. The helper uses system
`/usr/bin/python3` with a fixed working directory and Python environment. Its
protected configuration pins the installer-approved Docker Desktop or Homebrew
Docker binary by SHA-256 and the owner's Docker Desktop or Colima Unix socket.
Native sandbox policy allows read access to the system Command Line Tools Python
libraries without adding write access. Its only publisher operation is the fixed snapshot export command in
`ods-pixel-workspace-preview`; the publisher receives no Docker socket.

New Linux configurations select `workspacePreviewInspectionTransport: unix`;
native configurations select `native`. Absent transport means disabled.
Model-budget reconciliation preserves this admission distinction. Installation
readiness requires the configured immutable image and owner-accessible broker
transport. A build/readiness failure fails installation; it is not silently
reported as an available browser capability. Native image inspection verifies
identity only; browser behavior is separately qualified in the capsule tests.

Uninstall validates installed helper/unit bytes and configuration ownership,
stops the broker before the publisher, then removes only fixed known artifacts.
Content-addressed Docker images remain ordinary build cache. Complete older
native service bundles remain readable for rollback and uninstall; a partial
inspection bundle cannot pass as a legacy bundle.

The installer creates a private build context and inherits the owner's umask.
With umask 077, staged inputs are mode 0600. The Dockerfile fixes the lock and
Python source permissions to 0444 inside the image so UID 65534 can read them;
the host's source and staging permissions are not widened. This uses the same
Docker BuildKit `COPY --chmod` support already used by bundled runtime images.

The private-context CI job runs the actual installer build, records mode 0600
inputs, checks imports and read-only image files as UID 65534, then verifies a
small page through the production isolated capsule. To reproduce on Linux/WSL
as an ordinary Docker-enabled user, run from `ods`:

```sh
ODS_INSPECTION_BUILD_TESTS=1 python3 -m pytest -q tests/test_preview_inspection_permissions.py
```

The opt-in test builds only the fixed inspector image. It does not install
services, change permissions on existing host files or publish an image.
