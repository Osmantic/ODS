// Regression tests for the mainnet write guard. Dependency-free — run with
// `node test.mjs` (no npm install needed; cluster.mjs has no imports).
import assert from 'node:assert/strict';
import { rpcIsMainnet, isMainnetTarget, writesAllowed } from './cluster.mjs';

// RPC host classification.
assert.equal(rpcIsMainnet('https://api.devnet.solana.com'), false);
assert.equal(rpcIsMainnet('https://api.testnet.solana.com'), false);
assert.equal(rpcIsMainnet('https://api.mainnet-beta.solana.com'), true);
assert.equal(rpcIsMainnet('https://solana-mainnet.g.alchemy.com/v2/KEY'), true);
assert.equal(rpcIsMainnet('https://mainnet.helius-rpc.com/?api-key=x'), true);
assert.equal(rpcIsMainnet('not-a-url'), false);

// The reviewer's bypass: mainnet RPC + default devnet label must be treated as
// mainnet, so writes are refused unless explicitly opted in.
assert.equal(isMainnetTarget('devnet', 'https://api.mainnet-beta.solana.com'), true);
assert.equal(writesAllowed('devnet', 'https://api.mainnet-beta.solana.com', false), false);
assert.equal(writesAllowed('devnet', 'https://api.mainnet-beta.solana.com', true), true);

// Ordinary devnet stays writable.
assert.equal(isMainnetTarget('devnet', 'https://api.devnet.solana.com'), false);
assert.equal(writesAllowed('devnet', 'https://api.devnet.solana.com', false), true);

// Explicit mainnet label is refused unless opted in.
assert.equal(writesAllowed('mainnet-beta', 'https://api.mainnet-beta.solana.com', false), false);
assert.equal(writesAllowed('mainnet-beta', 'https://api.mainnet-beta.solana.com', true), true);

console.log('ok - solana mainnet-guard regression tests passed');
