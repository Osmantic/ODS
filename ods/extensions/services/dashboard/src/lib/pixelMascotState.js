export function pixelHeaderPose({sending, stopping, restoredActive, restoredChecking, interrupted, restoredActivity, status, task}) {
  if (stopping || restoredChecking || status === 'loading' || status === 'switching') return 'waiting'
  if (restoredActive) return 'working'
  if (sending) return task?.state === 'running' && task?.calls > 0 ? 'working' : 'thinking'
  if (status !== 'available' || (interrupted && restoredActivity === 'unknown')) return 'blocked'
  return 'idle'
}

// The reply's own outcome sets its pose, never the run's telemetry: a receipt
// can record a failed attempt beside a delivered answer. Only a reply that
// really failed keeps the brief "ended early" pose; a goal reply's outcome is
// its team's state.
export function pixelReplyPose(message, active) {
  if (message.status === 'error' || message.goalState === 'failed') return 'blocked'
  if (message.status === 'stopped' || message.goalState === 'cancelled') return 'idle'
  // A restored/abandoned streaming placeholder is not proof of ongoing work.
  if (message.status === 'streaming') return active ? (message.task?.state === 'running' && message.task?.calls > 0 ? 'working' : 'thinking') : 'waiting'
  return message.content ? 'done' : 'idle'
}
