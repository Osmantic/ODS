# Swagger Editor

Edit and validate OpenAPI documents while previewing their API documentation. Swagger UI and ReDoc render documents; this extension provides the authoring interface. It uses the current Editor 5 image rather than assuming the old master-branch v4 documentation describes the current registry tag.

## Use with a project

Open http://localhost:11137. Import the project's OpenAPI YAML/JSON file using the native file menu, edit it, review diagnostics, then export the result back to the project and review its diff. Importing a file does not grant ongoing access to its folder. Installation does not create or modify a project, run a backend or automatically synchronize a schema.

Editor content persistence belongs to the browser profile and origin. Export important changes; clearing browser storage or changing the port/profile can lose local state. There is no document database or server-side document volume to back up. Native example documents, if displayed, are examples rather than a running ODS API.

Imports from URLs, referenced schemas and API execution depend on the browser's network, CORS and selected document. A container-only hostname may be unreachable from the browser. Native actions that contact external APIs or generators must be chosen for the actual project; installing this service neither configures credentials nor guarantees offline operation for those actions. Avoid embedding secrets in schema files.

## Packaging

The official unprivileged image supplies the built Editor 5 assets. ODS serves them using nginx on port 8080 as UID/GID 101 with a read-only root, bounded temporary paths and loopback-only host binding. JavaScript/WebAssembly workers retain their real asset paths and MIME types. Missing static assets return 404 rather than an HTML fallback. The recipe exposes no proxy endpoint, server file upload or host filesystem mount.

One CPU and 256 MiB bound the static server; browser memory for large specifications is separate. No model or GPU is required. Root HTTP health verifies static serving, not editor worker execution or correctness of an OpenAPI contract. There is no server login; remote access needs deliberate ingress/access control.

## Provenance and validation

Apache-2.0 Swagger Editor, official v5 multiarchitecture image pinned by digest. The image labels do not expose the exact Editor patch version; nginx's 1.31.5 label is not an Editor version. amd64/arm64 images serve the same assets through Linux-container runtimes on Windows, Linux and macOS. Browser compatibility and large-document behavior still require runtime validation.

Build, browser loading/workers, OpenAPI 3.1 editing, import/export and each platform remain unverified. No containers, browsers or models were started.

Sources: [current source and Docker instructions](https://github.com/swagger-api/swagger-editor), [license](https://github.com/swagger-api/swagger-editor/blob/main/LICENSES/Apache-2.0.txt).
