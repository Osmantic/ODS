# Managed runtime diagnostics

`pixel_ods_project_build` supports an explicit, asynchronous diagnostic job:

```json
{"action":"diagnose","runtime":"python"}
```

Use `runtime: "npm"` for the Node profile. Observe the returned `jobId` with the existing `observe` action. Only these two installed profiles are supported. The request accepts no command, path, package name, image, or storage settings.

This job checks the **managed executor**, not the conversational sandbox. A missing sandbox executable does not establish that a managed runtime is missing, and an executor result does not prove sandbox availability. `chatSandboxVerified` is always false.

The fixed program checks Node, npm, Python, pip and private scratch write/read/unlink. The Python profile additionally creates a real isolated venv with offline pip bootstrap. The npm profile reports its venv check as unsupported. Individual checks report ready, missing, unavailable, incompatible or unsupported; other successful checks remain visible when one check fails. A denied request does not allocate resources.

No project source or arbitrary command runs. The container is nonroot, has no external network or host mounts, uses the existing read-only root and resource restrictions, and has a native 30-second deadline plus a 5-second kill grace. Its job-owned tmpfs is at most 64 MiB (or the owner's smaller configured job limit), with the same inode bound and durable global reservation as build jobs. The existing 2-GiB stage cgroup and 256-MiB `/tmp` cap also apply; these are bounds, not preallocated RAM. There is no inter-stage keeper for this single-stage job.

Normal completion removes the exact authenticated container and volume and releases the reservation. Unknown execution retains bounded storage and reports unconfirmed cleanup. Stop and recovery use the original immutable image, fixed argv, isolation and job identity. A controller restart does not replay a diagnostic. If no authentic container evidence exists after a crash, recovery stays unconfirmed; it never deletes a container using a label alone. A terminal cleanup failure can be retried through `cancel` without rerunning the probe or changing its recorded execution outcome.

The structured `nextAction` distinguishes cleanup recovery, runtime configuration inspection, and preparation of a locked project. Dependencies are installed only through the existing managed workflow: npm requires a matching package lock; Python requires exact versions and verified wheel hashes (including transitive dependencies), a main script and tests. Python wheel selection should first use the installed-image `capabilities` receipt. Unsupported package managers, arbitrary tools and host installation are not added by this diagnostic.

This capability does not bypass an earlier destructive-operation refusal in the same assistant run. The existing terminal tool fuse still applies. Earlier observed diagnostic facts can be reported while the refused action remains blocked.
