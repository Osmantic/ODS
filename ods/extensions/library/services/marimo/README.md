# marimo — reactive Python notebooks

marimo 0.24.2, Apache-2.0. Upstream: https://github.com/marimo-team/marimo/tree/0.24.2. This recipe installs the exact PyPI version and resolved dependency versions on a pinned Python 3.13 base. The separately published upstream `latest` image was still labeled 0.24.0 when inspected, so it is not used here.

## Editor access

Set required `MARIMO_TOKEN` to 64 random hexadecimal characters. Open `http://localhost:11110/` and authenticate with that token. The native token protection is enabled; the unprotected upstream Docker example is not used. The token is supplied through a private temporary file rather than a process argument and removed from the server's inherited environment. Token rotation requires updating ODS configuration/recreating the container and signing in again.

Create/import notebooks in `/workspace`. marimo stores notebooks as Python source and updates dependent cells reactively. No demonstration notebook, dataset, model provider, cloud account or Portal conversation is loaded automatically. The editor can run Python with container access; this is a single-owner development environment, not a multi-user execution sandbox.

## Python packages and project files

The base contains marimo and its required dependencies, not every data-science library. Native package management starts with pip, with user packages under `/data/python`; explicitly choose and pin notebook dependencies. The server uses one shared Python environment (`--no-sandbox`), so conflicting notebook dependencies require separately managed environments/deployments. Package compatibility on ARM/AMD64 depends on the packages selected; GPU frameworks are not implicitly installed or accelerated.

Only the named workspace volume is visible by default. Use explicit file upload or a deliberately configured project mount to work with host files. It does not automatically see the ODS Playground folder. Optional editor AI features require an explicit provider connection and compatible model; this recipe does not overwrite ODS's selected model or context limit.

## Persistence and resource scope

`marimo-workspace` preserves notebooks/data at `/workspace`; `marimo-data` preserves settings and user Python packages. Keep both in backups, excluding only rebuildable caches intentionally. The root filesystem is read-only; the app runs as UID/GID 1000 with two CPUs, two GiB memory and 256 MiB temporary space. Kernels share these limits. Larger data operations need deliberate resource sizing.

Host port 11110 is loopback-only, with token authentication also protecting access from ODS network peers. For remote access, configure HTTPS and the correct WebSocket proxy behavior. Keep the editor token private; any holder can execute code in this environment.

## Compatibility and verification

Pinned Linux Python base targets Docker on Windows/Linux/macOS, amd64/arm64; binary-only dependency installation rejects unsupported wheels instead of silently building an unknown backend. No GPU or model required. Native `/health` checks server availability, not notebook computation or dependency compatibility.

Schema/package checks do not establish runtime completion. Image build, token login, editing/reactivity, package installation, restart/restore and platform behavior remain pending. No notebook code, server or model was run during preparation.
