// MCP server exposing the Solana core as agent tools (Hermes connects over HTTP).
//
// The /mcp endpoint is unauthenticated within ods-network (the REST API key
// does not apply to it), so write tools (create_wallet, airdrop, transfer) are
// registered ONLY when SOLANA_MCP_WRITE=true. Default is read-only: agents can
// read the wallet/balance but cannot move funds or rotate the key over MCP
// unless the operator explicitly opts in.
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import * as solana from './solana.mjs';

const MCP_WRITE = process.env.SOLANA_MCP_WRITE === 'true';

const ok = (value) => ({ content: [{ type: 'text', text: JSON.stringify(value) }] });

// Run a core call and shape success/error into an MCP tool result.
async function run(fn) {
  try {
    return ok(await fn());
  } catch (err) {
    return { content: [{ type: 'text', text: `error: ${err.message}` }], isError: true };
  }
}

export function buildMcpServer() {
  const server = new McpServer({ name: 'ods-solana', version: '0.1.0' });

  // ── Read tools (always available) ────────────────────────────────────────
  server.registerTool(
    'solana_get_pubkey',
    {
      description: 'Return the public key of the managed wallet (creating it on first use).',
      inputSchema: {},
    },
    async () => run(() => solana.getPubkey()),
  );

  server.registerTool(
    'solana_get_balance',
    {
      description: 'Get the SOL balance of a public key (defaults to the managed wallet).',
      inputSchema: { pubkey: z.string().optional() },
    },
    async ({ pubkey }) => run(() => solana.getBalance(pubkey)),
  );

  // ── Write tools (opt-in via SOLANA_MCP_WRITE) ────────────────────────────
  if (MCP_WRITE) {
    server.registerTool(
      'solana_create_wallet',
      {
        description:
          'Create/rotate the managed self-custodial wallet (or import from a 64-byte secret array). Returns the public key only.',
        inputSchema: { importSecret: z.array(z.number()).length(64).optional() },
      },
      async ({ importSecret }) => run(() => solana.createWallet(importSecret)),
    );

    server.registerTool(
      'solana_airdrop',
      {
        description: 'Request a devnet/testnet airdrop of SOL to a public key (defaults to the managed wallet). Devnet only.',
        inputSchema: { pubkey: z.string().optional(), sol: z.number().positive() },
      },
      async ({ pubkey, sol }) => run(() => solana.airdrop(pubkey, sol)),
    );

    server.registerTool(
      'solana_transfer',
      {
        description:
          'Transfer from the managed wallet. Native SOL when `sol` is given; SPL token when `mint` and `amount` (base units) are given.',
        inputSchema: {
          to: z.string(),
          sol: z.number().positive().optional(),
          mint: z.string().optional(),
          amount: z.number().int().positive().optional(),
        },
      },
      async ({ to, sol, mint, amount }) =>
        run(() => {
          if (mint) return solana.transferSpl(mint, to, amount);
          if (sol != null) return solana.transferSol(to, sol);
          throw new solana.HttpError(400, 'provide `sol` (native) or `mint`+`amount` (SPL)');
        }),
    );
  }

  return server;
}
