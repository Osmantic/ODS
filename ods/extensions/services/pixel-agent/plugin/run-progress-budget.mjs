import { createHash } from 'node:crypto';

// Independent of tool dispatch: the host can reject a call before plugin tool
// hooks run. Model-round accounting must still bound that continuation loop.
export const RUN_PROGRESS_LIMITS = Object.freeze({
  consecutiveFailures: 4,
  totalFailures: 12,
  roundsWithoutProgress: 8,
  identicalSuccesses: 2,
});

export const RUN_PROGRESS_STOP_REASON =
  'Pixel stopped this response after repeated tool failures or attempts without progress. ' +
  'Saved files and previously verified publications were preserved. ' +
  'The full request was not completed; continue from the preserved work with a corrected approach.';

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
  return {
    get exhausted() { return terminal; },
    beginModelRound() {
      if (++rounds > RUN_PROGRESS_LIMITS.roundsWithoutProgress) terminal = true;
      return terminal;
    },
    observeResult({ callId, tool, params, failed, pending = false }) {
      if (terminal || typeof callId !== 'string' || !callId || seenCalls.has(callId)) return;
      seenCalls.add(callId);
      if (seenCalls.size > 256) seenCalls.delete(seenCalls.values().next().value);
      if (failed) {
        failures += 1;
        consecutiveFailures += 1;
        terminal = consecutiveFailures >= RUN_PROGRESS_LIMITS.consecutiveFailures ||
          failures >= RUN_PROGRESS_LIMITS.totalFailures;
        return;
      }
      consecutiveFailures = 0;
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
