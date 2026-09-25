// Per-turn guidance: the contracts selected for the current owner message,
// goal mode and repository evidence. before_prompt_build returns it as
// appendContext, which OpenClaw appends to the current owner message on every
// model call of the run, but OpenClaw stores that message without it. On the
// next owner turn the same message is replayed shorter, so a local server has
// to re-read everything after it (tower1 round 056: breaks at exactly the end
// of the previous owner message, 17,601 and 25,956 tokens).
//
// Storing the owner message together with the exact text the model saw keeps
// every later request an append-only extension of the earlier ones.

export const TURN_GUIDANCE_HEADER = '[ODS Pixel guidance for this owner message]';
const SEPARATOR = '\n\n';
const MARKER = `${SEPARATOR}${TURN_GUIDANCE_HEADER}\n`;
const MAX_PENDING = 256;

export function formatTurnGuidance(text) {
  const body = typeof text === 'string' ? text.trim() : '';
  return body ? `${TURN_GUIDANCE_HEADER}\n${body}` : '';
}

// OpenClaw composes the model prompt as [prependContext, prompt, appendContext]
// joined by a blank line; ODS contributes appendContext only.
export function withTurnGuidance(prompt, guidance) {
  return guidance ? `${prompt}${SEPARATOR}${guidance}` : prompt;
}

export function stripTurnGuidance(text) {
  if (typeof text !== 'string') return text;
  const index = text.indexOf(MARKER);
  return index === -1 ? text : text.slice(0, index);
}

// OpenClaw appends the context to the first text block of the owner message
// (replaceLastUserTextPrompt); mirror exactly that block.
function firstTextIndex(content) {
  if (!Array.isArray(content)) return -1;
  return content.findIndex(part => part && typeof part === 'object' && part.type === 'text' &&
    typeof part.text === 'string');
}

function textOf(content) {
  if (typeof content === 'string') return content;
  const index = firstTextIndex(content);
  return index === -1 ? undefined : content[index].text;
}

function replaceText(message, text) {
  if (typeof message.content === 'string') return {...message, content: text};
  const index = firstTextIndex(message.content);
  const content = message.content.slice();
  content[index] = {...content[index], text};
  return {...message, content};
}

// Plugin classifiers read owner prose. Persisted guidance is ODS text, not an
// owner request, so earlier owner messages are classified without it.
export function withoutPersistedTurnGuidance(messages) {
  if (!Array.isArray(messages)) return messages;
  let changed = false;
  const next = messages.map(message => {
    if (!message || message.role !== 'user') return message;
    if (typeof message.content === 'string') {
      const stripped = stripTurnGuidance(message.content);
      if (stripped === message.content) return message;
      changed = true;
      return {...message, content: stripped};
    }
    if (!Array.isArray(message.content)) return message;
    let partChanged = false;
    const content = message.content.map(part => {
      if (!part || typeof part !== 'object' || typeof part.text !== 'string') return part;
      const stripped = stripTurnGuidance(part.text);
      if (stripped === part.text) return part;
      partChanged = true;
      return {...part, text: stripped};
    });
    if (!partChanged) return message;
    changed = true;
    return {...message, content};
  });
  return changed ? next : messages;
}

export function createTurnGuidancePersistence({agentId = 'pixel'} = {}) {
  const pending = new Map();
  const keyFor = context => typeof context?.sessionKey === 'string' && context.sessionKey
    ? context.sessionKey : undefined;
  return {
    // Called from before_prompt_build with the prompt OpenClaw will store and
    // the appendContext it will show the model for this attempt.
    remember(context, prompt, appendContext) {
      const key = keyFor(context);
      if (context?.agentId !== agentId || !key) return;
      pending.delete(key);
      if (typeof prompt !== 'string' || !prompt.trim() || typeof appendContext !== 'string' ||
          !appendContext.trim()) return;
      if (pending.size >= MAX_PENDING) pending.delete(pending.keys().next().value);
      pending.set(key, {prompt, guidance: appendContext});
    },
    forget(context) {
      const key = keyFor(context);
      if (key) pending.delete(key);
    },
    // before_message_write is synchronous. Rewrite only the owner message of
    // the attempt that received the guidance, exactly once.
    beforeMessageWrite(event, context) {
      const agent = context?.agentId ?? event?.agentId;
      const key = keyFor(context) ?? keyFor(event);
      const message = event?.message;
      if (agent !== agentId || !key || message?.role !== 'user') return undefined;
      const entry = pending.get(key);
      if (!entry) return undefined;
      const text = textOf(message.content);
      if (text === undefined) return undefined;
      if (text === withTurnGuidance(entry.prompt, entry.guidance)) {
        pending.delete(key);
        return undefined;
      }
      if (text !== entry.prompt) return undefined;
      pending.delete(key);
      return {message: replaceText(message, withTurnGuidance(text, entry.guidance))};
    },
    pendingCount: () => pending.size,
  };
}
