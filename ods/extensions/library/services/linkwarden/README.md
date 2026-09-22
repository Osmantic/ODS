# Linkwarden for ODS

Linkwarden organizes shared bookmark collections, annotations and preserved page content. Its official web/worker image includes the browser used for archiving. This recipe adds private PostgreSQL and Meilisearch containers in the versions selected by upstream, counted together as one extension.

## Initial configuration

Set three different secrets in Extensions: `LINKWARDEN_DATABASE_PASSWORD`, `LINKWARDEN_AUTH_SECRET` and `LINKWARDEN_SEARCH_KEY`. The database password must be URL-safe letters/digits, at least 32 random characters, because the application uses a PostgreSQL connection URI. The search key needs at least 16 random characters. Keep all secrets stable across restarts.

Open `http://localhost:11023` after enabling. Create the intended accounts, then set `LINKWARDEN_DISABLE_REGISTRATION=true` if no further public signup is wanted. `LINKWARDEN_AUTH_URL` defaults to `http://localhost:11023/api/v1/auth`; update the full value if changing `LINKWARDEN_PORT` or using a reverse proxy. It must retain the `/api/v1/auth` suffix.

Create a collection, save a public webpage and inspect its archived representation before relying on it. Pages requiring remote authentication, blocked by the source website or using unsupported content can fail to archive. This setup does not export the user's browser cookies or existing sessions.

## Models and search

The configured OpenAI-compatible AI route uses the existing ODS gateway and `ods/current`, with `LITELLM_KEY`. It does not select another model or modify its context size. AI tagging quality and supported operations depend on the currently selected model; bookmarking and full-text search have their own local storage/search services. Configure AI features in the application deliberately and verify them with your model.

The dedicated Meilisearch index uses upstream's 1.13.3 version. It does not share indexes or force a downgrade of the standalone ODS Meilisearch extension. Source repository identity and capability are distinct from Karakeep; choose the bookmark workflow you prefer rather than enabling both unnecessarily.

## Storage and lifecycle

The named volumes are `linkwarden-data` for archived content under `/data/data`, `linkwarden-db-data` for PostgreSQL and `linkwarden-search-data` for the search index. Preserve all three and the configuration when backing up. Use PostgreSQL's consistent backup procedure or stop the stack before a complete volume snapshot. A container restart is not a database major-version migration.

The updated ODS host agent stops the web service and both namespaced companions on disable, retaining data. Shared services such as LiteLLM keep their own lifecycle. Only the web port is published, on loopback by default. Database and search ports remain internal. Upstream's private-network access restrictions are not relaxed by this recipe.

## Platforms and validation

All three pinned images have Linux amd64/arm64 variants. Windows and macOS use Linux-container Docker. Archiving/search run on CPU; inference uses the existing ODS backend. Resource caps total 5 GB for the application stack, separately from the model and other ODS services.

The web readiness probe requests the actual `/login` route using the image's Node runtime; search and database have separate health checks. Runtime validation remains pending: account creation, archive generation, annotations, search, configured AI actions, model changes and data persistence after disable/enable. The configuration and installation checks do not prove these user flows.

Upstream: [Linkwarden](https://github.com/linkwarden/linkwarden), AGPL-3.0. The [official Compose file](https://github.com/linkwarden/linkwarden/blob/main/docker-compose.yml) defines the deployment; image digests and architecture evidence are in `upstream.json`.
