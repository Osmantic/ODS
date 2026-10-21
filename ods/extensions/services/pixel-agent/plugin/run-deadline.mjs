// Portal's run deadline: agents.defaults.timeoutSeconds, pinned to 1800 by the
// ODS installers. OpenClaw arms it once per attempt and, when it fires, aborts
// the attempt wherever it is, even between dependent edits. Its own reply then
// tells the owner to raise that setting, which ODS resets (RTX Spark,
// 2026-10-16: a 30-minute game fix stopped after ui.js was changed to call
// helpers that the failed chars.js edit never defined).
//
// The guard records when each owner response and each of its attempts began,
// warns the model through persisted tool results as the deadline nears, and
// replaces the runtime's advice with what happened. Pure functions; the
// caller supplies the clock.
import { assistantMessageText } from './progress-finalization.mjs';

// before_prompt_build records the start just after the runtime arms its timer,
// so the recorded time runs slightly short of the runtime's.
export const RUN_DEADLINE_GRACE_MS = 15000;

const COMPLETED_STOP_REASONS = new Set(['stop', 'end_turn', 'completed']);

function duration(ms, adjective = false) {
  const [value, unit] = ms < 60000 ? [Math.round(ms / 1000), 'second'] : [Math.round(ms / 60000), 'minute'];
  return adjective ? `${value}-${unit}` : `${value} ${unit}${value === 1 ? '' : 's'}`;
}

// Remaining time at which each notice is due: 5 and 2 minutes of ODS's 30.
function noticeThresholds(timeoutMs) {
  return [Math.min(300000, timeoutMs / 6), Math.min(120000, timeoutMs / 15)];
}

// Mirrors OpenClaw's resolveAgentTimeoutSeconds for the integer values ODS
// pins. Anything else (unset, fractional, 0) gets no deadline handling.
// Each attempt of a response (a retry, a compaction retry, a revision) gets a
// fresh runtime timer, but Portal's ingress stops the whole response 1920 s
// after it began. So the notices keep the response's start and delivered
// stages from the previous attempt; only the classification uses this one's.
export function createRunDeadline(timeoutSeconds, attemptStartedAt, previous = undefined) {
  if (!Number.isSafeInteger(timeoutSeconds) || timeoutSeconds <= 0) return undefined;
  return {startedAt: previous?.startedAt ?? attemptStartedAt, attemptStartedAt, timeoutMs: timeoutSeconds * 1000,
    notices: previous?.notices ?? 0};
}

// The notice text holds only durations from config, never clock values, so
// its bytes are stable (see PR #6647, llama.cpp prefix caching).
function noticeText(stage, timeoutMs) {
  const [first, second] = noticeThresholds(timeoutMs);
  return stage === 1
    ? `[ODS Pixel time limit] About ${duration(first)} remain before Portal stops this response at its ` +
      `${duration(timeoutMs, true)} limit, even in the middle of a change. Do not start new work. ` +
      'If a change across several files is only partly made, finish it now or undo the parts already made, ' +
      'so the saved project still works. Then stop calling tools and reply to the owner: what is done and checked, ' +
      'what is left, and that sending "continue" resumes the rest.'
    : `[ODS Pixel time limit] About ${duration(second)} remain before Portal stops this response. ` +
      'Make at most the one edit needed to keep the saved files working together, then stop calling tools and ' +
      'reply to the owner with what is done, what is left, and that sending "continue" resumes the rest.';
}

// The highest notice now due that was not yet delivered, or undefined. Each
// stage is delivered once per response (a compaction resets `notices`, see
// the guard); after a jump past both thresholds only the second.
export function takeRunDeadlineNotice(deadline, now) {
  if (!deadline) return undefined;
  const remaining = deadline.timeoutMs - (now - deadline.startedAt);
  const [first, second] = noticeThresholds(deadline.timeoutMs);
  const stage = remaining <= second ? 2 : remaining <= first ? 1 : 0;
  if (stage <= deadline.notices) return undefined;
  deadline.notices = stage;
  return noticeText(stage, deadline.timeoutMs);
}

// agent_end of an attempt the deadline ended. No plugin hook sees the
// runtime's timeout kind, so this is inferred: the attempt failed, ran its
// full time, and did not end with a completed answer (OpenClaw delivers such
// an answer itself, like its recoveredFinalAssistantTextAfterPromptTimeout).
export function runDeadlineReached(deadline, event, now) {
  if (!deadline || event?.success !== false) return false;
  if (now - deadline.attemptStartedAt < deadline.timeoutMs - RUN_DEADLINE_GRACE_MS) return false;
  const last = Array.isArray(event.messages) ? event.messages.findLast(message => message?.role === 'assistant') : undefined;
  return !(COMPLETED_STOP_REASONS.has(String(last?.stopReason ?? '').trim().toLowerCase()) &&
    assistantMessageText(last));
}

// Owner copy. No configuration advice: ODS manages the limit. The unfinished
// change is named only when this response changed workspace files.
export function runDeadlineStopText(deadline, workspaceChanged) {
  return `**Portal stopped this response after ${duration(deadline.timeoutMs)}, the most one response can run.** ` +
    'This request needed more time. ' +
    (workspaceChanged ? 'Files saved before the stop are kept, but the last change may be unfinished, ' +
      'so the project may not work yet. ' : '') +
    'Send "continue" to pick up where it left off. ' +
    'If it keeps happening, [get help on Discord](https://discord.gg/4ntNp9MAwC).';
}
