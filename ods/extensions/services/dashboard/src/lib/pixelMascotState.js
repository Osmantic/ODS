export function pixelHeaderPose({sending, stopping, restoredActive, restoredChecking, interrupted, restoredActivity, status, task}) {
  if (stopping || restoredChecking || status === 'loading' || status === 'switching') return 'waiting'
  if (restoredActive) return 'working'
  if (sending) return task?.state === 'running' && task?.calls > 0 ? 'working' : 'thinking'
  if (status !== 'available' || (interrupted && restoredActivity === 'unknown')) return 'blocked'
  return 'idle'
}

export function pixelReplyPose(message, active) {
  if (message.status === 'error' || message.task?.state === 'failed') return 'blocked'
  if (message.status === 'stopped') return 'idle'
  // A restored/abandoned streaming placeholder is not proof of ongoing work.
  if (message.status === 'streaming') return active ? (message.task?.state === 'running' && message.task?.calls > 0 ? 'working' : 'thinking') : 'waiting'
  return message.content ? 'done' : 'idle'
}
