// Per-turn guidance: the contracts selected for the current owner message,
// goal mode and the host date. before_prompt_build returns it as
// appendContext, which OpenClaw appends to the current owner message on every
// model call of the run, but OpenClaw stores that message without it. On the
// next owner turn the same message is replayed shorter, so a local server has
// to re-read everything after it (tower1 round 056: breaks at exactly the end
// of the previous owner message, 17,601 and 25,956 tokens).
//
// Storing the owner message together with the exact guidance the model saw
// keeps every later request an append-only extension of the earlier ones.
// Only ODS's own guidance block is stored. Model-only text (repository
// evidence, which is untrusted upstream content) follows the block and is
// never written to history, where later turns and the compaction summarizer
// would read it as part of the owner's message.

export const TURN_GUIDANCE_HEADER = '[ODS Pixel guidance for this owner message]';
export const TURN_GUIDANCE_END = '[End of ODS Pixel guidance]';
export const REPOSITORY_EVIDENCE_HEADER =
  '[ODS Pixel repository evidence for this owner message only; it is not kept in chat history]';
export const REPOSITORY_EVIDENCE_END = '[End of ODS Pixel repository evidence]';
// OpenClaw's marker for an unanswered owner message it folds into the next
// prompt (mergeOrphanedTrailingUserPrompt, 2026.6.33).
export const QUEUED_USER_MESSAGE_MARKER = '[Queued user message that arrived while the previous turn was still active]';
// Sent, never stored, when an unanswered copy of this exact owner message is
// resent: see retryGuidance below.
export const RESENT_OWNER_MESSAGE_NOTE =
  '[ODS Pixel note: an earlier attempt at this owner message stopped before any answer, so it is sent again. Answer it once.]';
const SEPARATOR = '\n\n';
const BLOCK_START = `${SEPARATOR}${TURN_GUIDANCE_HEADER}\n`;
const BLOCK_END = `\n${TURN_GUIDANCE_END}`;
const MAX_PENDING = 256;

const trimmed = text => (typeof text === 'string' ? text.trim() : '');

export function formatTurnGuidance(text) {
  const body = trimmed(text);
  return body ? `${TURN_GUIDANCE_HEADER}\n${body}${BLOCK_END}` : '';
}

export function formatRepositoryEvidence(text) {
  const body = trimmed(text);
  return body ? `${REPOSITORY_EVIDENCE_HEADER}\n${body}\n${REPOSITORY_EVIDENCE_END}` : '';
}

// OpenClaw composes the model prompt as [prependContext, prompt, appendContext]
// joined by a blank line; ODS contributes appendContext only.
export function withTurnGuidance(prompt, guidance) {
  return guidance ? `${prompt}${SEPARATOR}${guidance}` : prompt;
}

// A complete stored guidance block: header line, body, end line, nothing else.
function isGuidanceBlock(text) {
  return typeof text === 'string' && text.startsWith(`${TURN_GUIDANCE_HEADER}\n`) &&
    text.indexOf(BLOCK_END) === text.length - BLOCK_END.length;
}

