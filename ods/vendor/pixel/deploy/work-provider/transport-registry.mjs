import { executeMoonshotChatTurn } from "./moonshot-transport.mjs";
import { executeLocalChatTurn } from "./local-transport.mjs";
import { executeRemoteProviderTurn } from "./generic-remote-transport.mjs";

// Closed registry of provider transports. Production execution may only resolve a
// transport through this registry by exact provider id; there is no path to inject an
// arbitrary callback into a routed production run. Test override is confined to the
// executor's explicit testSeam.
const TRANSPORT_REGISTRY = Object.freeze({
  "moonshot-kimi": Object.freeze({ execute: executeMoonshotChatTurn }),
  local: Object.freeze({ execute: executeLocalChatTurn }),
  openai: Object.freeze({ execute: executeRemoteProviderTurn }),
  anthropic: Object.freeze({ execute: executeRemoteProviderTurn }),
  openrouter: Object.freeze({ execute: executeRemoteProviderTurn }),
  together: Object.freeze({ execute: executeRemoteProviderTurn }),
  fireworks: Object.freeze({ execute: executeRemoteProviderTurn }),
  groq: Object.freeze({ execute: executeRemoteProviderTurn }),
});

export class WorkProviderTransportRegistryError extends Error {}
function fail(message) { throw new WorkProviderTransportRegistryError(message); }

export function listWorkProviderTransports() {
  return Object.keys(TRANSPORT_REGISTRY);
}

export function resolveWorkProviderTransport(providerId) {
  const entry = TRANSPORT_REGISTRY[providerId];
  if (!entry) fail(`provider id ${providerId} has no closed transport in the registry`);
  return entry.execute;
}

export function assertClosedTransport(transport) {
  const known = Object.values(TRANSPORT_REGISTRY).map((entry) => entry.execute);
  if (!known.includes(transport)) fail("transport is outside the closed provider transport registry");
  return transport;
}
