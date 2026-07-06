# Solana (local wallet + RPC/MCP)

A tiny, **self-custodial** Solana helper for your ODS agents. It manages a local
keypair and talks to a Solana RPC, exposing the same operations two ways:

- **MCP over HTTP** at `/mcp` — Hermes (and any MCP client) gets native tools:
  `solana_create_wallet`, `solana_get_pubkey`, `solana_get_balance`,
  `solana_airdrop`, `solana_transfer`.
- **REST** — for n8n, Open WebUI, OpenClaw, or plain `curl`.

No hosted API, no custodial service. Keys never leave the box. **Devnet by
default.**

## Enable

```bash
ods enable solana
ods start solana
curl http://127.0.0.1:8590/health
# {"status":"ok","network":"devnet","rpcUrl":"https://api.devnet.solana.com","allowMainnet":false}
```

## REST

| Method | Path | Body / query | Notes |
|---|---|---|---|
| GET | `/health` | — | liveness + current network |
| POST | `/wallet` | `{ "secretKey": [64 bytes] }` (optional) | create/import managed wallet → `{pubkey}` |
| GET | `/wallet/pubkey` | — | managed wallet pubkey |
| GET | `/balance` | `?pubkey=` (optional) | `{lamports, sol}` |
| POST | `/airdrop` | `{ "pubkey?": "...", "sol": 1 }` | devnet/testnet only |
| POST | `/transfer` | `{ "to": "...", "sol": 0.1 }` or `{ "to", "mint", "amount" }` | SOL or SPL (base units) |

```bash
# Full devnet loop
curl -s -XPOST 127.0.0.1:8590/wallet
curl -s -XPOST 127.0.0.1:8590/airdrop -d '{"sol":1}'
curl -s 127.0.0.1:8590/balance
curl -s -XPOST 127.0.0.1:8590/transfer -d '{"to":"<PUBKEY>","sol":0.1}'
```

## Use from agents (Hermes, via MCP)

The extension is an MCP server. Point Hermes at it in `config.yaml`:

```yaml
mcp_servers:
  solana:
    url: http://ods-solana:8590/mcp
    transport: http
```

Then the agent can just be asked: *"Create a Solana wallet and airdrop 1 devnet
SOL"* — it calls the `solana_*` tools directly. n8n / Open WebUI / OpenClaw can
call the REST endpoints instead.

## Security

- **Devnet by default** (`SOLANA_NETWORK=devnet`). Transfers on `mainnet-beta`
  are refused unless `SOLANA_ALLOW_MAINNET=true`. Airdrop is devnet/testnet only.
- The secret key is stored at `/keystore/id.json` with mode `0600` and is
  **never returned by any endpoint/tool and never logged**.
- The host port is bound to `127.0.0.1` only. On `ods-network`, other containers
  reach it by DNS (`ods-solana`).
- `SOLANA_API_KEY` (optional) requires `Authorization: Bearer <key>` on the REST
  write routes. For mainnet use, set it and keep the service on a trusted network.

## Config (`env_vars`)

| Var | Default | Purpose |
|---|---|---|
| `SOLANA_NETWORK` | `devnet` | `devnet` \| `testnet` \| `mainnet-beta` |
| `SOLANA_RPC_URL` | `https://api.devnet.solana.com` | JSON-RPC endpoint |
| `SOLANA_PORT` | `8590` | host port (bound to 127.0.0.1) |
| `SOLANA_ALLOW_MAINNET` | `false` | gate for mainnet writes |
| `SOLANA_API_KEY` | — | optional bearer token for REST writes |