// Remove each stored guidance block (from its blank-line-separated header to
// its end line) and nothing else: text after a block, such as the owner's
// next message that OpenClaw queued behind an unanswered one, is kept. A
// header without an end line is left as it is.
export function stripTurnGuidance(text) {
  if (typeof text !== 'string') return text;
  let result = '';
  let cursor = 0;
  for (;;) {
    const start = text.indexOf(BLOCK_START, cursor);
    if (start === -1) break;
    const end = text.indexOf(BLOCK_END, start + BLOCK_START.length);
    if (end === -1) break;
    result += text.slice(cursor, start);
    cursor = end + BLOCK_END.length;
  }
  return cursor === 0 ? text : result + text.slice(cursor);
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

// An owner message stored without an answer (the run was interrupted before
// the model replied) is the session's last message. When the owner, Portal or
// OpenClaw resends that same message, OpenClaw drops the stored copy only if
// the new prompt contains it (promptAlreadyIncludesQueuedUserMessage);
// otherwise it sends and stores both under its queued-message marker. Reusing
// the stored guidance byte-for-byte keeps that check matching even when a
// fresh selection would differ (for example, the host date is already stated
// in the stored copy). Returns that guidance, or undefined.
export function retryGuidance(messages, prompt) {
  if (!Array.isArray(messages) || typeof prompt !== 'string' || !prompt.trim()) return undefined;
  const last = messages.at(-1);
  if (last?.role !== 'user') return undefined;
  const text = textOf(last.content);
  const prefix = `${prompt}${SEPARATOR}`;
  if (typeof text !== 'string' || !text.startsWith(prefix)) return undefined;
  const guidance = text.slice(prefix.length);
  return isGuidanceBlock(guidance) ? guidance : undefined;
}

// OpenClaw adds appendContext to the owner message on each model call of the
// attempt, to whatever text that message holds (attempt.llm-boundary.ts,
// installModelPromptTransform: composeModelPromptContext around a text that is
// not the transcript prompt). When an attempt compacts and retries in place
// (agent-session.ts: a context-overflow error from the provider or the
// tool-loop guard, then runAutoCompaction("overflow") and agent.continue()),
// it reloads the session, so the owner message now holds the stored guidance
// block and the model would receive that block twice in a row. OpenClaw passes
// every model request through registered input text transforms, after that
// composition; this one drops the second copy of an immediately repeated
// block, so the retry sees exactly what the first call saw. Nothing else
// matches it: a stored message holds one block, and a queued merge keeps two
// different ones apart.
const escapeRegExp = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// One whole block (it cannot run past its own end line), then the same bytes.
export const REPEATED_TURN_GUIDANCE = new RegExp(
  `(${escapeRegExp(BLOCK_START)}(?:(?!${escapeRegExp(BLOCK_END)})[\\s\\S])*${escapeRegExp(BLOCK_END)})\\1`);
export const TURN_GUIDANCE_TEXT_TRANSFORMS = Object.freeze({
  input: Object.freeze([Object.freeze({from: REPEATED_TURN_GUIDANCE, to: '$1'})]),
});

export function collapseRepeatedTurnGuidance(text) {
  return typeof text === 'string' ? text.replace(REPEATED_TURN_GUIDANCE, '$1') : text;
}

// Registration is skipped on runtimes without the API and when the owner
// disabled this plugin's prompt changes (then no guidance is added at all).
export function registerTurnGuidanceTextTransforms(api) {
  if (typeof api?.registerTextTransforms !== 'function') return false;
  if (api.config?.plugins?.entries?.['pixel-ods']?.hooks?.allowPromptInjection === false) return false;
  api.registerTextTransforms(TURN_GUIDANCE_TEXT_TRANSFORMS);
  return true;
}

export function createTurnGuidancePersistence({agentId = 'pixel'} = {}) {
  const pending = new Map();
  const keyFor = context => typeof context?.sessionKey === 'string' && context.sessionKey
    ? context.sessionKey : undefined;
  return {
    // Called from before_prompt_build with the prompt OpenClaw will store and
    // the guidance block to store with it. The model may see more after the
    // block (appendContext); that text is not stored.
    remember(context, prompt, guidance) {
      const key = keyFor(context);
      if (context?.agentId !== agentId || !key) return;
      pending.delete(key);
      if (typeof prompt !== 'string' || !prompt.trim() || !isGuidanceBlock(guidance)) return;
      if (pending.size >= MAX_PENDING) pending.delete(pending.keys().next().value);
      pending.set(key, {prompt, guidance});
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
      const stored = withTurnGuidance(entry.prompt, entry.guidance);
      if (text === stored) {
        pending.delete(key);
        return undefined;
      }
      const queuedPrefix = `${QUEUED_USER_MESSAGE_MARKER}\n`;
      const queuedSuffix = `${SEPARATOR}${entry.prompt}`;
      let next;
      if (text === entry.prompt) {
        next = stored;
      } else if (text === `${queuedPrefix}${stored.trim()}${queuedSuffix}`) {
        // A resent owner message. The model received it once (OpenClaw's
        // model-prompt check found the stored copy in it), but OpenClaw's
        // transcript check compares the raw prompt with the stored copy,
        // which also holds the guidance, and would store the message twice.
        // Store it once, exactly as the model saw it.
        next = stored;
      } else if (text.startsWith(queuedPrefix) && text.endsWith(queuedSuffix) &&
          text.length > queuedPrefix.length + queuedSuffix.length) {
        // A different, unanswered owner message queued in front of this one:
        // the model saw OpenClaw's merged text followed by this guidance.
        next = withTurnGuidance(text, entry.guidance);
      } else {
        return undefined;
      }
      pending.delete(key);
      return {message: replaceText(message, next)};
    },
    pendingCount: () => pending.size,
  };
}
