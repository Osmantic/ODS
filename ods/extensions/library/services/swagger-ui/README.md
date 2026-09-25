# Swagger UI for ODS

Interactive OpenAPI documentation using the actual Swagger UI distribution.
The ODS entry page opens a user-selected document rather than an example API.

## Opening project specifications

Open `http://localhost:11054`. Choose either:

- **Open JSON**: select an exported OpenAPI/Swagger JSON file, up to 10 MiB.
  It is read in the browser, not uploaded to this container. The extension checks
  the declared specification version and Swagger UI renders validation errors.
- **Load URL**: provide the HTTP(S) URL of the project's JSON or YAML OpenAPI
  document. The browser must be able to reach it and the server must allow CORS
  for this UI origin where required. Credentials embedded in URLs are rejected.

For example, export your framework's generated OpenAPI document and open that
file; do not treat a handwritten placeholder as the project's actual API. The
extension does not invent paths, copy Portal credentials or infer authorization.
Local JSON documents with relative external references should instead be served
from their original URL so those references resolve. Relative API server URLs
can also need an absolute server address in the exported document.

Swagger UI's **Authorize** and **Try it out** features operate against the API
declared by the document. Execute requests only when you intend to call those
endpoints; this is not a mock server. A browser cannot resolve Docker-only
service names such as `dashboard-api`; use an endpoint reachable from that
browser. Loading documentation does not establish API connectivity or permission.

## Data and configuration

No specification or credentials are stored in a server volume. Reloading the
page requires opening the document again. Authorization persistence and query
configuration are disabled, and no credentials are prefilled. The online
swagger.io validator is disabled; document rendering does not automatically
upload the specification to that service. The selected URL, external references
and explicitly executed API calls can still make network requests.

The custom entry page supports browser-local JSON and URL loading. It does not
run upstream Docker environment configurators, so `SWAGGER_JSON` and related
upstream startup variables are not the interface for this recipe. No host project
directory is mounted implicitly. For team hosting or persistent documentation,
publish your actual specification at a controlled URL and load it explicitly.

## Runtime and verification

Apache-2.0 release **v5.33.0**, official image pinned by digest. The published
manifest includes amd64, arm64 and additional Linux architectures. Windows and
macOS require a Linux Docker engine. Local nginx serves the packaged JS/CSS
assets as UID/GID 101, with a read-only root filesystem and a bounded temporary
directory. Half a CPU and 256 MiB RAM are assigned to the static server; rendering
large documents consumes browser memory separately.

The health check requests the static entry page. That proves HTTP serving only,
not valid API documentation or successful API calls. **Image build, browser
rendering, CORS/authenticated API access and interactive requests remain
runtime-unverified.** Syntax/schema/Compose/staging checks are separate. No
browser, model, API operation or application container was started for this work.

- [Versioned source](https://github.com/swagger-api/swagger-ui/tree/v5.33.0)
- [Swagger UI configuration](https://swagger.io/docs/open-source-tools/swagger-ui/usage/configuration/)
- [Installation](https://swagger.io/docs/open-source-tools/swagger-ui/usage/installation/)
