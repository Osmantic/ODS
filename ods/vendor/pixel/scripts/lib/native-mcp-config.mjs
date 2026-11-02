// OpenClaw owns this registry, its transport settings and its OAuth store.
// Rendering Pixel must retain those settings and the owner's MCP tool policy;
// it must not enroll a server, probe it or grant MCP merely because it exists.
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const own = (value, key) => Object.hasOwn(value, key);
const clone = value => structuredClone(value);

function strings(value) {
  if (!Array.isArray(value) || value.some(item => typeof item !== 'string')) {
    throw new Error('Existing native MCP tool policy must contain string lists');
  }
  return value;
}

// Qualified MCP tool names are opaque native IDs (<server>__<tool>). Preserve
// explicit plugin grants, qualified names and owner-selected patterns; do not
// recreate OpenClaw's server/tool name sanitizer or resolve a live tool list.
function mcpSelectors(value) {
  return strings(value).filter(item => item === 'bundle-mcp' || item === 'group:plugins'
    || item === 'plugin:bundle-mcp' || item.includes('__') || item.includes('*'));
}

function mergeSelectors(before, after, key) {
  if (!own(before, key)) return;
  const selected = mcpSelectors(before[key]);
  if (!selected.length) return;
  after[key] = [...new Set([...(own(after, key) ? strings(after[key]) : []), ...selected])];
}

function restrictions(before, after, {generatedDeny = false} = {}) {
  // An allow list is restrictive, including an empty list. Dropping it because
  // it contains no MCP names could silently give MCP access via a wider policy.
  if (own(before, 'allow')) after.allow = clone(strings(before.allow));
  if (own(before, 'deny')) {
    if (generatedDeny) mergeSelectors(before, after, 'deny');
    else after.deny = clone(strings(before.deny));
  }
  for (const key of ['profile', 'byProvider', 'toolsBySender']) {
    if (own(before, key)) after[key] = clone(before[key]);
  }
  mergeSelectors(before, after, 'alsoAllow');
}

export function preserveNativeMcp(current, candidate, agentId) {
  if (!own(current, 'mcp')) return;
  if (!object(current.mcp) || (own(current.mcp, 'servers') && !object(current.mcp.servers))) {
    throw new Error('Existing native MCP registry must be an object');
  }
  candidate.mcp = clone(current.mcp);
  const before = current.tools ?? {};
  if (!object(before)) throw new Error('Existing native MCP tool policy must be an object');
  restrictions(before, candidate.tools);
  const sandbox = before.sandbox?.tools;
  if (sandbox !== undefined) {
    if (!object(sandbox)) throw new Error('Existing native MCP sandbox policy must be an object');
    const target = candidate.tools.sandbox.tools;
    // Keep Pixel's freshly generated core/source grants, adding only the
    // owner's existing native grants. A registry alone grants nothing.
    for (const key of ['allow', 'alsoAllow']) mergeSelectors(sandbox, target, key);
    if (own(sandbox, 'deny')) target.deny = clone(strings(sandbox.deny));
  }
  const oldAgent = current.agents?.list?.find(item => item.id === agentId);
  const newAgent = candidate.agents.list.find(item => item.id === agentId);
  if (!newAgent) throw new Error('Native MCP preservation requires the selected Pixel agent');
  if (oldAgent?.tools !== undefined) {
    if (!object(oldAgent.tools)) throw new Error('Existing native MCP agent policy must be an object');
    // Pixel's new managed-source denials win. The renderer already carries
    // other old denials while removing ones for newly enabled managed tools.
    restrictions(oldAgent.tools, newAgent.tools, {generatedDeny: true});
    if (own(oldAgent.tools, 'sandbox')) newAgent.tools.sandbox = clone(oldAgent.tools.sandbox);
  }
}
