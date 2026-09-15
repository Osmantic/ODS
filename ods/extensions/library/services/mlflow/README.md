# MLflow

Local experiment tracking, metrics and artifacts using MLflow 3.16.0 with its native HTTP authentication. Clients connect explicitly; this recipe does not change existing model routing, training jobs, traces or workflows.

## Install and connect

Set `MLFLOW_ADMIN_PASSWORD` and `MLFLOW_SECRET_KEY` in `.env`, then install MLflow from the Extensions library. Use different randomly generated values; `openssl rand -hex 24` produces a suitable value for either. The initial administrator username is `ods`.

| Variable | Purpose | Default |
| --- | --- | --- |
| `MLFLOW_PORT` | Host loopback UI/API port; container uses 5000 | `5051` |
| `MLFLOW_ADMIN_PASSWORD` | Initial administrator password, 16-128 printable ASCII characters without spaces | required |
| `MLFLOW_SECRET_KEY` | Stable CSRF signing secret, 32-128 printable ASCII characters without spaces | required |

The existing installation hook builds `ods-mlflow:3.16.0-r1` from the pinned official image. The official image omits its optional authentication dependencies, so the Dockerfile adds only Flask-WTF 1.3.0 and WTForms 3.2.2 with wheel hashes and runs `pip check`. Installation needs access to the container registry and Python package index. Runtime startup does not install packages. The installed Compose recipe uses the prepared image with `pull_policy: never`; a missing image requires rerunning setup, not pulling a similarly named image from a registry.

Open `http://localhost:5051`. A host MLflow client uses this tracking URI; a client on `ods-network` uses `http://mlflow:5000`. Configure that client with `MLFLOW_TRACKING_USERNAME` and `MLFLOW_TRACKING_PASSWORD`. Basic authentication is not transport encryption: this recipe publishes only on loopback and assumes a trusted Docker network. It does not configure a remote TLS endpoint.

Use the native user/permission APIs to create ordinary accounts and grant access to selected experiments. The default permission is `NO_PERMISSIONS`; authentication alone does not grant access to another account's runs and artifacts. The public `/health` endpoint only indicates server availability. One worker and disabled authentication caching keep credential/permission changes visible without a per-worker cache delay.

## Persistence and credentials

The existing host agent prepares `data/mlflow` for UID/GID 1000. Tracking metadata (`tracking.db`), authentication/permissions (`auth.db`) and artifacts (`artifacts/`) persist together in that directory. The runtime has a read-only root filesystem, dropped capabilities and bounded temporary storage. It uses two CPUs and 2 GiB memory by default, without a throughput guarantee.

The startup guard writes a private native authentication configuration and preserves literal percent signs in passwords. The initial administrator password seeds a new auth database only. Changing `MLFLOW_ADMIN_PASSWORD` later does not reset an existing account: rotate the password with MLflow's authenticated user API, then update clients. Preserve the signing secret with a protected backup. Administrators of `.env`, Docker configuration and the container can read the supplied credentials; the temporary auth configuration contains the seed password and the native process environment contains the signing secret.

Disable/re-enable/recreation retains data. For a consistent cold backup, stop MLflow and copy the complete data directory, including SQLite WAL files and artifacts, together with protected configuration and ownership. Restore the same pinned image first. Do not assume a migrated database can be downgraded. Removing the extension preserves data until explicitly purged.

## Scope and limits

MLflow telemetry is disabled. Workspace mode and remote Assistant access are disabled; native Assistant sandboxing is forced on, with no Docker socket or execution helper configured. This recipe qualifies tracking and artifact storage, not Assistant execution, model serving, gateway credentials, automatic training, cloud storage or tracing integrations. It does not impose a storage quota or delete old experiments automatically; monitor disk usage and use MLflow's lifecycle tools deliberately.

The opt-in `ODS_TEST_MLFLOW=1` regression exercises real authenticated experiment/run APIs, metrics, artifact permissions, password rotation and restoration from a stopped-data copy. Static tests cover installation image preparation and required credentials. Native Windows/macOS installs, concurrent writers, large artifacts, interrupted writes, upgrades between MLflow versions and browser-only administration require separate qualification.

Upstream: [MLflow 3.16.0](https://github.com/mlflow/mlflow/releases/tag/v3.16.0), [versioned auth configuration](https://github.com/mlflow/mlflow/blob/v3.16.0/mlflow/server/auth/config.py), [authentication documentation](https://mlflow.org/docs/latest/self-hosting/security/basic-http-auth/).
