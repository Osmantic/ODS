# Diagnose CPU, memory and I/O contention

`GET /api/resources/pressure` exposes Linux Pressure Stall Information (PSI)
through the dashboard API's existing authenticated access path. Supply the same
Bearer API key used for other dashboard diagnostics. No Docker socket access,
host-agent command, workload restart or extra package is required.

The version 1 receipt reports the API runtime's kernel, with an explicit
`scope: "api-runtime-kernel"` and UTC `captured_at`. Each `resources.cpu`,
`resources.memory` and `resources.io` object contains:

- `available`: whether that resource's PSI file could be read.
- `some`: `avg10_percent`, `avg60_percent`, `avg300_percent`, and cumulative
  `total_us`. These measure time stalled, not percent CPU utilization or bytes.
- `full`: the same fields for simultaneous stalls of all non-idle tasks.
  CPU `full` is always null because that metric is undefined at system scope.

For example, `io.some.avg10_percent: 25` means at least some tasks spent about a
quarter of the recent ten-second window stalled on I/O. This can help explain
slow model loading or indexing even when CPU use is low. Inspect memory and I/O
full pressure alongside application latency; this API sets no universal alert
threshold and takes no automatic action.

Missing PSI support is `available: false`, `reason: "not_available"`, with null
measurements. Permission denial uses `reason: "permission_denied"`. Other
resources remain readable. Malformed kernel data yields HTTP 502 instead of
invented healthy zeroes. Missing `full` rows remain null. Genuine zero averages
are returned as measurements when the resource is available.

The kernel files are read sequentially on a worker thread; this is a short
snapshot, not an atomic sample of all three resources or a historical store.
There is no polling loop, trigger registration or mutation. Cumulative totals
reset with the observed kernel; compare deltas only within the same boot and
scope.

On native Linux this describes the kernel visible to the API. On Docker Desktop
or WSL it can describe the Linux VM rather than the physical Windows/macOS host.
It is not a per-container or per-service attribution API. Native Windows/macOS
without `/proc/pressure` report unavailable. These distinctions remain explicit
even when the API is used by an operator script or an authenticated monitoring
workflow. Live validation covers Linux amd64 under WSL; other host semantics
must be verified before making host-specific capacity claims.

The format and definitions follow the
[Linux kernel PSI contract](https://docs.kernel.org/accounting/psi.html).
