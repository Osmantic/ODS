import { describe, test, expect, vi } from 'vitest'
import { createRecoveryTracker } from '../recoveryTracker'

describe('createRecoveryTracker', () => {
  test('recordFailure increments counter and returns the new count', () => {
    const tracker = createRecoveryTracker()
    expect(tracker.recordFailure()).toBe(1)
    expect(tracker.recordFailure()).toBe(2)
    expect(tracker.recordFailure()).toBe(3)
  })

  test('calls onThresholdReached exactly once at the default threshold (3)', () => {
    const onThresholdReached = vi.fn()
    const tracker = createRecoveryTracker({ onThresholdReached })

    tracker.recordFailure()
    tracker.recordFailure()
    expect(onThresholdReached).not.toHaveBeenCalled()

    tracker.recordFailure() // crosses threshold
    expect(onThresholdReached).toHaveBeenCalledTimes(1)

    // Subsequent failures do NOT re-fire the callback
    tracker.recordFailure()
    tracker.recordFailure()
    expect(onThresholdReached).toHaveBeenCalledTimes(1)
  })

  test('recordSuccess resets the counter and calls onRecovered when previously past threshold', () => {
    const onThresholdReached = vi.fn()
    const onRecovered = vi.fn()
    const tracker = createRecoveryTracker({ onThresholdReached, onRecovered })

    tracker.recordFailure()
    tracker.recordFailure()
    tracker.recordFailure() // past threshold
    expect(onThresholdReached).toHaveBeenCalledTimes(1)

    tracker.recordSuccess()
    expect(onRecovered).toHaveBeenCalledTimes(1)

    // Counter has reset — failures must accumulate again before threshold re-fires
    expect(tracker.recordFailure()).toBe(1)
    expect(onThresholdReached).toHaveBeenCalledTimes(1)

    tracker.recordFailure()
    tracker.recordFailure() // crosses threshold again
    expect(onThresholdReached).toHaveBeenCalledTimes(2)
  })

  test('recordSuccess does NOT call onRecovered when never past threshold', () => {
    const onRecovered = vi.fn()
    const tracker = createRecoveryTracker({ onRecovered })

    tracker.recordFailure()
    tracker.recordFailure() // counter=2, below threshold=3
    tracker.recordSuccess()

    expect(onRecovered).not.toHaveBeenCalled()
  })

  test('honors a custom threshold', () => {
    const onThresholdReached = vi.fn()
    const tracker = createRecoveryTracker({ threshold: 2, onThresholdReached })

    tracker.recordFailure()
    expect(onThresholdReached).not.toHaveBeenCalled()
    tracker.recordFailure()
    expect(onThresholdReached).toHaveBeenCalledTimes(1)
  })

  test('works without any callbacks (no throw)', () => {
    const tracker = createRecoveryTracker()
    expect(() => {
      tracker.recordFailure()
      tracker.recordFailure()
      tracker.recordFailure()
      tracker.recordSuccess()
    }).not.toThrow()
  })
})
