# Weaviate

Open-source vector database for semantic search, hybrid search, and generative AI. Supports structured filtering, multi-tenancy, and both gRPC and RESTful APIs.

## Requirements

- **GPU:** CPU only — no GPU required
- **Dependencies:** None

## Enable / Disable

```bash
ods enable weaviate
ods disable weaviate
```

Your data is preserved when disabling. To re-enable later: `ods enable weaviate`

## Access

- **URL:** `http://localhost:7811`
- **GraphQL:** `http://localhost:7811/v1/graphql`

## First-Time Setup

1. Enable the service: `ods enable weaviate`
2. Use the REST API or GraphQL endpoint to create schemas and import data

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `WEAVIATE_API_KEY` | API key for authentication (auto-generated) | _(required)_ |

## Local telemetry policy

ODS disables Weaviate's vendor telemetry in the container definition. The
shipped 1.36.2 runtime otherwise sends startup, periodic and shutdown reports
containing installation/runtime metadata, collection/object counts and client
usage. These reports are separate from document contents and vector queries.

The change preserves API-key authentication, REST/gRPC ports, stored vectors
and operator-selected vectorizer modules. It is not an outbound network
firewall: explicitly configured remote vectorizers or generative modules can
still send requests to their selected providers. To opt into vendor telemetry,
deliberately override DISABLE_TELEMETRY in your local Compose configuration.

Existing containers require recreation for this environment setting to take
effect. Stop writes and back up data/weaviate before updating; retain the
existing data mount. Reverting just this setting re-enables telemetry without
changing the data format. The image remains 1.36.2 and is pinned by digest.

The opt-in test ODS_TEST_WEAVIATE_TELEMETRY=1 pytest
ods/tests/test_weaviate_telemetry.py starts that actual image and an isolated
local HTTP collector. It proves the default emits reports, then checks the
disabled setting through startup, periodic and shutdown boundaries while
authenticated vector search and data survive recreation. No reports are sent
to the vendor during that test.

Sources: [1.36.2 environment handling](https://github.com/weaviate/weaviate/blob/v1.36.2/usecases/config/environment.go),
[telemetry lifecycle](https://github.com/weaviate/weaviate/blob/v1.36.2/usecases/telemetry/telemetry.go),
[reported fields](https://github.com/weaviate/weaviate/blob/v1.36.2/usecases/telemetry/payload.go).
