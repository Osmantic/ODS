# WireMock — explicit project API fixtures

Install and enable in ODS Extensions. The local API is available at `http://localhost:11035`; this is an API service, not a web application with a dashboard. ODS therefore does not expose a misleading UI launch button.

Use it when a project needs deterministic HTTP behavior: expected payloads, request matching, latency or failure scenarios. These are explicit test doubles. The extension never replaces Portal/model replies, intercepts name questions or claims a fixture response came from a real external service.

## Opt-in example

The recipe includes `example-mapping.json`, a labeled synthetic response at `/ods-example/status`. It is not loaded during installation. From the recipe directory, register it deliberately:

```sh
curl -X POST http://localhost:11035/__admin/mappings -H "Content-Type: application/json" --data-binary @example-mapping.json
curl http://localhost:11035/ods-example/status
```

On Windows PowerShell, use `curl.exe` for these commands, since older PowerShell versions alias `curl` to a different command. The returned JSON identifies itself as a fixture. The mapping has a fixed ID and requests persistence. If already registered, update that ID with the administrative API instead of repeatedly creating duplicates.

For project integration, point only the test environment at this base URL. A container on `ods-network` uses `http://wiremock:8080`, while a host process uses the published localhost port. Keep production URLs unchanged. Separate projects' mappings by explicit paths or reset only the mapping IDs owned by the test; do not indiscriminately delete another project's fixtures.

## Storage and access

- `wiremock-data` holds mappings and response body files under `/home/wiremock`. Fresh named volumes inherit UID/GID 1000 ownership from the image layer, avoiding host filesystem permission differences.
- API-created mappings are not automatically durable unless saved. The example sets `persistent: true`; other workflows can use WireMock's save-mappings endpoint deliberately. In-memory request history/scenario progress is not a durable results database.
- The request journal is bounded to 500 entries. Requests may contain credentials or personal data from tests; avoid real production secrets, inspect only necessary request data and clear it when appropriate. JVM memory is limited within a 1 GB container allocation.
- The administrative API is unauthenticated upstream. Only loopback is published, but trusted services on `ods-network` can reach it. Remote access requires a deliberately authenticated proxy. No host socket, host drive or privileged mode is granted.
- Proxying/recording are not enabled. They would send requests outside the test service and require explicit target configuration. No Java extension JARs are downloaded at runtime by this recipe.

## Platform and lifecycle

Official Apache-2.0 standalone 3.13.2-2 image, pinned by digest for amd64/arm64/ARMv7. Windows/macOS use Linux containers through Docker Desktop; Linux uses Docker Engine. No model or GPU is needed. Runtime UID 1000 with `no-new-privileges` uses the existing entrypoint without its optional UID-switching path.

Changing `WIREMOCK_PORT` changes the host port only. Disabling preserves saved mappings/files. Back up the volume after stopping writers; recreating a container is not a backup of request history. The health probe calls the actual `/__admin/health` endpoint with a timeout; successful health is not proof that a project's contract tests passed.

Registry architecture metadata, official image/entrypoint and upstream API behavior were inspected. ODS schema/Compose/staging checks do not prove image build or HTTP scenarios. Build, explicit mapping registration, matching, persistence and test-suite behavior remain runtime checks; no example was activated during preparation.

- [Core project and license](https://github.com/wiremock/wiremock/tree/3.13.2)
- [Versioned official image](https://github.com/wiremock/wiremock-docker/tree/3.13.2-2)
- [Stubbing documentation](https://wiremock.org/docs/stubbing/)
