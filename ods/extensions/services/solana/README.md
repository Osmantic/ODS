# Solana (local wallet + RPC/MCP)

A tiny, **self-custodial** Solana helper for your ODS agents. It manages a local
keypair and talks to a Solana RPC, exposing the same operations two ways:

- **MCP over HTTP** at `/mcp` — Hermes (and any MCP client) gets native tools.
  Read tools (`solana_get_pubkey`, `solana_get_balance`) are always available;
  write tools (`solana_create_wallet`, `solana_airdrop`, `solana_transfer`) are
  exposed only when `SOLANA_MCP_WRITE=true` (MCP is read-only by default).
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

By default MCP exposes only read tools. To let agents create wallets, airdrop,
and transfer over MCP, set `SOLANA_MCP_WRITE=true` — but note the `/mcp`
endpoint is **unauthenticated within `ods-network`** (unlike the REST write
routes, which honor `SOLANA_API_KEY`), so enable write tools only on a trusted
network. The mainnet guard still applies. Then the agent can be asked *"Create a
Solana wallet and airdrop 1 devnet SOL"* and it calls the tools directly. n8n /
Open WebUI / OpenClaw can use the REST endpoints instead.

## Security

- **Devnet by default.** A target is treated as mainnet if `SOLANA_NETWORK` is
  `mainnet-beta`/`mainnet` **or** `SOLANA_RPC_URL` points at a mainnet endpoint —
  so a mainnet RPC can't slip past the guard while the network label is left at
  its devnet default. Transfers on mainnet are refused unless
  `SOLANA_ALLOW_MAINNET=true`; airdrop is devnet/testnet only.
- The secret key is stored at `/keystore/id.json` with mode `0600` and is
  **never returned by any endpoint/tool and never logged**.
- The host port is bound to `127.0.0.1` only. On `ods-network`, other containers
  reach it by DNS (`ods-solana`).
- REST write routes honor `SOLANA_API_KEY` (bearer). MCP is **read-only by
  default**; write tools require `SOLANA_MCP_WRITE=true` and are unauthenticated
  within `ods-network` — enable only on a trusted network.

## Config (`env_vars`)

| Var | Default | Purpose |
|---|---|---|
| `SOLANA_NETWORK` | `devnet` | `devnet` \| `testnet` \| `mainnet-beta` |
| `SOLANA_RPC_URL` | `https://api.devnet.solana.com` | JSON-RPC endpoint |
| `SOLANA_PORT` | `8590` | host port (bound to 127.0.0.1) |
| `SOLANA_ALLOW_MAINNET` | `false` | gate for mainnet writes (network label or RPC URL) |
| `SOLANA_API_KEY` | — | optional bearer token for REST writes |
| `SOLANA_MCP_WRITE` | `false` | expose MCP write tools (create_wallet/airdrop/transfer) |
