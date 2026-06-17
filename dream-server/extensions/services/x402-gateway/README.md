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

Health:

```bash
curl http://127.0.0.1:4020/health
```

An unpaid protected request should return `402 Payment Required`:

```bash
curl -i http://127.0.0.1:4020/llama/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"local","messages":[{"role":"user","content":"hello"}]}'
```

## Design

V1 is route-only and allowlist-only. There is no wildcard charging mode, no
database, and no MCP/tool interception yet. That keeps paid access explicit and
leaves Dream Server's local/private behavior unchanged.
