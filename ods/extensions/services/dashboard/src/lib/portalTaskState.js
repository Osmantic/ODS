import {useSyncExternalStore} from 'react'

// Portal streams one live task into this page, so a new task waits for it.
// The default local model serves one request at a time (LLAMA_PARALLEL=1),
// the Pixel edge takes one image turn at a time, and a task left mid-stream
// cannot be reattached live: its chat shows only the final result later.
// Team and goal runs are tracked by the server and never block a new task.
const NEW_TASK_RUNNING = 'Portal is finishing the current task; you can start a new one when it’s done, or stop it.'
const NEW_TASK_STOPPING = 'Portal is stopping the current task; you can start a new one when it has stopped.'
const NEW_TASK_WAIT = 'Portal is finishing a step in this chat; you can start a new task in a moment.'

export function newTaskBlockedReason({running, stopping, checking, launching, compacting}) {
  if (stopping) return NEW_TASK_STOPPING
  if (running) return NEW_TASK_RUNNING
  return checking || launching || compacting ? NEW_TASK_WAIT : ''
}

// The composer stays editable while Portal works. Sending waits, and says why
// instead of doing nothing.
export function sendBlockedReason({deleted, running, stopping, waiting, ready}) {
  if (deleted) return ''
  if (stopping) return 'Portal is stopping the current task. Your message is kept here; send it when Portal has stopped.'
  if (running) return 'Portal is finishing the current task. Your message is kept here; send it when Portal is done, or stop the task.'
  if (waiting || !ready) return 'Portal is not ready for a new message yet. Your message is kept here; send it when Portal is ready.'
  return ''
}

export const ATTACH_BLOCKED = 'Pictures can be added when Portal is ready for your next message. Your draft is kept.'

// The sidebar and the command search live outside the Portal page. The page
// publishes why a new task cannot start, so they can show it.
let newTaskBlocked = ''
const listeners = new Set()

export function publishNewTaskBlocked(reason) {
  const next = typeof reason === 'string' ? reason : ''
  if (next === newTaskBlocked) return
  newTaskBlocked = next
  listeners.forEach(listener => listener())
}

function subscribe(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
const snapshot = () => newTaskBlocked

export function useNewTaskBlocked() {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}
