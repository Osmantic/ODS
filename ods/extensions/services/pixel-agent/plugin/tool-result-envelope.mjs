// Tool discovery owns descriptions and schemas. Execution receipts need the
// tool identity and complete result, not another copy of discovery metadata.
// Preserve errors, evidence, details, call IDs and all result content verbatim.
export function compactToolResultEnvelope(message) {
  if (message?.toolName !== 'tool_call' || !Array.isArray(message.content)) return message;
  let changed = false;
  const content = message.content.map(block => {
    if (block?.type !== 'text' || typeof block.text !== 'string') return block;
    let envelope;
    try { envelope = JSON.parse(block.text); } catch { return block; }
    if (!envelope || Array.isArray(envelope) || typeof envelope !== 'object' ||
        !Object.hasOwn(envelope, 'result') || !envelope.tool ||
        typeof envelope.tool.id !== 'string' || !envelope.tool.id.startsWith('openclaw:') ||
        typeof envelope.tool.name !== 'string') return block;
    const {description, label, source, sourceName, ...identity} = envelope.tool;
    if ([description, label, source, sourceName].every(value => value === undefined)) return block;
    changed = true;
    return {...block, text: JSON.stringify({...envelope, tool: identity})};
  });
  return changed ? {...message, content} : message;
}
