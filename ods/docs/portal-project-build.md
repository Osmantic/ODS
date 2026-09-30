# Managed project builds in Portal

The `pixel_ods_project_build` tool runs dependency acquisition, tests and builds
for an existing npm project in the owner's Pixel workspace. Linux and WSL
installations provision its owner service and expose the tool only after the
service reports the expected image and execution policy.

The current adapter requires runtime-verified Full Access. It does not enable
permissions, approve protected shell plans or install npm packages on the host.
macOS provisioning and scoped Sandbox approval are not implemented yet.

## Execution and results

- The project must contain a matching npm v3 lockfile with supported public npm
  dependencies. Git, local-file dependencies and workspaces are rejected.
- Acquisition uses `npm ci --ignore-scripts`. Tests and builds run without a
  network in separate stages of a pinned Node 22 container image.
- Source enters a private Docker volume through a bounded snapshot. Containers
  receive neither host bind mounts nor the Docker socket.
- A successful build imports bounded regular files into a new
  `ods-builds/<job>/site` directory. It never replaces the source project.
- Publication and browser inspection remain separate tools. Build success alone
  is not evidence that a preview was published or inspected.
- Calls carry trusted session and call identities. A lost response must be
  observed; it must not trigger resubmission under a new identity.
- Cancellation is confirmed only after execution stops. Restart does not replay
  an interrupted build automatically.
- After a crash, an authorized cancellation can stop orphaned containers whose
  immutable IDs, image, command, isolation settings and private job volume are
  verified. Recovery has a total deadline and runs in the controller worker;
  missing, foreign or unresponsive resources leave cancellation unconfirmed.
  Stopped containers and volumes remain as recovery evidence. A cancelled
  execution does not establish whether an artifact import completed before the
  crash; recovery does not import, delete or roll back workspace files.

## Validation and remaining work

A local Portal conversation acquired dependencies, passed 12 application tests,
built a Next.js project, published the output and passed four browser inspection
checks. That installation also included the framework-preview fixes in PR #6959;
this result does not establish that the new capability alone fixes Next previews.

Before declaring this workflow generally ready, validate natural-language tool
discovery, installed-service lifecycle, scoped Sandbox
approval, and macOS provisioning. Disk quotas and history retention also remain
open. The initial tool supports this specific npm workflow, not arbitrary host
package installation or every project ecosystem.

An isolated Docker regression kills its own controller subprocess during npm
execution and verifies that a replacement cancels the exact orphan without
replay or source changes. Negative tests cover foreign containers, absent
resources, a nonresponsive Docker client and policy revocation while queued.
This does not qualify a fresh machine installation or macOS/Sandbox execution.
