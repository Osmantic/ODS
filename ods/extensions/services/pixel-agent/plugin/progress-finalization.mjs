// Graceful finalization after the run-progress budget stops a response.
//
// The budget is unchanged. Once it is exhausted every tool stays blocked. The
// next refused tool call carries one fixed instruction (OpenClaw's
// before_tool_call blockReason is the only text that reaches the live model:
// tool_result_persist rewrites the saved transcript only), and the model then
// gets exactly one tool-free turn to answer from evidence the run already
// returned. A tool call in that turn, any further model call, or an
// empty/degenerate answer falls back to the canned RUN_PROGRESS_STOP_REASON.
import { RUN_PROGRESS_STOP_REASON } from './run-progress-budget.mjs';
import { promisesExecution } from './completion-assurance.mjs';

// Delivered only as a refused tool call's result at the end of the
// conversation, never in the system prompt. Keep it byte-identical so it adds
// no per-turn variable text (see PR #6647, llama.cpp prefix caching).
export const PROGRESS_FINALIZATION_INSTRUCTION =
  '[ODS Pixel tool limit] This response was stopped after repeated tool failures or attempts without progress. ' +
  'Tools are now disabled: do not call any tool, or your answer is discarded. ' +
  'Answer the owner now using only evidence already returned by tools in this response. ' +
  'Deliver the requested output format as far as that evidence allows; mark each missing or unverified item ' +
  '(use null where the format needs a value) and briefly say why. Do not invent values, sources or results. ' +
  'Do not claim a publication, preview URL, passing test or saved file that a tool result above did not confirm.';

export const PROGRESS_FINALIZATION_NOTE =
  '**Partial answer: Pixel reached its tool limit.** This response was stopped after repeated tool failures ' +
  'or attempts without progress. The answer above was written from evidence gathered before the stop and may be ' +
  'incomplete; items it marks as missing or unverified were not completed. ' +
  'Saved files and previously verified publications were preserved.';

