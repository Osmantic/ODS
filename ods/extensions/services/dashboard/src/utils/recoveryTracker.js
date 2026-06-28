/**
 * createRecoveryTracker — closure-based failure counter for poll loops.
 *
 * Returns { recordFailure, recordSuccess }.
 * - recordFailure() → increments counter; calls onThresholdReached() once
 *   when the counter first crosses `threshold`. Returns the new count so
 *   callers can use it for backoff calculations.
 * - recordSuccess() → resets counter; calls onRecovered() if the tracker
 *   was previously past threshold (i.e. recovery from a degraded state).
 *
 * Used to unify two near-identical "consecutive-failure → banner" patterns
 * in dashboard polling code (Extensions.jsx pollProgress + ConsoleModal
 * log poll). Plain factory + closure (not a React hook) so it can be used
 * inside non-component scopes like `setInterval` callbacks.
 *
 * @param {object} [opts]
 * @param {number} [opts.threshold=3] - failures required to trip the banner
 * @param {Function} [opts.onThresholdReached] - called once when counter >= threshold
 * @param {Function} [opts.onRecovered] - called on first success after threshold
 * @returns {{ recordFailure: () => number, recordSuccess: () => void }}
 */
export function createRecoveryTracker({
  threshold = 3,
  onThresholdReached,
  onRecovered,
} = {}) {
  let counter = 0
  let pastThreshold = false

  return {
    recordFailure() {
      counter += 1
      if (counter >= threshold && !pastThreshold) {
        pastThreshold = true
        onThresholdReached?.()
      }
      return counter
    },
    recordSuccess() {
      const wasPastThreshold = pastThreshold
      counter = 0
      pastThreshold = false
      if (wasPastThreshold) onRecovered?.()
    },
  }
}
