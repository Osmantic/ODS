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
  Confirmed bounded resources are removed after cancellation; durable job
  receipts remain as recovery evidence. A cancelled
  execution does not establish whether an artifact import completed before the
  crash; recovery does not import, delete or roll back workspace files.

## Validation and remaining work

A local Portal conversation acquired dependencies, passed 12 application tests,
built a Next.js project, published the output and passed four browser inspection
checks. That installation also included the framework-preview fixes in PR #6959;
this result does not establish that the new capability alone fixes Next previews.

Before declaring this workflow generally ready, validate natural-language tool
discovery, installed-service lifecycle, scoped Sandbox
approval, and macOS provisioning. Aggregate retained outputs and history remain
unbounded. The initial tool supports this specific npm workflow, not arbitrary host
package installation or every project ecosystem.

An isolated Docker regression kills its own controller subprocess during npm
execution and verifies that a replacement cancels the exact orphan without
replay or source changes. Negative tests cover foreign containers, absent
resources, a nonresponsive Docker client and policy revocation while queued.
This does not qualify a fresh machine installation or macOS/Sandbox execution.

## Execution storage and independent deadlines

New jobs use a hard-sized Linux tmpfs volume: 1 GiB per job by default, with
durable reservations capped at 2 GiB and two jobs for the installed controller.
Each volume also has a 65,536-inode ceiling so empty files are not unbounded.
The installer configuration can set `storageLimits` with integer `jobBytes`,
`totalBytes` and `maxJobs`; these are not model/tool arguments. Supported ranges
are 32 MiB–4 GiB per job, up to 8 GiB aggregate, and one–eight reservations.
Absent configuration uses the same bounded defaults, including older units.

Tmpfs is RAM-backed, not a disk quota or preallocated RAM. Admission checks the
Linux Docker engine's available memory and capacity, reserving worst-case
stage memory and extra headroom for every prior reservation, including orphans.
This is not a scheduler for unrelated Docker workloads. The existing service
lifetime lock permits one controller at its fixed private state directory;
unaccounted project volumes on the engine refuse new reservations.

A fixed, offline, unprivileged keeper retains the tmpfs mount across the
separate acquisition, test and build containers. It exits independently after
20 minutes. Each stage has a controller-owned native `timeout` (240 seconds,
then forced termination after five seconds); seeds have a 60-second deadline.
Container termination also removes descendants in its PID namespace. Stage
memory is limited to 2 GiB with no extra swap allowance; `/tmp` remains a
separate 256 MiB tmpfs. An observed owner cancellation remains distinct from
an observed timeout.

The filesystem returns ENOSPC at the job limit, including downloads, installed
dependencies, intermediate files and output. This is stronger than checking
artifact size after execution or polling disk usage. The keeper uses memory
only for its process; populated volume pages consume VM memory, and prior
writers' cgroup accounting must not be attributed to the keeper alone.

Controller failure never replays a stage or imports recovered files. Exact
image, command/deadline, isolation and tmpfs options must match before recovery
can stop resources. Legacy disk volumes, foreign commands or uncertain Docker
state remain unconfirmed; they are not silently adopted or stopped by label.
Reservations survive restart and remain charged until all corresponding
containers and the volume are proven removed. An exhausted/lost keeper can
lose tmpfs data; this is an interrupted job requiring a new authorized attempt,
not resumable project execution. Workspace sources and already imported
artifacts are preserved.

Unconfirmed Docker creation or CLI disconnection retains the bounded volume
and reservation: deleting it could allow a late `docker run` to auto-create an
ordinary disk volume. A crash before any expected container is visible can
therefore require operator investigation; absence alone never releases that
reservation. For a terminal job whose cleanup failed, an authorized cancel
request retries identity-checked cleanup asynchronously without changing the
recorded job outcome. Previous cleanup warnings remain in its receipt.

Qualification includes real ENOSPC, detached-descendant deadline termination,
crash cancellation, fresh copied-runtime startup/shutdown, and a real Next
export using about 433 MiB after build within the 1 GiB limit. This is one
fixture, not a guarantee that every Next or dependency workload fits. A real
32 MiB probe observed roughly 31.5 MiB less engine shared memory after exact
resource removal; unrelated VM workloads can affect such aggregate readings.
