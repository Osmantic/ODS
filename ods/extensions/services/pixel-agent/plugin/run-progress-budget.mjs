import { createHash } from 'node:crypto';

// Independent of tool dispatch: the host can reject a call before plugin tool
// hooks run. Model-round accounting must still bound that continuation loop.
export const RUN_PROGRESS_LIMITS = Object.freeze({
  consecutiveFailures: 4,
  totalFailures: 12,
  roundsWithoutProgress: 8,
  identicalSuccesses: 2,
});

// Failures per run whose consecutive charge may wait for one corrected attempt
// (see observeResult). Kept outside RUN_PROGRESS_LIMITS: those four fuses are
// unchanged, and this number grants no failure beyond the total cap.
export const RUN_PROGRESS_CORRECTED_ATTEMPTS = 2;

export const RUN_PROGRESS_STOP_REASON =
  'This response was stopped after repeated tool failures or attempts without progress. ' +
  'Saved files and previously verified publications were preserved. ' +
  'The full request was not completed; continue from the preserved work with a corrected approach.';

const PROGRESS_LANES = new Set(['workspace', 'extension']);
export function progressLaneStopReason(lane) {
  return `The ${lane === 'extension' ? 'extension' : 'workspace'} portion of this response stopped after repeated failures. ` +
    'Do not retry that portion in this response. Continue the separately requested work that remains available. ' +
    'Preserve saved files and accepted operations; the full request remains incomplete.';
}

export function failedToolOutcome(event) {
  if (event?.error) return true;
  let result = event?.result;
  for (let depth = 0; depth < 3 && result && typeof result === 'object'; depth++) {
    const details = result.details;
    if (result.isError === true || ['failed', 'error', 'blocked'].includes(details?.status) ||
        (Number.isInteger(details?.exitCode) && details.exitCode !== 0)) return true;
    result = details?.result;
  }
  return false;
}

export function createRunProgressBudget() {
  let rounds = 0;
  let failures = 0;
  let consecutiveFailures = 0;
  let terminal = false;
  const seenCalls = new Set();
  const successes = new Map();
  const laneFailures = new Map();
  const exhaustedLanes = new Set();
  // At most one failure's consecutive charge is pending: 'global' or a lane.
  let deferred;
  let corrections = 0;
  const charge = counter => {
    if (counter === 'global') { consecutiveFailures += 1; return; }
    const count = (laneFailures.get(counter) ?? 0) + 1;
    laneFailures.set(counter, count);
    if (count >= RUN_PROGRESS_LIMITS.consecutiveFailures) exhaustedLanes.add(counter);
  };
  return {
    get exhausted() { return terminal; },
    // Another guard (the research web-loop terminal) stopped the response.
    // Sticky, like exhaustion; it grants nothing and changes no limit.
    stop() { terminal = true; },
    get exhaustedLanes() { return [...exhaustedLanes]; },
    laneExhausted(lane) { return exhaustedLanes.has(lane); },
    beginModelRound() {
      if (++rounds > RUN_PROGRESS_LIMITS.roundsWithoutProgress) terminal = true;
      return terminal;
    },
    // correctable: the caller established that this failure measured nothing
    // and that its result already gives the model the correction. Its
    // consecutive (or lane) charge waits for the next result: the next failure
    // of any tool charges both, and only a success that resets that same
    // counter forgives it. The total cap counts it at once. Discovery and free
    // corrections neither charge nor forgive it. At most one charge waits, and
    // at most RUN_PROGRESS_CORRECTED_ATTEMPTS per run.
    observeResult({ callId, tool, params, failed, pending = false, discovery = false, lane, correctable = false }) {
      if (terminal || typeof callId !== 'string' || !callId || seenCalls.has(callId)) return;
      seenCalls.add(callId);
      if (seenCalls.size > 256) seenCalls.delete(seenCalls.values().next().value);
      const classifiedLane = PROGRESS_LANES.has(lane) ? lane : undefined;
      if (failed) {
        failures += 1;
        if (correctable === true && deferred === undefined && corrections < RUN_PROGRESS_CORRECTED_ATTEMPTS) {
          deferred = classifiedLane ?? 'global';
          corrections += 1;
        } else {
          charge(classifiedLane ?? 'global');
          if (deferred !== undefined) charge(deferred);
          deferred = undefined;
        }
        // A mixed task may retain its other lane, never an unlimited retry
        // allowance. Unknown calls retain the strict global consecutive fuse;
        // every failure still consumes the unchanged total/global round caps.
        terminal = exhaustedLanes.size === PROGRESS_LANES.size ||
          consecutiveFailures >= RUN_PROGRESS_LIMITS.consecutiveFailures ||
          failures >= RUN_PROGRESS_LIMITS.totalFailures;
        return;
      }
      // Discovery changes the available schemas, not the task's outcome. A
      // successful search between failed actions must not erase their history
      // or let differently worded searches keep a run alive indefinitely.
      if (discovery || tool === 'tool_search' || tool === 'tool_describe' || tool === 'pixel_ods_skill') return;
      consecutiveFailures = 0;
      if (classifiedLane) laneFailures.set(classifiedLane, 0);
      if (deferred === 'global' || deferred === classifiedLane) deferred = undefined;
      // An actual running-process receipt is a verified wait, not a failure.
      // Plain text saying "running" must never be supplied as this signal.
      if (pending) { rounds = 0; return; }
      const fingerprint = createHash('sha256').update(JSON.stringify([tool, params], (_key, value) =>
        value && typeof value === 'object' && !Array.isArray(value)
          ? Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]]))
          : value)).digest('hex');
      const count = (successes.get(fingerprint) ?? 0) + 1;
      successes.set(fingerprint, count);
      if (successes.size > 128) successes.delete(successes.keys().next().value);
      if (count <= RUN_PROGRESS_LIMITS.identicalSuccesses) rounds = 0;
    },
  };
}

// This deliberately does not classify general shell commands as read-only.
// Only a single quoted literal echo is provably unable to mutate the project.
export function isLiteralEcho(command) {
  return typeof command === 'string' &&
    /^\s*echo\s+(?:"[^"$`\\\r\n]*"|'[^'\r\n]*')\s*$/.test(command);
}
