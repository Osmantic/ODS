# Karakeep for ODS

Karakeep saves bookmarks, archived page content and attachments, with full-text retrieval and model-assisted tagging. This integration supplies the upstream application's web/worker container, its Chromium companion and the Meilisearch version specified by its deployment. These companions belong to this extension; they are not additional catalog applications.

## Configuration and first use

In Extensions, configure two different persistent random secrets before enabling:

- `KARAKEEP_AUTH_SECRET`: session-signing secret, generated from at least 36 random bytes.
- `KARAKEEP_SEARCH_KEY`: private index key, at least 16 random characters.
- `KARAKEEP_URL`: the URL you actually open, default `http://localhost:11019`. Update it if changing `KARAKEEP_PORT` or using a reverse proxy; it controls authentication redirects.
- `KARAKEEP_DISABLE_SIGNUPS`: initially `false` for account creation; set `true` after creating your intended accounts.

Enable the ODS model gateway dependency and Karakeep. Open the extension, create your account and save a public webpage. Check its crawl status, extracted content, search results and generated tags separately. No cloud account is required. Browser authentication in Karakeep is separate from the ODS owner profile.

## Model selection

Text tagging and chat address the local LiteLLM gateway with `ods/current`, using the existing `LITELLM_KEY`. No model ID or server context limit is overwritten. One inference worker avoids launching several tagging jobs concurrently on a smaller machine. The worker timeout allows slower local inference, but does not guarantee that every model produces useful tags.

Strict structured-output mode is disabled because it is not supported by every local model. Image inference also points at `ods/current` and requires a vision-capable selected model; a text-only model cannot describe images. OCR stays on the application's non-LLM path. Automatic embeddings and semantic search are disabled because a chat model is not an embedding model. Full-text search remains enabled. Configure a verified embedding endpoint, model and matching dimensions before enabling semantic indexing; do not reuse `ods/current` for that purpose.

## Data and dependencies

`karakeep-data` contains the SQLite application database and assets under `/data`. `karakeep-search-data` contains its index under `/meili_data`. Stop the extension before taking a consistent backup, retain both volumes and the session/index secrets, and follow upstream upgrade instructions before changing the pinned Meilisearch version. The standalone ODS Meilisearch extension is not reused because its version and indexes have an independent lifecycle.

Only the web port is published, on loopback by default. Chromium's debugging port and the search endpoint have no host mappings. Crawling makes network requests to saved URLs; upstream internal-address restrictions remain in place. There are no host filesystem bind mounts or Docker socket access.

The host agent must include the companion-stop fix shipped with this ODS change: disabling Karakeep stops `karakeep`, `karakeep-chrome` and `karakeep-search`, while leaving the shared model gateway running. Older agents stopped only the main service; refresh the host agent together with this catalog update.

## Platforms and validation

All three pinned images provide Linux amd64 and arm64 variants. Windows and macOS use Linux containers through Docker; Apple Silicon can use arm64. Web, search and crawling run on CPU. Model inference follows the existing ODS backend and its supported hardware. Budget roughly 4 GB for this application stack in addition to the model and ODS services; the recipe caps each component separately.

Web readiness probes `/api/health`; startup waits for the search health endpoint and starts the browser companion. HTTP readiness does not prove successful crawling, authentication or inference. Runtime validation remains pending: verify account setup, crawling, full-text search, text tagging with the current model, model switching and persistence after disabling/enabling.

Upstream: [Karakeep](https://github.com/karakeep-app/karakeep), AGPL-3.0. Review [deployment](https://docs.karakeep.app/installation/docker/) and [configuration](https://docs.karakeep.app/configuration/environment-variables/) when upgrading. Digests and architectures are recorded in `upstream.json`.
