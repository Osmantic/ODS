# ChartDB for ODS

Design database schemas visually or import metadata from an existing database.
This integration provides the upstream diagram editor; it does not open database
connections or execute schema changes on your behalf.

## Local deployment

- Official ChartDB **1.20.1**, AGPL-3.0, pinned by digest for amd64 and arm64.
  Docker's Linux engine provides the same deployment on Windows, Linux and macOS.
  No model, GPU, database server or cloud account is required for local diagrams.
- Open `http://localhost:11042`. Choose the correct database dialect and create a
  diagram, or use the application's metadata import instructions. Run any source
  query yourself against the intended database and import the resulting metadata.
  A diagram is not proof that an actual database was modified.
- A small image layer configures Nginx on port 8080 as UID/GID 101 with a read-only
  root filesystem and a bounded temporary filesystem. The UI binds loopback on
  the host. Limits are half a CPU and 256 MiB for the static web server; browser
  diagram memory is separate.

## Where your diagrams are saved

ChartDB uses **IndexedDB in the browser**, verified in its versioned storage
provider. There is no server-side diagram database to put in a Docker volume.
Reloading the same browser profile/origin retains diagrams; another browser,
private session, port or hostname has different storage. Clearing site data can
remove them. Export project files regularly and import them to move between
devices or preserve them in a Portal project. Container restarts do not provide
or replace a backup of browser data.

The recipe disables analytics and hides the ChartDB Cloud promotion using its
supported public runtime configuration. AI credentials and endpoints are empty.
It deliberately does not embed the private ODS gateway key in `/config.js`, which
is readable by every browser opening the app. AI-assisted features need a separate
authenticated integration; ordinary diagram editing and deterministic exports do
not require them. No selected model or context setting is changed.

There is no separate login for the static editor. Do not treat it as a shared
multi-user diagram repository or assume browser diagrams synchronize to other
ODS users. Automatic Portal project association remains separate work.

## Verification status

Image provenance/platforms and the upstream storage/runtime configuration were
inspected. Schema, Compose resolution and ODS staging are separate checks.
**Image build and browser workflows remain runtime-pending.** The HTTP readiness
probe only checks delivery of the UI. Local acceptance should create/import a
diagram, export it, reload the same origin and reimport into a separate browser
profile. No live database or model was accessed during preparation.

- [Versioned source and license](https://github.com/chartdb/chartdb/tree/v1.20.1)
- [Browser storage implementation](https://github.com/chartdb/chartdb/blob/v1.20.1/src/context/storage-context/storage-provider.tsx)
- [Runtime configuration](https://github.com/chartdb/chartdb/blob/v1.20.1/default.conf.template)
