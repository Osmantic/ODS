# Weaviate

Open-source vector database for semantic search, hybrid search, and generative AI. Supports structured filtering, multi-tenancy, and both gRPC and RESTful APIs.

## Requirements

- **GPU:** CPU only — no GPU required
- **Dependencies:** None

## Enable / Disable

```bash
dream enable weaviate
dream disable weaviate
```

Your data is preserved when disabling. To re-enable later: `dream enable weaviate`

## Access

- **URL:** `http://localhost:7811`
- **GraphQL:** `http://localhost:7811/v1/graphql`

## First-Time Setup

1. Enable the service: `dream enable weaviate`
2. Use the REST API or GraphQL endpoint to create schemas and import data

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `WEAVIATE_API_KEY` | API key for authentication (auto-generated) | _(required)_ |
