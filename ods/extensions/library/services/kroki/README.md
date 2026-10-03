# Kroki for ODS

Render diagram source through a local HTTP API. The official MIT server image
contains engines such as Graphviz, PlantUML, D2, Ditaa and others; the bundled
engines retain their respective licenses. It is an API service, not a graphical
editor or a container that automatically modifies Portal responses.

## Use

Install and enable `kroki`. The host API is `http://localhost:11072`; another
explicitly configured ODS service can use `http://kroki:8000` on `ods-network`.
`KROKI_PORT` changes the loopback host port. Submit your own diagram text to a
format endpoint, for example:

```text
curl --fail --request POST --header "Content-Type: text/plain" --data-binary @diagram.dot http://localhost:11072/graphviz/svg --output diagram.svg
```

On PowerShell use `curl.exe` to call the HTTP client rather than the legacy
PowerShell alias. `diagram.dot` must already contain your Graphviz source. This
command is an explicit usage example and was not executed during installation.
The response file is written by the client; Kroki does not save it in Portal's
workspace or maintain a document library.

Mermaid, Excalidraw and diagrams.net formats require separately configured
companion services and are not included by this recipe. The existing Mermaid
Live Editor is not automatically connected or rebuilt to use this API. Do not
interpret those endpoint names appearing in upstream docs as enabled services.

## Resources and boundaries

Native nonroot Kroki user and Java startup are retained. Heap is bounded to 1 GiB
inside a 2 GiB container; diagram subprocesses share the remaining allowance.
Rendering command timeout is 10 seconds. Secure mode is enabled and PlantUML
external includes are disabled with its SANDBOX profile. Diagrams requiring
external includes must be adapted; this setting is not a promise that arbitrary
renderers can process every input safely or within the memory limit.

The unauthenticated API is bound to host loopback. No public routing, shared
credentials, notifications or host mounts are configured. Request and temporary
render data are not a persistent document store, so no fictitious data volume is
added. Export and retain source/output in the calling application.

The pinned official 0.32.1 image supports amd64 and arm64. Use Docker Engine on
Linux or Docker Desktop Linux containers on Windows/macOS. No GPU or selected
language model is required. The image contains multiple native rendering tools
and fonts and is larger than a simple HTTP service.

Native `/health` checks server readiness, not every diagram engine. Image startup,
rendering formats, error handling, font coverage and platform execution remain
runtime-pending. No application containers were started during integration.

Source: https://github.com/yuzutech/kroki/tree/v0.32.1
