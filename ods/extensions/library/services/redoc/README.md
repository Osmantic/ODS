# ReDoc for ODS

Read an OpenAPI specification as structured documentation with operations,
schemas and code samples. This is the MIT community standalone renderer, not
Redocly's commercial platform. Unlike Swagger UI's request execution workflow,
this entry focuses on reading reference documentation.

Install `redoc` and open `http://localhost:11062`. `REDOC_PORT` overrides the
loopback host port. Choose a local OpenAPI/Swagger JSON file up to 10 MiB, or enter
an HTTP(S) specification URL. No default Petstore or other example API is loaded.
Local YAML files are not parsed by this selector; serve YAML through a URL or
export JSON. Relative external references need a served URL with an appropriate
base and browser-accessible CORS policy. Embedded URL credentials are rejected.

Documents are selected in the browser, not uploaded to an ODS database. Referenced
schemas, images and URLs can cause browser network requests. Sanitized description
rendering is enabled; no API keys or conversations are supplied automatically.
Selection is not persisted. Keep your original specification; refreshing the
page requires choosing it again. The original SPEC_URL/PAGE_TITLE environment
template is replaced by this selector and is not supported here.

Pinned official v2.5.3 bundle, separately pinned nginx runtime, nonroot UID 101,
read-only filesystem and bounded temporary storage. No persistent volume, model
or GPU is required. Published assets support Linux amd64/arm64; use Docker Engine
on Linux or Docker Desktop Linux containers on Windows/macOS.

HTTP health confirms the static server answers. Image build, browser rendering,
reference resolution, selection races and platform execution remain runtime-pending.
No application containers were started during integration.

Source: https://github.com/Redocly/redoc/tree/v2.5.3
