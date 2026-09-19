# Kroki

Render diagram source from documentation, scripts or agents without submitting
it to a public rendering service. This optional API recipe pins upstream Kroki
**0.32.1** by digest. It is a renderer, not an interactive diagram editor.

## Use the local API

Install **Kroki** from Extensions. From the same host:

```bash
curl --fail --show-error http://localhost:7829/graphviz/svg \
  -H 'Content-Type: text/plain' \
  --data-binary 'digraph { document -> index -> answer }' --output diagram.svg
```

Containers attached to `ods-network` use `http://kroki:8000`. Configure writing
tools explicitly to use your local endpoint. `KROKI_API_PORT` changes only the
published host port; `KROKI_LISTEN` keeps the server listening on container 8000.
The health endpoint is `/health`.

The main image includes Graphviz, PlantUML, D2 and other native renderers. This
recipe does **not** start the optional Mermaid, BPMN, Excalidraw or diagrams.net
companion services. Requests for those renderers cannot work until an operator
separately installs/configures the corresponding companions. Do not substitute
the public kroki.io endpoint if a local renderer is unavailable.

## Runtime boundaries

Publishing defaults to loopback; the upstream API has no account/authentication
layer. Remote use needs an operator-managed protected network or HTTPS proxy.
The service has no host data mounts, runs as UID/GID 1001, and has a read-only root
with temporary scratch space. It retains no diagram library or user account data.

Kroki secure mode and the PlantUML sandbox disable filesystem/network includes.
The request body limit is 1 MiB; native commands time out after 5 seconds and Java
conversions after 20 seconds. The container is capped at 2 CPU and 1.5 GB RAM.
These settings are not a complete sandbox guarantee for every bundled renderer;
keep clients trusted and review an image update before widening access.

Save source diagrams and rendered output in your own project. Replacing or rolling
back this stateless container needs no data migration. Diagrams may render
differently after a renderer update, so keep the image digest with important builds.

## Verification

`ODS_TEST_KROKI_DOCKER=1 python -m pytest -q ods/tests/test_kroki_extension.py`
exercises the pinned image's health, real Graphviz/PlantUML output, malformed-input
and oversized-body rejection, external-include denial and replacement. Linux
x86-64 is the live test target; ARM, native Windows/macOS, other diagram libraries
and real document-editor integrations need separate verification.

Upstream: [0.32.1 server configuration](https://github.com/yuzutech/kroki/blob/v0.32.1/server/src/main/java/io/kroki/server/Server.java),
[secure mode](https://docs.kroki.io/kroki/setup/configuration/), and
[included versus companion renderers](https://docs.kroki.io/kroki/setup/install/).
