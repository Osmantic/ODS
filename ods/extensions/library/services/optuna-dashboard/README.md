# Optuna Dashboard — experiment inspection

Install and enable in ODS Extensions, then open `http://localhost:11034`. The dashboard reads persistent Optuna studies from `sqlite:////data/studies.db`. An empty study list is expected on a fresh installation; installation does not launch training or fabricate trial results.

## First study

The image contains an explicitly labeled, synthetic CPU example. Run it only when desired:

```sh
docker exec ods-optuna-dashboard python /app/ods_examples/study.py --trials 20
```

Refresh the dashboard and open `ods-cpu-demonstration` to inspect parameter values and objective history. The script minimizes a small mathematical function; it is not an AI benchmark or evidence of model quality. Repeating it appends the requested number of trials to the same study instead of replacing saved work. No external download, inference endpoint or GPU is used by the example.

For a real experiment, write the objective and explicitly attach its writer to the same storage. A host process cannot access `/data/studies.db` simply by using that container path. Either run the writer in the appropriate container/workspace with a deliberate volume mapping, or plan an Optuna-supported shared database configuration. This recipe does not silently share Portal files or discover experimental results elsewhere on the host.

## Data and platform

- The official MIT-licensed 0.21.0 dashboard image is pinned by digest for amd64 and arm64. A small derived layer creates UID 1000 and the owned data directory. Runtime is nonroot with `no-new-privileges`.
- `optuna-dashboard-data` retains study storage. Named volumes avoid Windows/Linux/macOS host-path assumptions. Windows/macOS use Docker Desktop Linux containers; Linux uses Docker Engine. The dashboard requires no GPU; the requirements of your objective are separate.
- SQLite is suited to local experiments with limited concurrent writers. It is not a distributed experiment queue. Do not put the SQLite volume on network storage or assume many concurrent training workers will work without database contention. PostgreSQL/MySQL need an intentional storage migration and configuration.
- Back up the database with writers stopped for a consistent copy. Disabling the extension preserves it. The dashboard may alter or delete studies through its API/UI, so a backup is needed before destructive experiment cleanup.
- The container's 1 GB memory limit also bounds an example launched with `docker exec`. Real objective memory/CPU/GPU needs must be configured independently; installing a dashboard does not provide a training environment.

## Access and health

The upstream UI/API is unauthenticated. The published port is loopback-only and the container is on the trusted ODS network. Do not expose it remotely without an authenticated reverse proxy. No Docker socket, host drive or model credential is mounted. Changing `OPTUNA_DASHBOARD_PORT` changes only the host port; the internal server stays on 8080.

The health probe reads `/api/studies` with a bounded Python HTTP request, covering storage-backed API availability rather than only static frontend files. It does not validate the objective, trial correctness or browser charts.

Registry manifests, official Dockerfile/license and study route were inspected. ODS schema/Compose/staging checks remain separate from runtime. Image build, example execution, chart rendering, concurrent access and recovery still need runtime validation; the example was not executed during integration.

- [Versioned project and MIT license](https://github.com/optuna/optuna-dashboard/tree/v0.21.0)
- [Official image definition](https://github.com/optuna/optuna-dashboard/blob/v0.21.0/Dockerfile)
- [Storage-backed API](https://github.com/optuna/optuna-dashboard/blob/v0.21.0/optuna_dashboard/_app.py)
