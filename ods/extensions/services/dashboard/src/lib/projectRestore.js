// Portal's "Put back files". The first time a Portal turn touches a Playground
// project, the host keeps a copy of that project's files from before the turn.
// Under the latest turn that changed a project, the owner can put them back
// in one step. A restore first keeps a copy of the files as they are, so it
// can be undone the same way: its receipt names that copy as undo, and the
// host remembers it, so the Undo is still offered after a reload.

const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
export const RUN_ID = new RegExp(`^chatcmpl_${UUID}$`, 'i')
const UNDO = new RegExp(`^restore_${UUID}$`)
const TOKEN = /^[a-f0-9]{64}$/
const PROJECT = /^Playground\/[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/
const RECEIPT_KEYS = 'confirm,failed,failedCount,failure,late,left,leftCount,project,removed,schemaVersion,snapshot,status,undo,written'
// A refusal's code says why nothing changed; any other failure leaves the outcome unknown.
export const REFUSALS = new Set(['project-restore-busy', 'project-snapshot-unavailable', 'project-snapshot-pruned',
  'project-snapshot-damaged', 'project-folder-unusable', 'project-copies-unusable', 'project-restore-unsupported'])

const keysOf = value => Boolean(value) && typeof value === 'object' && !Array.isArray(value) ? Object.keys(value).sort().join(',') : ''
const count = item => Number.isSafeInteger(item) && item >= 0 && item <= 65536
const listed = (items, total) => Array.isArray(items) && items.length <= 8 && items.length <= total
  && items.every(item => typeof item === 'string' && item.length > 0 && item.length <= 512)

function validReceipt(value, project, snapshot) {
  const confirm = value?.confirm
  return keysOf(value) === RECEIPT_KEYS && value.schemaVersion === 2 && ['restored', 'unchanged', 'confirm'].includes(value.status)
    && value.project === project && value.snapshot === snapshot
    && [value.written, value.removed, value.leftCount, value.failedCount].every(count)
    && listed(value.left, value.leftCount) && listed(value.failed, value.failedCount)
    && [null, 'permission', 'in-use', 'changed', 'other'].includes(value.failure) && typeof value.late === 'boolean'
    && (value.status === 'restored' ? UNDO.test(value.undo ?? '') : value.undo === null)
    && (value.status === 'confirm'
      ? keysOf(confirm) === 'overwrite,overwriteCount,remove,removeCount,token' && TOKEN.test(confirm.token ?? '')
        && count(confirm.overwriteCount) && count(confirm.removeCount)
        && listed(confirm.overwrite, confirm.overwriteCount) && listed(confirm.remove, confirm.removeCount)
      : confirm === null)
}

const validOption = (entry, runs) => keysOf(entry) === 'late,project,restore,snapshot' && PROJECT.test(entry.project ?? '')
  && runs.includes(entry.snapshot) && typeof entry.late === 'boolean'
  && (entry.restore === null || keysOf(entry.restore) === 'side,undo' && UNDO.test(entry.restore.undo ?? '')
    && ['before', 'after'].includes(entry.restore.side))

async function post(url, body, signal) {
  return fetch(url, {method: 'POST', signal, cache: 'no-store', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
}

// The run IDs of a chat's finished Portal turns, newest last, at most 64.
export function finishedRuns(messages) {
  const runs = messages.filter(message => message.role === 'assistant' && message.status !== 'streaming'
    && RUN_ID.test(message.task?.runId ?? '')).map(message => message.task.runId)
  return [...new Set(runs)].slice(-64)
}

// For each project whose latest turn that changed it is one of runs: that
// turn (snapshot), whether its copy was made late, and the latest restore's
// undo while the project is still as that restore left it, with the version
// the project holds (side 'before': the files from before the turn).
export async function restoreOptions(runs, signal) {
  const response = await post('/api/pixel/projects/restore-options', {runs}, signal)
  const value = response.ok ? await response.json() : null
  if (keysOf(value) !== 'projects,schemaVersion' || value.schemaVersion !== 1 || !Array.isArray(value.projects)
      || !value.projects.every(entry => validOption(entry, runs))) throw new Error('Portal could not read which turns can be put back')
  return value.projects
}

// Puts project back to the copy named snapshot: a turn's run ID, or the undo
// a restore returned. With confirm, the owner said yes to overwriting what
// changed later. A refusal throws with its code (nothing changed); any other
// failure throws with code 'project-restore-unavailable' (outcome unknown).
export async function restoreProject({chatId, project, snapshot, confirm}, signal) {
  const response = await post('/api/pixel/projects/restore',
    {chat_id: chatId, project, snapshot, ...(confirm ? {confirm} : {})}, signal)
  const refuse = code => Object.assign(new Error('Project files were not put back'), {code})
  const value = await response.json().catch(() => null)
  if (!response.ok) throw refuse(REFUSALS.has(value?.detail?.code) ? value.detail.code : 'project-restore-unavailable')
  if (!validReceipt(value, project, snapshot)) throw refuse('project-restore-unavailable')
  return value
}
