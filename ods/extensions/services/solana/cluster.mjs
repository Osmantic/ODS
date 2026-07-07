// Pure cluster-classification helpers — no Solana/network deps, so the mainnet
// write guard is deterministic and unit-testable, and shared by the runtime.

const MAINNET_RPC_HOSTS = new Set(['api.mainnet-beta.solana.com']);

// Best-effort: does this RPC URL point at Solana mainnet? Errs toward "yes"
// (i.e. refuse writes) for anything whose host looks like mainnet.
export function rpcIsMainnet(rpcUrl) {
  let host;
  try {
    host = new URL(rpcUrl).hostname.toLowerCase();
  } catch {
    return false;
  }
  return MAINNET_RPC_HOSTS.has(host) || host.includes('mainnet');
}

// Treat a target as mainnet if the network label says so OR the RPC URL
// resolves to a mainnet endpoint. This closes the bypass where
// SOLANA_NETWORK=devnet (default) is paired with a mainnet SOLANA_RPC_URL.
export function isMainnetTarget(network, rpcUrl) {
  return network === 'mainnet-beta' || network === 'mainnet' || rpcIsMainnet(rpcUrl);
}

// Writes are allowed on non-mainnet targets, and on mainnet only when the
// operator explicitly opts in.
export function writesAllowed(network, rpcUrl, allowMainnet) {
  return !isMainnetTarget(network, rpcUrl) || allowMainnet === true;
}
