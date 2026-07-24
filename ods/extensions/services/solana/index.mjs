// ODS `solana` extension entrypoint.
//
// One process, two interfaces over the same shared core (solana.mjs):
//   - MCP over HTTP at /mcp  -> Hermes/agents get native Solana tools
//   - REST                  -> n8n / open-webui / openclaw / curl
//
// Listens on 0.0.0.0 inside the container; the compose file maps the host side
// to 127.0.0.1 only. Other containers reach it by DNS name on ods-network.

import http from 'node:http';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { buildMcpServer } from './mcp.mjs';
import * as solana from './solana.mjs';

const PORT = Number(process.env.SOLANA_PORT_INTERNAL || 8590);
const API_KEY = process.env.SOLANA_API_KEY || '';

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(payload);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let raw = '';
    req.on('data', (chunk) => {
      raw += chunk;
      if (raw.length > 1_000_000) reject(new solana.HttpError(413, 'request body too large'));
    });
    req.on('end', () => {
      if (!raw) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch {
        reject(new solana.HttpError(400, 'invalid JSON body'));
      }
    });
    req.on('error', reject);
  });
}

// API key guards the REST write routes only (opt-in via SOLANA_API_KEY).
function authorized(req) {
  if (!API_KEY) return true;
  const header = req.headers['authorization'] || '';
  const bearer = header.startsWith('Bearer ') ? header.slice(7) : '';
  return bearer === API_KEY || req.headers['x-api-key'] === API_KEY;
}

// Stateless MCP: a fresh server + transport per request.
async function handleMcp(req, res, body) {
  const server = buildMcpServer();
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
  res.on('close', () => {
    transport.close();
    server.close();
  });
  await server.connect(transport);
  await transport.handleRequest(req, res, body);
}

async function handleRest(req, res, url) {
  const { pathname, searchParams } = url;
  const method = req.method || 'GET';
  const isWrite = method === 'POST';

  if (method === 'GET' && pathname === '/health') {
    return sendJson(res, 200, { status: 'ok', ...solana.config() });
  }
  if (method === 'GET' && pathname === '/wallet/pubkey') {
    return sendJson(res, 200, solana.getPubkey());
  }
  if (method === 'GET' && pathname === '/balance') {
    return sendJson(res, 200, await solana.getBalance(searchParams.get('pubkey') || undefined));
  }

  if (isWrite && !authorized(req)) {
    return sendJson(res, 401, { error: 'unauthorized: set Authorization: Bearer <SOLANA_API_KEY>' });
  }

  if (method === 'POST' && pathname === '/wallet') {
    const body = await readJson(req);
    return sendJson(res, 200, solana.createWallet(body.secretKey));
  }
  if (method === 'POST' && pathname === '/airdrop') {
    const body = await readJson(req);
    return sendJson(res, 200, await solana.airdrop(body.pubkey, body.sol));
  }
  if (method === 'POST' && pathname === '/transfer') {
    const body = await readJson(req);
    const result = body.mint
      ? await solana.transferSpl(body.mint, body.to, body.amount)
      : await solana.transferSol(body.to, body.sol);
    return sendJson(res, 200, result);
  }

  return sendJson(res, 404, { error: `not found: ${method} ${pathname}` });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  try {
    if (url.pathname === '/mcp') {
      const body = req.method === 'POST' ? await readJson(req) : undefined;
      return await handleMcp(req, res, body);
    }
    return await handleRest(req, res, url);
  } catch (err) {
    const status = err instanceof solana.HttpError ? err.status : 500;
    if (!res.headersSent) sendJson(res, status, { error: err.message });
  }
});

server.listen(PORT, '0.0.0.0', () => {
  const { network, rpcUrl } = solana.config();
  console.log(`ods-solana listening on :${PORT} (network=${network}, rpc=${rpcUrl})`);
});
