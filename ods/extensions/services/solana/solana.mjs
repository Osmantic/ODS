// Shared Solana core for the ODS `solana` extension.
//
// Self-custodial: a single keypair is generated on demand and persisted to the
// keystore volume with mode 0600. The secret key never leaves this process
// (never returned by any endpoint/tool, never logged).
//
// Safety model: devnet by default. Any *write* (transfer) on mainnet is refused
// unless SOLANA_ALLOW_MAINNET=true. Airdrop is devnet/testnet only.

import fs from 'node:fs';
import path from 'node:path';
import {
  Connection,
  Keypair,
  PublicKey,
  LAMPORTS_PER_SOL,
  SystemProgram,
  Transaction,
  sendAndConfirmTransaction,
} from '@solana/web3.js';
import {
  getOrCreateAssociatedTokenAccount,
  transfer as splTransfer,
} from '@solana/spl-token';
import { isMainnetTarget, writesAllowed } from './cluster.mjs';

const NETWORK = process.env.SOLANA_NETWORK || 'devnet';
const RPC_URL = process.env.SOLANA_RPC_URL || 'https://api.devnet.solana.com';
const ALLOW_MAINNET = process.env.SOLANA_ALLOW_MAINNET === 'true';
const KEYSTORE_DIR = process.env.SOLANA_KEYSTORE_DIR || '/keystore';
const KEY_PATH = path.join(KEYSTORE_DIR, 'id.json');
const MAX_AIRDROP_SOL = 5;

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// Mainnet is inferred from the network label AND the RPC URL, so a mainnet
// SOLANA_RPC_URL cannot slip past the guard while SOLANA_NETWORK is left at
// its devnet default.
const isMainnet = () => isMainnetTarget(NETWORK, RPC_URL);

function assertWriteAllowed() {
  if (!writesAllowed(NETWORK, RPC_URL, ALLOW_MAINNET)) {
    throw new HttpError(403, 'writes on mainnet require SOLANA_ALLOW_MAINNET=true');
  }
}

function toPublicKey(value, field = 'pubkey') {
  try {
    return new PublicKey(value);
  } catch {
    throw new HttpError(400, `invalid ${field}: ${value}`);
  }
}

export function config() {
  return { network: NETWORK, rpcUrl: RPC_URL, allowMainnet: ALLOW_MAINNET };
}

let connection;
function getConnection() {
  if (!connection) connection = new Connection(RPC_URL, 'confirmed');
  return connection;
}

function persistKeypair(keypair) {
  fs.mkdirSync(KEYSTORE_DIR, { recursive: true });
  fs.writeFileSync(KEY_PATH, JSON.stringify(Array.from(keypair.secretKey)), { mode: 0o600 });
  fs.chmodSync(KEY_PATH, 0o600); // enforce perms even if the file pre-existed
  return keypair;
}

// The managed wallet: loaded from the keystore, created + persisted on first use.
function loadOrCreateKeypair() {
  if (fs.existsSync(KEY_PATH)) {
    const secret = JSON.parse(fs.readFileSync(KEY_PATH, 'utf8'));
    return Keypair.fromSecretKey(Uint8Array.from(secret));
  }
  return persistKeypair(Keypair.generate());
}

// Create (or import) the managed wallet. Returns the public key only.
export function createWallet(importSecret) {
  let keypair;
  if (importSecret) {
    try {
      keypair = Keypair.fromSecretKey(Uint8Array.from(importSecret));
    } catch {
      throw new HttpError(400, 'secretKey must be an array of 64 bytes');
    }
  } else {
    keypair = Keypair.generate();
  }
  persistKeypair(keypair);
  return { pubkey: keypair.publicKey.toBase58() };
}

export function getPubkey() {
  return { pubkey: loadOrCreateKeypair().publicKey.toBase58() };
}

export async function getBalance(pubkey) {
  const key = pubkey ? toPublicKey(pubkey) : loadOrCreateKeypair().publicKey;
  const lamports = await getConnection().getBalance(key);
  return { pubkey: key.toBase58(), lamports, sol: lamports / LAMPORTS_PER_SOL };
}

export async function airdrop(pubkey, sol) {
  if (isMainnet()) throw new HttpError(400, 'airdrop is only available on devnet/testnet');
  const amount = Number(sol);
  if (!Number.isFinite(amount) || amount <= 0 || amount > MAX_AIRDROP_SOL) {
    throw new HttpError(400, `sol must be a number between 0 and ${MAX_AIRDROP_SOL}`);
  }
  const key = pubkey ? toPublicKey(pubkey) : loadOrCreateKeypair().publicKey;
  const signature = await getConnection().requestAirdrop(key, amount * LAMPORTS_PER_SOL);
  await getConnection().confirmTransaction(signature, 'confirmed');
  return { signature, pubkey: key.toBase58(), sol: amount };
}

export async function transferSol(to, sol) {
  assertWriteAllowed();
  const amount = Number(sol);
  if (!Number.isFinite(amount) || amount <= 0) throw new HttpError(400, 'sol must be a positive number');
  const recipient = toPublicKey(to, 'to');
  const from = loadOrCreateKeypair();
  const tx = new Transaction().add(
    SystemProgram.transfer({
      fromPubkey: from.publicKey,
      toPubkey: recipient,
      lamports: Math.round(amount * LAMPORTS_PER_SOL),
    }),
  );
  const signature = await sendAndConfirmTransaction(getConnection(), tx, [from]);
  return { signature, from: from.publicKey.toBase58(), to: recipient.toBase58(), sol: amount };
}

// SPL transfer. `amount` is in the token's base units (not decimal-adjusted).
export async function transferSpl(mint, to, amount) {
  assertWriteAllowed();
  const raw = Number(amount);
  if (!Number.isInteger(raw) || raw <= 0) throw new HttpError(400, 'amount must be a positive integer (base units)');
  const mintKey = toPublicKey(mint, 'mint');
  const recipient = toPublicKey(to, 'to');
  const payer = loadOrCreateKeypair();
  const source = await getOrCreateAssociatedTokenAccount(getConnection(), payer, mintKey, payer.publicKey);
  const dest = await getOrCreateAssociatedTokenAccount(getConnection(), payer, mintKey, recipient);
  const signature = await splTransfer(
    getConnection(),
    payer,
    source.address,
    dest.address,
    payer.publicKey,
    raw,
  );
  return { signature, mint: mintKey.toBase58(), to: recipient.toBase58(), amount: raw };
}
