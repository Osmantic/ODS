// Graceful finalization after the run-progress budget stops a response.
//
// The budget is unchanged. Once it is exhausted every tool stays blocked. The
// next refused tool call carries one fixed instruction (OpenClaw's
// before_tool_call blockReason is the only text that reaches the live model:
// tool_result_persist rewrites the saved transcript only), and the model then
// gets exactly one tool-free turn to answer from evidence the run already
// returned. A tool call in that turn still ends the run at that boundary, but
// substantive answer text in the same message is kept as a partial answer (the
// calls are refused and never run). Any further model call, or an empty or
// degenerate answer, falls back to the canned stop text, followed by the
// host's list of pages read successfully in the response, unless the stop
// synthesis (stop-synthesis.mjs) answers from those pages first.
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

export const PROGRESS_FINALIZATION_REFUSED_CALLS_NOTE =
  'The answer ended by requesting more tool calls after the limit; ODS refused them, so they did not run.';

// Host-built from this response's successful page-read receipts; only the
// list varies. Titles are page-provided text, reduced to plain words.
export const PROGRESS_READ_PAGES_HEADING =
  'Pages Pixel read successfully in this response before the stop (listed by ODS from its read receipts; ' +
  'titles are as each page reported them):';
export const MAX_READ_PAGES_LISTED = 8;

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

// Visible text of an assistant message: its text blocks only (never thinking
// blocks or tool calls), without inline reasoning tags.
export function assistantMessageText(message) {
  if (!Array.isArray(message?.content)) return '';
  let text = message.content.filter(block => block?.type === 'text' && typeof block.text === 'string')
    .map(block => block.text).join('\n');
  text = text.replace(/<(think|thinking|reasoning)>[\s\S]*?<\/\1>/gi, '');
  const stray = text.search(/<\/(?:think|thinking|reasoning)>/i);
  if (stray >= 0) text = text.slice(text.indexOf('>', stray) + 1);
  return text.trim();
}

// Text sent together with the answer turn's tool calls usually narrates them
// ("Let me search once more:"). It is kept only when, besides passing every
// check a tool-free answer passes, it still carries substantial content once
// such narration is set aside: at least MIN_PARTIAL_CONTENT_CHARS letters or
// digits across at least two other lines or sentences.
const MIN_PARTIAL_CONTENT_CHARS = 160;
const MIN_PARTIAL_CONTENT_UNITS = 2;
const NARRATION = /^(?:(?:ok(?:ay)?|now|next|first|then|so|alright|good|great|also|finally)\b[,!.:]?\s*)*(?:let(?:'s|\s+me|\s+us)\b|(?:i|we)(?:'ll|'m going to|'re going to|\s+(?:will|am going to|are going to|need to|still need to|should|must|want to|have to|can now))\b|(?:agora\s+)?(?:vou|irei|vamos|preciso|precisamos|deixe-me|deixa eu)\b)/i;
const alphanumerics = value => value.match(/[\p{L}\p{N}]/gu)?.length ?? 0;

