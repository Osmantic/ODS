// Portal's memory of the owner, kept by the host integrations service. Each turn of a Portal
// chat gets the facts the owner let Portal remember, unless the owner set that chat not to be
// remembered. When Portal proposes a fact, the chat shows a card (the same card as
// pixel_ods_ask_user); the owner's answer comes back as the next turn, and this module passes
// it to the service, so saving never depends on the model reading the answer.
const SESSION_PREFIX = 'agent:pixel:openai-user:';
const CONTEXT_TIMEOUT_MS = 5000;
export const MEMORY_QUESTION_ID = 'memory_save';
export const MEMORY_CHOICES = Object.freeze({'Save': 'save', "Don't save": 'discard', 'Always save without asking': 'always'});
const QUESTION = /^Remember this about you\? "([\s\S]{1,300})"\n(Save|Don't save|Always save without asking)\s*$/;
const LEARN_HINT = 'When the owner tells you a lasting fact about themselves or a preference (diet, people they mention, '
  + 'how they like answers), save it with pixel_ods_notes action remember, as one short sentence about the owner.';
// Kept short and positive on purpose: a longer hint listing what not to save ("never a request, a task, a
// reminder... do not call remember") made the small local model stop calling tools at all, reminders
// included. A wrong proposal costs little: the owner is asked before anything is saved.

export const memoryQuestion = text => `Remember this about you? "${text}"`;

// The Portal chat a run belongs to ("" for other sessions, such as a scheduled task).
export function chatSession(sessionKey) {
  const user = typeof sessionKey === 'string' && sessionKey.startsWith(SESSION_PREFIX) ? sessionKey.slice(SESSION_PREFIX.length) : '';
  return /^ods-[a-f0-9]{64}$/.test(user) ? user : '';
}

// The ingress adds a delivery note after the owner's message (pixel_ingress.mjs); it is not theirs.
const DELIVERY_NOTE = /\n\n\[ODS (?:Portal|Pixel) delivery requirement:[\s\S]*$/;

// The owner's answer to a memory card, when this turn is one.
export function memoryAnswer(text) {
  const match = typeof text === 'string' ? text.replace(DELIVERY_NOTE, '').trim().match(QUESTION) : null;
  return match ? {text: match[1], choice: MEMORY_CHOICES[match[2]]} : null;
}

const ANSWERED = {
  saved: 'The owner saved the memory from your last question. Say in one short sentence that you will remember it; call no tools.',
  discarded: 'The owner chose not to save the memory from your last question. Say in one short sentence that you will not keep it; call no tools.',
  unknown: 'That memory question is no longer waiting (it was answered in Settings or the chat was set not to be remembered). Say so in one short sentence; call no tools.',
};

// submit is integrations.mjs's submitIntegration, passed in: that module imports this one.
export function createMemoryHooks({submit, warn = () => {}}) {
  return {
    // Before a turn: the owner's answer to a memory card is applied, then the facts for this chat.
    async promptContext(userText, sessionKey) {
      const session = chatSession(sessionKey);
      if (!session) return {};
      const notes = {};
      try {
        const answer = memoryAnswer(userText);
        if (answer) {
          const reply = await submit({action: 'memory-answer', session, ...answer}, {timeoutMs: CONTEXT_TIMEOUT_MS});
          if (reply?.ok) notes.answer = ANSWERED[reply.status] ?? ANSWERED.unknown;
          else warn(`Portal memory: answer not applied: ${reply?.message}`);
        }
        const reply = await submit({action: 'memory-context', session}, {timeoutMs: CONTEXT_TIMEOUT_MS});
        if (reply?.ok && reply.text) notes.facts = reply.text;
        if (reply?.ok && reply.enabled && !reply.quiet) notes.hint = LEARN_HINT;
      } catch (error) {
        // The service is down or not on this system: the turn goes on without memory.
        warn(`Portal memory: unavailable for this turn: ${error.message}`);
      }
      return notes;
    },
  };
}
