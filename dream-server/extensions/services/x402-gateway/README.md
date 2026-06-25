# x402 Gateway

Optional HTTP 402 payment gateway for Dream Server.

The gateway protects only explicit HTTP routes listed in `config/x402/config.json`.
It does not change `llama-server`, `dashboard-api`, or any other core service.

## Status

This extension is disabled by default. Enable it only after creating a config
file with your receiving wallet address.

## Configure

Copy the example config:

```bash
mkdir -p config/x402
cp config/x402/config.example.json config/x402/config.json
```

Edit:

- `seller.recipient` — your receiving wallet address
- `seller.network` — `eip155:84532` for Base Sepolia testing, `eip155:8453` for Base mainnet
- `rules` — exact gateway routes to protect

For no-signup testnet usage, keep:

```json
"facilitator": {
  "url": "https://x402.org/facilitator",
  "provider": "x402.org",
  "auth": { "type": "none" }
}
```

For Base mainnet with the CDP facilitator, set:

```json
"facilitator": {
  "url": "https://api.cdp.coinbase.com/platform/v2/x402",
  "provider": "cdp",
  "auth": {
    "type": "cdp_api_key",
    "apiKeyIdEnv": "CDP_API_KEY_ID",
    "apiKeySecretEnv": "CDP_API_KEY_SECRET"
  }
}
```

and add the CDP credentials to `.env`:

```bash
CDP_API_KEY_ID=<your-cdp-key-id>
CDP_API_KEY_SECRET=<your-base64-cdp-ed25519-secret>
```

The default example protects:

```text
POST /llama/v1/chat/completions
```

and forwards successful paid requests to:

```text
http://llama-server:8080/v1/chat/completions
```

## Enable

```bash
dream enable x402-gateway
dream start x402-gateway
```

## Test

Free vendor/control checks:

```bash
curl http://127.0.0.1:4020/v1/health
curl http://127.0.0.1:4020/v1/health/ready
curl http://127.0.0.1:4020/v1/vendor
curl http://127.0.0.1:4020/v1/limits
curl http://127.0.0.1:4020/v1/capabilities
```

An unpaid protected capability request should return `402 Payment Required`:

```bash
curl -i http://127.0.0.1:4020/v1/capabilities/local_chat \
  -H 'content-type: application/json' \
  -d '{"model":"local","messages":[{"role":"user","content":"hello"}],"stream":true}'
```

## Vendor contract

The gateway exposes the Dream Server vendor contract as free control-plane
endpoints and paid execution endpoints.

Required free vendor endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/health` | Fast liveness check for the API process. |
| `GET /v1/health/ready` | Readiness check for capability registry, payment rules, and usage metering. |
| `GET /v1/vendor` | Provider identity and protocol metadata. |
| `GET /v1/limits` | Node-level request size, streaming, timeout, and rate-limit metadata. |
| `GET /v1/capabilities` | Advertised paid capabilities, pricing, risk level, schemas, and examples. |

Default paid capability endpoints:

| Endpoint | Capability |
| --- | --- |
| `POST /v1/capabilities/local_chat` | General local LLM chat completion. |
| `POST /v1/capabilities/coding_help` | Code explanation, generation, and debugging help. |
| `POST /v1/capabilities/coding_review` | Review pasted code or diffs and return findings. |

Health, readiness, vendor, limits, and capabilities endpoints are not charged.
Payment is enforced before protected capability routes are proxied upstream.
Streaming is advertised per capability and forwarded to the configured upstream
when the backend supports streaming responses.

## Design

V1 is route-only and allowlist-only. There is no wildcard charging mode, no
database, and no MCP/tool interception yet. The free vendor contract lets buyers
check who the node is, whether it is ready, what it sells, and what limits apply
before paying for explicit capability routes.
