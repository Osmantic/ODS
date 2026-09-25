# Trilium Notes — project knowledge base

Install and enable in ODS Extensions, then open `http://localhost:11037`. Complete Trilium's first-run database/password setup. There is no preset account password. Organize notes in project branches, link related pages and add attachments through the application. This is a full hierarchical knowledge base, not another name for the catalog's simpler Memos notebook.

## Storage and startup

`trilium-data` persists the complete application data directory at `/home/node/trilium-data`, including the database, configuration and application-managed backups. Do not back up only exported Markdown if you need full restoration of note structure, metadata and attachments. Use Trilium's backup facilities or stop writes before a complete volume copy; keep recovery copies outside the same volume.

The official image normally starts as root, fixes ownership and switches user. This recipe prepares the data directory in an image layer and starts `main.cjs` as UID/GID 1000 instead. `no-new-privileges` stays enabled. Fresh Docker named volumes inherit the prepared ownership, avoiding host-specific drive/path/UID assumptions. Existing volumes with different ownership need a deliberate migration; recreating the image does not repair them automatically.

The main image is pinned to 0.105.0 by digest and includes amd64, arm64 and ARM variants. Windows/macOS use Linux containers in Docker Desktop; Linux uses Docker Engine. No GPU or loaded model is required. The 1 GB container limit may need tuning for large knowledge bases or custom scripts.

`TRILIUM_PORT` changes the published host port, while this recipe explicitly keeps the internal listener on 8080. Only loopback is exposed. No host folders, Docker socket or external synchronization server is supplied. Disabling the extension preserves the data volume.

## Project use and boundaries

Trilium stores its own note database; it does not automatically mirror Portal's Playground folder or publish project files. Import/export is an explicit workflow. If a project requires synchronization or API access, configure it deliberately using the application's supported credentials. Notes containing scripts should be treated as executable project content, not as harmless prose from any source.

Built-in optional integrations, external synchronization and AI features are not configured by this recipe. In particular, installing this note application does not route prompts to an arbitrary provider or select another ODS model. Remote access requires an intentional HTTPS/authenticated deployment; do not disable login to simplify it.

Keep the password and recovery plan available before enabling protected notes. Restoring a volume or resetting a login does not guarantee recovery of encrypted/protected content without the required secret. Review upstream recovery instructions before changing credentials on existing data.

## Validation

The health check invokes the image's actual bundled `docker_healthcheck.cjs`, which requests `/api/health-check`. It checks server response, not note editing, password setup or synchronization. On the target machine, create a project branch, link two notes, attach a small file, restart and verify that all remain accessible.

Official image configuration, startup script and versioned health source were inspected. ODS schema/Compose/staging checks are separate from runtime. Image build, first-run setup, browser editing and recovery remain pending; no note or account was created during preparation.

- [Versioned source and AGPL-3.0 license](https://github.com/TriliumNext/Trilium/tree/v0.105.0)
- [Official image](https://github.com/TriliumNext/Trilium/blob/v0.105.0/apps/server/Dockerfile)
- [Startup behavior](https://github.com/TriliumNext/Trilium/blob/v0.105.0/apps/server/start-docker.sh)
- [Health implementation](https://github.com/TriliumNext/Trilium/blob/v0.105.0/apps/server/src/docker_healthcheck.ts)
