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
// The exact call id, as for reminders: knowing only the tool's name, the small model called other
// tools (pixel_ods_research, pixel_ods_skill) with remember's arguments (e2e).
const LEARN_HINT = 'When the owner tells you a lasting fact about themselves or a preference (diet, people they mention, '
  + 'how they like answers), save it with one call: tool_call with id "openclaw:pixel-ods:pixel_ods_notes" '
  + 'and args {"action": "remember", "text": "<one short sentence about the owner>"}.';
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

// Chats whose current turn proposes no memory, with why: 'answered' (the turn applied the owner's
// answer to a memory card), 'reminder' (the owner asked for a reminder) or 'card' (this turn
// already shows a memory card; another fact waits in Settings). Shared across plugin
// instances (the prompt hook and the tool can come from different ones), as the scheduler is.
const NO_PROPOSAL_TURNS = Symbol.for('ods.pixel-ods.memoryNoProposalTurns');
export function noProposalTurns(shared = globalThis) {
  shared[NO_PROPOSAL_TURNS] ??= new Map();
  return shared[NO_PROPOSAL_TURNS];
}

// The owner's card answers already applied, by chat, fact and choice: each attempt of the answer's
// run builds its prompt again, and the service no longer has the fact waiting the second time.
const APPLIED_ANSWERS = Symbol.for('ods.pixel-ods.memoryAppliedAnswers');
const APPLIED_KEEP = 200;
function appliedAnswers(shared) {
  shared[APPLIED_ANSWERS] ??= new Map();
  return shared[APPLIED_ANSWERS];
}

// submit is integrations.mjs's submitIntegration, passed in: that module imports this one.
export function createMemoryHooks({submit, warn = () => {}, shared = globalThis}) {
  return {
    // Before a turn: the owner's answer to a memory card is applied, then the facts for this chat.
    // reminder: the turn asks for a reminder (integration-tasks.mjs reminderHint gave it one).
    async promptContext(userText, sessionKey, {reminder = false, runId = ''} = {}) {
      const session = chatSession(sessionKey);
      if (!session) return {};
      const notes = {};
      const answer = memoryAnswer(userText);
      // A turn that answers a card only acknowledges it: no new proposal (e2e: the small model
      // proposed "I will remember you're vegetarian." right after the owner saved that fact).
      // A reminder turn gets only the reminder hint (e2e: with both, "remind me in 2 minutes to
      // stretch" became the card "The owner reminds them to stretch in 2 minutes").
      const block = answer ? 'answered' : reminder ? 'reminder' : '';
      if (block) noProposalTurns(shared).set(session, block);
      else noProposalTurns(shared).delete(session);
      try {
        if (answer) {
          // By run: the same words in a later turn (the fact proposed again after a delete) are a new answer.
          const applied = appliedAnswers(shared), key = runId ? JSON.stringify([runId, session, answer.text, answer.choice]) : '';
          let status = key ? applied.get(key) : undefined;
          if (status === undefined) {
            const reply = await submit({action: 'memory-answer', session, ...answer}, {timeoutMs: CONTEXT_TIMEOUT_MS});
            if (reply?.ok) {
              status = reply.status;
              if (key && status !== 'unknown') applied.set(key, status);
              if (applied.size > APPLIED_KEEP) applied.delete(applied.keys().next().value);
            } else warn(`Portal memory: answer not applied: ${reply?.message}`);
          }
          if (status !== undefined) notes.answer = ANSWERED[status] ?? ANSWERED.unknown;
        }
        const reply = await submit({action: 'memory-context', session}, {timeoutMs: CONTEXT_TIMEOUT_MS});
        if (reply?.ok && reply.text) notes.facts = reply.text;
        if (reply?.ok && reply.enabled && !reply.quiet && !block) notes.hint = LEARN_HINT;
      } catch (error) {
        // The service is down or not on this system: the turn goes on without memory.
        warn(`Portal memory: unavailable for this turn: ${error.message}`);
      }
      return notes;
    },
  };
}
