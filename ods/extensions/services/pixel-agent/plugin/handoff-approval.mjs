// Checkpoint preview for a trusted owner-control adapter, never an agent tool.
// This does not supply an owner UI, grant OS privileges or persist conversation scope.
import { createHash } from 'node:crypto';

const fail = () => {throw new Error('ODS handoff approval unavailable');};
function freeze(value, depth = 0) {
  if (value && typeof value === 'object') {
    // Match the private store/dashboard's bounded 32-container JSON parser.
    if (depth >= 32) return fail();
    if (!Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype) return fail();
    for (const child of Object.values(value)) freeze(child, depth + 1);
    return Object.freeze(value);
  }
  if (value !== null && !['string', 'boolean', 'number'].includes(typeof value)) return fail();
  if (typeof value === 'number' && !Number.isFinite(value)) return fail();
  return value;
}

export function handoffRecipient(value) {
  if (!value || !['baseUrl,id,kind,label,model,previousProviderId,revision,scope',
      'baseUrl,id,kind,label,model,previousProviderId,revision,scope,selectionScope'].includes(Object.keys(value).sort().join(',')) ||
      ('selectionScope' in value && !['task','conversation','default'].includes(value.selectionScope)) ||
      !/^[a-z][a-z0-9_-]{0,63}$/.test(value.id) ||
      !/^[a-z][a-z0-9_-]{0,63}$/.test(value.previousProviderId) || value.id === value.previousProviderId ||
      !['local', 'ods-peer', 'cloud'].includes(value.kind) || value.scope !== 'run' ||
      !Number.isSafeInteger(value.revision) || value.revision < 0 ||
      ['label','model','baseUrl'].some(key => typeof value[key] !== 'string' || !value[key] || value[key].length > 2048)) return fail();
  const endpoint = new URL(value.baseUrl);
  if (!['http:', 'https:'].includes(endpoint.protocol) || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) return fail();
  return Object.freeze({...value});
}

export function handoffCheckpoint(event, ctx, recipient) {
  if (typeof event?.prompt !== 'string' || typeof event.systemPrompt !== 'string' ||
      !Array.isArray(event.messages) || typeof ctx?.workspaceDir !== 'string' || !ctx.workspaceDir) return fail();
  const pending = new Set();
  for (const message of event.messages) {
    if (!message || typeof message !== 'object') return fail();
    if (message.role === 'assistant' && Array.isArray(message.content)) {
      for (const part of message.content) if (part?.type === 'toolCall') {
        if (typeof part.id !== 'string' || !part.id || pending.has(part.id)) return fail();
        pending.add(part.id);
      }
    } else if (message.role === 'toolResult') {
      if (!pending.delete(message.toolCallId)) return fail();
    }
  }
  if (pending.size) return fail();
  // Preserve entire runtime-supplied history, including completed tool/results.
  // This is a preview, not a rewritten inference prompt or trusted model text.
  const value = {schemaVersion: 1, runId: ctx.runId, sessionId: ctx.sessionId, agentId: ctx.agentId,
    workspaceDir: ctx.workspaceDir, recipient, dataScope: 'conversation-and-this-run-tool-results',
    returnAction: recipient.selectionScope ? 'owner-scope-return-or-end' : 'configured-leader-on-next-run',
    prompt: event.prompt, systemPrompt: event.systemPrompt, messages: event.messages};
  const encoded = JSON.stringify(value);
  if (Buffer.byteLength(encoded, 'utf8') > 2 * 1024 * 1024) return fail();
  const checkpoint = freeze(JSON.parse(encoded));
  return {checkpoint, checkpointDigest: createHash('sha256').update(encoded).digest('hex')};
}

export async function approveHandoff(preview, callback, signal, timeoutMs) {
  if (typeof callback !== 'function' || signal.aborted) return false;
  let timer, onAbort;
  const stopped = new Promise(resolve => {
    onAbort = () => resolve(null);
    signal.addEventListener('abort', onAbort, {once: true});
    timer = setTimeout(onAbort, timeoutMs);
  });
  try {
    const receipt = await Promise.race([stopped, Promise.resolve().then(() => callback({...preview, signal}))]);
    const cloud = preview.checkpoint.recipient.kind === 'cloud';
    return !signal.aborted && receipt?.approved === true &&
      Object.keys(receipt).sort().join(',') === (cloud
        ? 'acceptUnknownCost,allowCloud,approved,checkpointDigest' : 'approved,checkpointDigest') &&
      (!cloud || receipt.allowCloud === true && receipt.acceptUnknownCost === true) &&
      receipt.checkpointDigest === preview.checkpointDigest;
  } catch { return false; }
  finally {clearTimeout(timer); signal.removeEventListener('abort', onAbort);}
}
