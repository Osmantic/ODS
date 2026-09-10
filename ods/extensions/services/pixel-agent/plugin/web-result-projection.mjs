import { isDeepStrictEqual } from "node:util";

const record = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);

// Preserve native evidence blocks instead of serializing them inside a second
// JSON document. The caller binds this framework envelope to the exact call.
export function projectWebResult(message, envelope) {
  if (!record(message) || message.toolName !== "tool_call" || !record(envelope)) return undefined;
  const { tool, result } = envelope;
  if (!record(tool) || !record(result) || tool.source !== "openclaw" ||
      tool.sourceName !== "core" || !["web_search", "web_fetch"].includes(tool.name) ||
      tool.id !== `openclaw:core:${tool.name}`) return undefined;
  const content = result.content;
  if (!Array.isArray(content) || content.length === 0 ||
      !content.every((block) => record(block) && block.type === "text" && typeof block.text === "string")) {
    return undefined;
  }
  const metadata = { ...result };
  delete metadata.content;
  if (content.length === 1 && record(result.details)) {
    let duplicate = false;
    try { duplicate = isDeepStrictEqual(JSON.parse(content[0].text), result.details); }
    catch { /* Plain text is not a duplicate structured payload. */ }
    if (duplicate) delete metadata.details;
    else if (result.details.aggregated === content[0].text) {
      metadata.details = { ...result.details };
      delete metadata.details.aggregated;
    }
  }
  const failed = message.isError === true || result.isError === true;
  const identity = { id: tool.id, source: tool.source, sourceName: tool.sourceName, name: tool.name };
  return {
    ...message,
    ...(failed ? { isError: true } : {}),
    content: [
      { type: "text", text: JSON.stringify({ tool: identity, result: metadata, ...(failed ? { isError: true } : {}) }) },
      ...content.map((block) => ({ ...block })),
    ],
    details: envelope,
  };
}
