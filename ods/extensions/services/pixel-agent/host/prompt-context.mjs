// Inlined by the exact-source SDK repair. Keep all state in the owned session.
import {createHash} from 'node:crypto';

export const ODS_TURN_CONTEXT_TYPE = 'ods.turn-context.v1';
const contextDigest = value => createHash('sha256').update(value).digest('hex');

export function odsPromptContextEnabled(config, agentId) {
  const plugins = config?.plugins;
  return agentId === 'pixel' && plugins?.enabled !== false &&
    plugins?.entries?.['pixel-ods']?.enabled === true &&
    (!Array.isArray(plugins.allow) || plugins.allow.includes('pixel-ods')) &&
    (!Array.isArray(plugins.deny) || !plugins.deny.includes('pixel-ods'));
}

export function prepareOdsTurnContext({config, agentId, runId, sessionId, sessionKey,
  prompt, entries, signal, now = () => new Date().toISOString()}) {
  if (!odsPromptContextEnabled(config, agentId)) return undefined;
  if (signal?.aborted) throw new Error('ODS turn context admission cancelled');
  if (![runId, sessionId, sessionKey, prompt].every(value => typeof value === 'string') ||
      !runId || !sessionId || !sessionKey) throw new Error('ODS turn context identity unavailable');
  const binding = {runId, sessionId, sessionKey, promptSha256: contextDigest(prompt)};
  const prior = entries.filter(entry => entry.type === 'custom_message' &&
    entry.customType === ODS_TURN_CONTEXT_TYPE && entry.details?.runId === runId);
  if (prior.length > 1) throw new Error('Duplicate ODS turn context');
  const previous = prior[0];
  if (previous && (previous.details?.source !== 'pixel-ods' ||
      Object.entries(binding).some(([key, value]) => previous.details[key] !== value) ||
      typeof previous.content !== 'string' ||
      previous.details.contentSha256 !== contextDigest(previous.content))) {
    throw new Error('ODS turn context binding mismatch');
  }
  const startedAt = previous?.details.startedAt ?? now();
  if (typeof startedAt !== 'string' || !Number.isFinite(Date.parse(startedAt))) {
    throw new Error('ODS turn context clock unavailable');
  }
  return Object.freeze({schemaVersion: 1, ...binding, startedAt,
    previous: previous ? Object.freeze({...previous, details: Object.freeze({...previous.details})}) : undefined});
}

export function admitOdsTurnContext(admission, descriptor, signal) {
  if (!admission) {
    if (descriptor !== undefined) throw new Error('ODS context outside enabled Pixel scope');
    return undefined;
  }
  if (signal?.aborted) throw new Error('ODS turn context admission cancelled');
  if (!descriptor || descriptor.schemaVersion !== 1 ||
      ['runId', 'sessionId', 'sessionKey', 'promptSha256', 'startedAt'].some(key => descriptor[key] !== admission[key]) ||
      typeof descriptor.content !== 'string' || descriptor.content.length === 0 ||
      descriptor.content.length > 16384 ||
      (admission.previous && descriptor.content !== admission.previous.content)) {
    throw new Error('Invalid or changed trusted ODS turn context');
  }
  return {role: 'custom', customType: ODS_TURN_CONTEXT_TYPE, content: descriptor.content,
    display: false, timestamp: Date.parse(admission.startedAt), details: {
      source: 'pixel-ods', runId: admission.runId, sessionId: admission.sessionId,
      sessionKey: admission.sessionKey, promptSha256: admission.promptSha256,
      startedAt: admission.startedAt, contentSha256: contextDigest(descriptor.content),
    }};
}

// A compaction may remove a custom message from the active context while its
// durable source entry remains in this branch. Reintroduce only that admitted
// current-run entry, before its active owner message, without rewriting either.
export function restoreOdsTurnContext(messages, context, prompt) {
  if (!context) return messages;
  const matching = messages.filter(message => message.role === 'custom' &&
    message.customType === ODS_TURN_CONTEXT_TYPE && message.details?.runId === context.details.runId);
  if (matching.length > 1 || matching.some(message => message.content !== context.content ||
      ['source','sessionId','sessionKey','promptSha256','startedAt','runId','contentSha256']
        .some(key => message.details[key] !== context.details[key]))) {
    throw new Error('ODS active context differs from its durable receipt');
  }
  if (matching.length) return messages;
  let index = messages.length;
  for (let n = messages.length - 1; n >= 0; n--) {
    const message = messages[n];
    if (message.role !== 'user') continue;
    const content = typeof message.content === 'string' ? message.content :
      (message.content ?? []).filter(block => block.type === 'text').map(block => block.text).join('\n');
    if (content === prompt) index = n;
    break;
  }
  return [...messages.slice(0, index), context, ...messages.slice(index)];
}
