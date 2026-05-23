# Linux portability

How to run Dream Server reliably across different Linux machines (with or without a GPU), recover from a bad reinstall, and work with extensions.

## Installer

- **Entry:** `install.sh` → `install-core.sh` — see [INSTALLER-ARCHITECTURE.md](INSTALLER-ARCHITECTURE.md).
- **CPU-only systems:** With a capability profile, set `CAP_LLM_BACKEND=cpu` so the installer keeps CPU inference (see `02-detection.sh`).
- **Re-running the installer:** If a previous run left directories owned by container users, the installer checks that `config/*` and `data/*` are writable before copying files, uses `rsync` without preserving foreign ownership, and tells you to `chown` if something is still blocked (`06-directories.sh`).
- **Fedora/RHEL SELinux:** Compose bind mounts use shared `:z` labels so enforcing SELinux can relabel Dream Server repo/data paths for containers. If you add custom bind mounts on SELinux systems, include `:z` as well.

## Extensions and integrations

Services are declared under `extensions/services/<name>/manifest.yaml` for dashboard health and feature metadata. Schema: `extensions/schema/service-manifest.v1.json`. To add or change a service, follow [EXTENSIONS.md](EXTENSIONS.md).

## Checklist on a new Linux machine

1. Docker and Compose v2 work; your user can run containers (e.g. member of the `docker` group).
2. `./install.sh --dry-run` finishes without errors.
3. Before or after install, run **`./scripts/linux-install-preflight.sh`** (or `./dream-preflight.sh --install-env`) for a structured environment report with stable check IDs and optional `--json` output.
4. After a real install, run `./dream-preflight.sh` (service health) and `scripts/dream-doctor.sh` if you use them.

More context: [LINUX-TROUBLESHOOTING-GUIDE.md](LINUX-TROUBLESHOOTING-GUIDE.md) (ID-indexed fixes), [FIELD-INSTALL-REPORT-LINUX.md](FIELD-INSTALL-REPORT-LINUX.md) (bug report template), [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

Extension PRs that touch catalog vs core: [EXTENSION-PR-BRANCHING.md](EXTENSION-PR-BRANCHING.md).