// The composed reply is host verification text (ingress limit: 32 KiB).
const MAX_ANSWER_CHARS = 20000;
const MIN_ANSWER_WORD_CHARS = 20;
const CONTROL_CHARACTERS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g;
// Tool-call syntax emitted as text is an attempted call, not an answer.
const TOOL_CALL_TEXT = /<\/?tool_call\b|<\|tool_call|<function[=\s>]|<\/function>|"tool_calls"\s*:|^\s*\{\s*"name"\s*:\s*"[^"\n]{1,128}"\s*,\s*"(?:arguments|parameters)"\s*:/m;
const LOCAL_URL = /\b(?:https?|wss?):\/\/(?:[^\s/?#@]*@)?(?:localhost|[a-z0-9.-]*\.localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1?\]|host\.docker\.internal)(?::\d+)?(?:[/?#][^\s<>"'`)\]]*)?/gi;

// Returns the sanitized answer, or undefined when it cannot be delivered.
export function progressFinalizationAnswer(text, {localUrlsForbidden = false, allowedUrls = []} = {}) {
  if (typeof text !== 'string') return undefined;
  const answer = text.replace(CONTROL_CHARACTERS, '').trim();
  if (!answer || answer.length > MAX_ANSWER_CHARS) return undefined;
  if ((answer.match(/[\p{L}\p{N}]/gu)?.length ?? 0) < MIN_ANSWER_WORD_CHARS) return undefined;
  if (/^(?:NO_REPLY|No response from OpenClaw\.?)$/i.test(answer)) return undefined;
  if (TOOL_CALL_TEXT.test(answer)) return undefined;
  // Echoing the stop text or instruction is not an answer to the owner.
  if (answer.includes(RUN_PROGRESS_STOP_REASON) || answer.includes('[ODS Pixel tool limit]')) return undefined;
  if (promisesExecution(answer) && answer.length < 900 && !/https?:\/\//.test(answer)) return undefined;
  if (localUrlsForbidden) {
    for (const match of answer.matchAll(LOCAL_URL)) {
      const url = match[0].replace(/[.,;:!?]+$/, '');
      if (!allowedUrls.some(allowed => typeof allowed === 'string' && allowed && url.startsWith(allowed.replace(/\/$/, ''))))
        return undefined;
    }
  }
  return answer;
}

// Model answer first (the requested format stays intact), then host facts.
export function composeProgressFinalization(answer, {preview, previewExpected = false,
  verificationStatus, researchLimit = false, unverifiedLinks = []} = {}) {
  const fences = [...answer.matchAll(/^[ \t]{0,3}(`{3,}|~{3,})/gm)].map(match => match[1]);
  const closing = fences.length % 2 ? `\n${fences.at(-1)}` : '';
  const facts = [PROGRESS_FINALIZATION_NOTE];
  if (researchLimit) facts.push("This response's web research allowance was used up, so no further sources could be read.");
  if (verificationStatus === 'failed') facts.push('The latest recognized test or verification command failed.');
  else if (verificationStatus === 'pending') facts.push('A recognized test or verification command had not finished, so its result is unverified.');
  const links = unverifiedLinks.filter(url => typeof url === 'string' && url.length <= 2048).slice(0, 5);
  if (links.length) facts.push('These linked pages were not read successfully in this response and remain unverified: ' +
    links.map(url => `<${url}>`).join(', ') + '.');
  if (preview?.url) facts.push(`[Open last published preview](${preview.url})`,
    'This is the last verified publication, not proof that all requested work completed.');
  else if (previewExpected) facts.push('ODS did not verify a preview in this response. No localhost URL is live or claimed.');
  return `${answer}${closing}\n\n${facts.join('\n\n')}`;
}

// Per-run state. Phases:
//   idle        budget not exhausted
//   unavailable exhausted, but this run keeps the strict stop (not eligible)
//   pending     exhausted; the model has not yet been shown the instruction
//   instructed  a refused tool call returned the instruction
//   turn        the single tool-free answer turn is in progress
//   answered    a deliverable answer was captured
//   failed      the turn was forfeited; deliver the canned stop text
// Parallel sibling calls of the model round that received the instruction
// share it; any other call forfeits the answer turn.
const MAX_SIBLING_INSTRUCTIONS = 8;

export function createProgressFinalization() {
  let phase = 'idle';
  let unawareCalls = 0;
  let instructedRound;
  let instructions = 0;
  let answer;
  const fail = () => { phase = 'failed'; answer = undefined; };
  return {
    get phase() { return phase; },
    get answer() { return phase === 'answered' ? answer : undefined; },
    // The harness abort waits while the single answer turn is still available.
    get abortDeferred() { return ['pending', 'instructed', 'turn', 'answered'].includes(phase); },
    arm(eligible) {
      if (phase === 'idle') phase = eligible ? 'pending' : 'unavailable';
      return phase;
    },
    // A tool call after exhaustion, in observed model round `round` (0 when the
    // runtime reports no model calls). 'instruct' means its refusal carries the
    // instruction; 'stop' means the canned stop text (and the abort) applies.
    // Without an observed round, only the first refusal can carry it.
    toolBoundary(round = 0) {
      if (phase === 'pending') {
        phase = 'instructed';
        instructedRound = round;
        instructions = 1;
        return 'instruct';
      }
      if (phase === 'instructed' && round > 0 && round === instructedRound &&
          instructions < MAX_SIBLING_INSTRUCTIONS) {
        instructions += 1;
        return 'instruct';
      }
      if (phase === 'instructed' || phase === 'turn' || phase === 'answered') fail();
      return 'stop';
    },
    modelCallStarted() {
      if (phase === 'instructed') phase = 'turn';
      else if (phase === 'turn' || phase === 'answered') fail();
      // The call that was already running when the budget tripped may answer
      // on its own. A second uninstructed call means no tool boundary could
      // deliver the instruction: stop rather than wait any longer.
      else if (phase === 'pending' && ++unawareCalls > 1) fail();
      return phase;
    },
    // Final text is produced only by a model call after the last tool result,
    // so in 'instructed' (no model hook observed the call) it follows the
    // instruction as well.
    accept(text, options) {
      if (!['pending', 'instructed', 'turn'].includes(phase)) return undefined;
      answer = progressFinalizationAnswer(text, options);
      if (answer) phase = 'answered';
      else fail();
      return answer;
    },
  };
}
