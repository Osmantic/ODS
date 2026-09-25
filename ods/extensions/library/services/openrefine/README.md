# OpenRefine

Clean inconsistent CSV/Excel/JSON data, cluster similar values, apply GREL transformations and export cleaned datasets. Project history can be exported as operations for another compatible dataset. This is the upstream OpenRefine application, not an ODS-generated spreadsheet editor.

## Packaging

The official 3.10.1 Linux archive is SHA256-verified and runs using its original launcher on pinned Eclipse Temurin Java 21. The app uses BSD-3-Clause; dependencies and bundled extensions retain their own licenses in the distribution. No model, GPU, account or remote AI API is required.

Docker publishes port 3333 only at `127.0.0.1:${OPENREFINE_PORT:-11081}`. The internal ODS service address is `http://openrefine:3333`. This is a single-user service without built-in authentication: other containers sharing `ods-network` can access it. Do not expose it to a LAN/public proxy without an authenticated boundary.

## Data workflow

1. Open the extension and import the dataset using the file picker. Host directories and Playground are not mounted implicitly.
2. Inspect facets, cluster values and apply transformations. Review changes using OpenRefine's Undo/Redo history.
3. Export CSV/TSV/JSON or a complete project archive. Place the exported file in the intended ODS project explicitly; installing this recipe alone does not give Portal filesystem access or create a project association.

Project state lives in the `openrefine-data` volume under `/data/projects`; the same volume retains the launcher/Jython cache. Back up the volume with the service stopped, or export project archives through OpenRefine. Changing the image does not reset existing projects. Shutdown allows 60 seconds for saving.

Reconciliation services, URL imports, Wikibase uploads and database connections are optional upstream features that can contact external systems. They are not configured or invoked by this recipe. Decide which data to send and supply relevant credentials only when using those features.

## Resources and platform status

The JVM starts at 128 MiB and is bounded to 1536 MiB heap inside a 2 GiB container limit, with two CPUs. Large datasets may need higher heap and container limits together. No inference service is started or model context changed.

The base image provides Linux amd64/arm64 variants for Docker on Windows, Linux and macOS. The archive is a Java distribution without a bundled JRE; bundled native dependency compatibility still requires runtime verification on each architecture. Schema/staging checks do not establish import/export or platform functionality. Image build, first startup, project persistence and real data operations remain untested.

Upstream: https://github.com/OpenRefine/OpenRefine/releases/tag/3.10.1
