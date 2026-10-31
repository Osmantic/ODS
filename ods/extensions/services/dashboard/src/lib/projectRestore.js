import {parseTaskActivity} from './pixelTaskActivity'

// Portal's "Put back files". The first time a Portal turn touches a Playground
// project, the host keeps a copy of that project's files from before the turn.
// The owner can put them back in one step. A restore first keeps a copy of
// the files as they are, so it can be undone the same way: its receipt names
// that copy as undo.

const UNDO = /^restore_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const KEYS = 'complete,failed,failedCount,kept,keptCount,project,removed,schemaVersion,snapshot,undo,written'

// The Playground projects a turn changed: those whose latest recorded change
// happened while the turn ran. A turn's task lists every project of its chat,
// each with the time of its latest recorded change.
export function changedProjects(task) {
  const parsed = parseTaskActivity(task, task?.runId)
  if (!parsed?.projects) return []
  return parsed.projects.filter(project => project.observedAt >= parsed.startedAt
    && (parsed.finishedAt === null || project.observedAt <= parsed.finishedAt)).map(project => project.relativeDirectory)
}

function validReceipt(value, project, snapshot) {
  const count = item => Number.isSafeInteger(item) && item >= 0 && item <= 65536
  const listed = (items, total) => Array.isArray(items) && items.length <= 8 && items.length <= total
    && items.every(item => typeof item === 'string' && item.length > 0 && item.length <= 512)
  return Boolean(value) && typeof value === 'object' && Object.keys(value).sort().join(',') === KEYS
    && value.schemaVersion === 1 && value.project === project && value.snapshot === snapshot
    && [value.written, value.removed, value.keptCount, value.failedCount].every(count)
    && listed(value.kept, value.keptCount) && listed(value.failed, value.failedCount)
    && typeof value.complete === 'boolean' && (value.undo === null || UNDO.test(value.undo ?? ''))
}

// Puts project back to the copy named snapshot: a turn's run ID, or the undo
// a restore returned. A refusal or an unexpected answer throws with code
// 'busy' (a response is running), 'missing' (that copy is no longer kept),
// 'damaged' (the copy cannot be used; nothing changed) or 'unavailable'.
export async function restoreProject({chatId, project, snapshot}, signal) {
  const response = await fetch('/api/pixel/projects/restore', {
    method: 'POST', signal, cache: 'no-store',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({chat_id: chatId, project, snapshot}),
  })
  const refuse = code => Object.assign(new Error('Project files were not put back'), {code})
  if (!response.ok) throw refuse({404: 'missing', 409: 'busy', 422: 'damaged'}[response.status] ?? 'unavailable')
  const value = await response.json()
  if (!validReceipt(value, project, snapshot)) throw refuse('unavailable')
  return value
}