export function partialFinalizationAnswer(text, options) {
  const answer = progressFinalizationAnswer(text, options);
  if (!answer) return undefined;
  let chars = 0, units = 0;
  for (const line of answer.split('\n')) {
    for (const sentence of line.split(/(?<=[.!?])\s+(?=\S)/)) {
      const unit = sentence.replace(/^[\s>#*_`|~+\-–—•]*(?:\d{1,3}[.)]\s*)?[\s*_`]*/u, '');
      const count = alphanumerics(unit);
      if (count < 3 || NARRATION.test(unit)) continue;
      chars += count;
      units += 1;
    }
  }
  return chars >= MIN_PARTIAL_CONTENT_CHARS && units >= MIN_PARTIAL_CONTENT_UNITS ? answer : undefined;
}

// The fallback's page list: host receipts only, at most MAX_READ_PAGES_LISTED.
export function composeReadPages(pages) {
  const listed = (Array.isArray(pages) ? pages : [])
    .filter(page => typeof page?.url === 'string' && page.url.length <= 2048 && /^https?:\/\//.test(page.url));
  if (!listed.length) return '';
  const lines = listed.slice(0, MAX_READ_PAGES_LISTED)
    .map(({url, title}) => typeof title === 'string' && title ? `- ${title} — <${url}>` : `- <${url}>`);
  if (listed.length > MAX_READ_PAGES_LISTED) lines.push(`- and ${listed.length - MAX_READ_PAGES_LISTED} more`);
  return `${PROGRESS_READ_PAGES_HEADING}\n\n${lines.join('\n')}`;
}

// Model answer first (the requested format stays intact), then host facts.
// `synthesis` marks a stop synthesis (stop-synthesis.mjs): its note, and the
// pages it was given, listed last.
export function composeProgressFinalization(answer, {preview, previewExpected = false,
  verificationStatus, researchLimit = false, unverifiedLinks = [], refusedToolCalls = false,
  requestedTextMissing, synthesis} = {}) {
  const fences = [...answer.matchAll(/^[ \t]{0,3}(`{3,}|~{3,})/gm)].map(match => match[1]);
  const closing = fences.length % 2 ? `\n${fences.at(-1)}` : '';
  const facts = [PROGRESS_FINALIZATION_NOTE];
  if (refusedToolCalls) facts.push(PROGRESS_FINALIZATION_REFUSED_CALLS_NOTE);
  if (typeof synthesis?.note === 'string' && synthesis.note) facts.push(synthesis.note);
  if (researchLimit) facts.push("This response's web research allowance was used up, so no further sources could be read.");
  if (verificationStatus === 'failed') facts.push('The latest recognized test or verification command failed.');
  else if (verificationStatus === 'pending') facts.push('A recognized test or verification command had not finished, so its result is unverified.');
  const links = unverifiedLinks.filter(url => typeof url === 'string' && url.length <= 2048).slice(0, 5);
  if (links.length) facts.push('These linked pages were not read successfully in this response and remain unverified: ' +
    links.map(url => `<${url}>`).join(', ') + '.');
  if (preview?.url) {
    facts.push(`[Open last published preview](${preview.url})`,
      'This is the last verified publication, not proof that all requested work completed.');
    if (typeof requestedTextMissing === 'string' && requestedTextMissing) facts.push(requestedTextMissing);
  } else if (previewExpected) facts.push('ODS did not verify a preview in this response. No localhost URL is live or claimed.');
  const pages = synthesis ? composeReadPages(synthesis.pages) : '';
  if (pages) facts.push(pages);
  return `${answer}${closing}\n\n${facts.join('\n\n')}`;
}

// Per-run state. Phases:
//   idle        budget not exhausted
//   unavailable exhausted, but this run keeps the strict stop (not eligible)
//   pending     exhausted; the model has not yet been shown the instruction
//   instructed  a refused tool call returned the instruction
//   turn        the single tool-free answer turn is in progress
//   answered    a deliverable answer was captured
//   partial     the answer turn called tools, and its text was kept as a
//               partial answer; the calls are refused and the run ends
//   failed      the turn was forfeited; deliver the canned stop text
// Parallel sibling calls of the model round that received the instruction
// share it; any other call forfeits the answer turn.
const MAX_SIBLING_INSTRUCTIONS = 8;
const MAX_TRACKED_CALLS = 16;

export function createProgressFinalization() {
  let phase = 'idle';
  let unawareCalls = 0;
  let instructedRound;
  let instructions = 0;
  let answer;
  // Tool-call IDs: those refused with the instruction, the answer turn's
  // message (its text and calls, when written before the calls are
  // dispatched), the call that forfeited the turn (when the message is written
  // after it), and the calls of the message whose text became the partial.
  const instructedCalls = new Set();
  let turnMessage;
  let forfeitCall;
  let partialCalls = new Set();
  const fail = () => { phase = 'failed'; answer = undefined; forfeitCall = undefined; turnMessage = undefined; };
  const callId = value => typeof value === 'string' && value.length > 0 && value.length <= 256 ? value : undefined;
  return {
    get phase() { return phase; },
    get answer() { return phase === 'answered' || phase === 'partial' ? answer : undefined; },
    get partial() { return phase === 'partial'; },
    // The harness abort waits while the single answer turn is still available.
    get abortDeferred() { return ['pending', 'instructed', 'turn', 'answered'].includes(phase); },
    arm(eligible) {
      if (phase === 'idle') phase = eligible ? 'pending' : 'unavailable';
      return phase;
    },
    // The budget re-opened for a refusal's remedy (correctFuse in
    // run-progress-budget.mjs) before any tool boundary delivered the
    // instruction: the model never saw the stop. Only 'pending' returns to
    // 'idle'; once instructed, answering, or stopped, nothing is undone.
    disarm() {
      if (phase !== 'pending') return false;
      phase = 'idle';
      unawareCalls = 0;
      return true;
    },
    // A tool call after exhaustion, in observed model round `round` (0 when the
    // runtime reports no model calls). 'instruct' means its refusal carries the
    // instruction; 'stop' means the canned stop text (and the abort) applies.
    // Without an observed round, only the first refusal can carry it.
    toolBoundary(round = 0, toolCallId) {
      const id = callId(toolCallId);
      if (phase === 'pending') {
        phase = 'instructed';
        instructedRound = round;
        instructions = 1;
        if (id) instructedCalls.add(id);
        return 'instruct';
      }
      if (phase === 'instructed' && round > 0 && round === instructedRound &&
          instructions < MAX_SIBLING_INSTRUCTIONS) {
        instructions += 1;
        if (id && instructedCalls.size < MAX_TRACKED_CALLS) instructedCalls.add(id);
        return 'instruct';
      }
      if (phase === 'turn' && id && turnMessage?.calls.has(id)) {
        // The answer turn's own message: keep its text, refuse its calls.
        const candidate = turnMessage;
        fail();
        if (candidate.answer) {
          phase = 'partial';
          answer = candidate.answer;
          partialCalls = candidate.calls;
        }
        return 'stop';
      }
      if (phase === 'turn') {
        fail();
        forfeitCall = id;
        return 'stop';
      }
      // Sibling calls of the partial answer's message are refused as well.
      if (phase === 'partial' && id && partialCalls.has(id)) return 'stop';
      if (phase === 'instructed' || phase === 'answered' || phase === 'partial') fail();
      return 'stop';
    },
    // An assistant message with tool calls, as the runtime writes it to the
    // transcript. Only the answer turn's message can supply a partial answer:
    // it is identified by call ID, never by timing alone. `validate` returns
    // the deliverable text or undefined (see partialFinalizationAnswer).
    assistantMessage(text, toolCallIds, validate) {
      const ids = (Array.isArray(toolCallIds) ? toolCallIds : []).map(callId).filter(Boolean).slice(0, MAX_TRACKED_CALLS);
      if (!ids.length || typeof validate !== 'function') return phase;
      if (phase === 'turn' && !turnMessage && !ids.some(id => instructedCalls.has(id))) {
        turnMessage = {answer: validate(text), calls: new Set(ids)};
      } else if (phase === 'failed' && forfeitCall && ids.includes(forfeitCall)) {
        forfeitCall = undefined;
        const candidate = validate(text);
        if (candidate) {
          phase = 'partial';
          answer = candidate;
          partialCalls = new Set(ids);
        }
      }
      return phase;
    },
    modelCallStarted() {
      if (phase === 'instructed') phase = 'turn';
      else if (phase === 'turn' || phase === 'answered' || phase === 'partial') fail();
      // A late-written message cannot revive a turn once another call started.
      else if (phase === 'failed') forfeitCall = undefined;
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
