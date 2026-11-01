import {useEffect, useRef, useState} from 'react'
import {Loader2} from 'lucide-react'
import {Link} from 'react-router-dom'
import HelpLink from './HelpLink'
import {recoveryReceipt} from './PortalModelRecovery'

// The one-click fixes /api/pixel/status names in "recovery". Each calls the
// existing API that Portal permissions or Models uses for the same state.
export const PORTAL_RECOVERY_ACTIONS = new Set(['verify-sandbox', 'restore-sandbox', 'resume-model', 'recover-model'])

// A held state implies its model action. Other actions need the API's hint:
// only it knows the configured permissions mode, or that the runtime it owns
// is stopped rather than unreachable.
const STATE_ACTIONS = {unloaded: 'resume-model', 'model-recovery': 'recover-model'}
const HINTED_ACTIONS = {
  available: ['verify-sandbox', 'restore-sandbox'],
  recovery: ['verify-sandbox', 'restore-sandbox'],
  unavailable: ['resume-model'],
}

/** The fix Portal offers for this status and API hint, or null. */
export function portalRecoveryAction(status, action) {
  if (Object.hasOwn(STATE_ACTIONS, status)) return STATE_ACTIONS[status]
  return Object.hasOwn(HINTED_ACTIONS, status) && HINTED_ACTIONS[status].includes(action) ? action : null
}

const COPY = {
  'verify-sandbox': ['Verify now', 'Checking Portal’s sandbox…'],
  'restore-sandbox': ['Restore Sandbox', 'Restoring Sandbox and checking the running tools…'],
  'resume-model': ['Resume model', 'Loading the model. This can take a few minutes…'],
  'recover-model': ['Recover model switch', 'Finishing the model switch…'],
}
// Held states show the API's own explanation. An available or offline Portal
// already shows its status text elsewhere, so it gets these short lines.
const MESSAGES = {
  'verify-sandbox': 'Portal’s sandbox check is out of date. ODS usually re-checks it on its own; if this message stays, you can check it now.',
  'restore-sandbox': 'An earlier permissions change did not finish, so Portal’s sandbox is not verified.',
  'resume-model': 'The model runtime is not running, so Portal is paused.',
  'recover-model': 'A model switch stopped before it finished, so new messages are held.',
}
const HELD_STATES = new Set(['recovery', 'unloaded', 'model-recovery'])
// Verify for an available Portal holds no messages, and the host usually
// re-checks the sandbox by itself within seconds of Portal going idle. So a
// new hint of it is drawn only once it has stayed this long; drawn at once, it
// flashed after every reply (laptop, 2026-10-31). Held states show at once.
export const VERIFY_HINT_DELAY_MS = 20000
// Sandbox attempts. Their outcome ends at the next status update that does
// not name them; Resume model keeps its outcome while Portal reads "switching".
const ACCESS_ACTIONS = new Set(['verify-sandbox', 'restore-sandbox'])
const REVISION = /^[a-f0-9]{64}$/
const verified = value => value?.available === true && value.runtime_verified === true && value.pending === false
  && value.reason == null && ['sandboxed', 'full-access'].includes(value.effective_mode)

class RecoveryFailure extends Error {
  constructor(cause, next, {help = true} = {}) {
    super(cause)
    this.next = next
    this.help = help
  }
}

// The API's own words for a refusal, as one bounded sentence.
function apiMessage(body) {
  const detail = body?.detail
  const text = (typeof detail === 'string' ? detail
    : [detail?.message, detail?.error, body?.error].find(value => typeof value === 'string'))?.trim().slice(0, 300)
  return text ? /[.!?]$/.test(text) ? text : `${text}.` : ''
}

async function requestJson(url, options, timeout) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const response = await fetch(url, {...options, cache: 'no-store', signal: controller.signal})
    let body = null
    try { body = await response.json() } catch { body = null }
    return {ok: response.ok, status: response.status, body}
  } catch {
    throw new RecoveryFailure('ODS did not answer, so the result is not confirmed.',
      'Portal keeps checking. If this notice stays, choose the button again.')
  } finally {
    clearTimeout(timer)
  }
}

const post = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body})

// Verify Sandbox and Restore Sandbox from Portal permissions: a fresh read for
// the revision, then {"mode":"sandboxed"}. Never Full Access.
async function recoverAccess() {
  const read = await requestJson('/api/pixel/access-mode', {}, 45000)
  const current = read.body
  if (!read.ok || current?.available !== true || !REVISION.test(current?.revision ?? '')) {
    throw new RecoveryFailure('ODS could not read Portal’s permissions, so nothing was changed.',
      'Wait a moment and try again.')
  }
  if (verified(current)) return
  if (current.busy) {
    throw new RecoveryFailure('Portal is still finishing earlier work, so nothing was changed.',
      'Try again when that work stops.', {help: false})
  }
  const change = await requestJson('/api/pixel/access-mode',
    post(JSON.stringify({mode: 'sandboxed', revision: current.revision, confirmed: false})), 340000)
  if (change.status === 409 && change.body?.detail?.code === 'model_lifecycle_busy') {
    throw new RecoveryFailure('ODS is busy with a model or Portal change, so nothing was changed.',
      'Try again in a moment.', {help: false})
  }
  if (!change.ok || !verified(change.body)) {
    throw new RecoveryFailure('Portal could not confirm its sandbox, so messages stay held.',
      'Try again. If it fails again, open Portal permissions in Settings to see what is unfinished.')
  }
}

// Resume model from Models: start the stopped runtime with the saved model.
async function resumeModel() {
  const result = await requestJson('/api/models/runtime/start', post('{}'), 1225000)
  if (result.ok && result.body?.status === 'started') return
  const cause = apiMessage(result.body)
  throw new RecoveryFailure(cause ? `The model did not start: ${cause}` : 'The model did not start.',
    'Wait a moment and choose Resume model again. If it keeps failing, open Models to check the model.')
}

// Recover model switch from Models: finish or undo the existing transaction.
// It never loads another model; a release without proof is a separate choice.
async function recoverModel(release) {
  const result = await requestJson('/api/models/recovery', post(release ? '{"releaseUnverified":true}' : '{}'), 405000)
  const value = recoveryReceipt(result.body)
  if (result.ok && value && !value.pending) return 'done'
  if (value?.reason === 'model-recovery-proof-required' && value.releasable === true && !release) return 'releasable'
  throw new RecoveryFailure(value?.reason === 'model-recovery-proof-required'
    ? 'The interrupted switch still needs repair. The saved state has been preserved.'
    : 'The model switch could not be finished, so messages stay held.',
  'Open Models to read its current state and choose another model if needed.')
}

/**
 * One primary button for a held Portal state, with progress and a helpful failure.
 * `update` counts status updates, so it changes on every poll even when the
 * status and its hint do not.
 */
export default function PortalRecovery({status, action = null, detail = '', update = 0, onSettled}) {
  // {action, phase: working|failed|releasable|done, failure, update}. A run
  // stays visible while Portal's status moves through it (Resume model reads
  // as a switch), so its outcome is never lost. A finished run records the
  // status update count it finished at.
  const [run, setRun] = useState(null)
  // What the status names now, and what is drawn: a new Verify hint for an
  // available Portal waits until it has stayed VERIFY_HINT_DELAY_MS. One the
  // owner already acted on is not new, so its button keeps working.
  const named = portalRecoveryAction(status, action)
  const quiet = status === 'available' && named === 'verify-sandbox'
  const [quietDue, setQuietDue] = useState(false)
  const chosen = quiet && !quietDue && run?.action !== named ? null : named
  const lock = useRef(false), mounted = useRef(true)
  const settled = useRef(onSettled)
  settled.current = onSettled
  const updates = useRef(update)
  updates.current = update
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  // The hint has to stay for the whole delay: an update without it restarts it.
  useEffect(() => {
    setQuietDue(false)
    if (!quiet) return undefined
    const timer = setTimeout(() => setQuietDue(true), VERIFY_HINT_DELAY_MS)
    return () => clearTimeout(timer)
  }, [quiet])
  // Another held state, or none, retires an earlier outcome.
  useEffect(() => {
    setRun(current => current && current.phase !== 'working' && current.action !== named ? null : current)
  }, [named])
  // A finished Sandbox attempt also ends at a later status update that does
  // not name it, even an unchanged one. A reply that starts meanwhile withdraws
  // the hint, and the host's own re-check then clears it without naming it
  // again, so waiting for a change left a stale attempt and its button up.
  useEffect(() => {
    setRun(current => current && current.phase !== 'working' && ACCESS_ACTIONS.has(current.action)
      && current.update < update && current.action !== named ? null : current)
  }, [update, named])

  async function start(release = false) {
    const target = run?.phase === 'releasable' ? run.action : chosen
    if (lock.current || !target) return
    lock.current = true
    setRun({action: target, phase: 'working', failure: null})
    try {
      let phase = 'done'
      if (target === 'resume-model') await resumeModel()
      else if (target === 'recover-model') phase = await recoverModel(release)
      else await recoverAccess()
      if (!mounted.current) return
      setRun({action: target, phase, failure: null, update: updates.current})
      if (phase === 'done') settled.current?.()
    } catch (error) {
      if (!mounted.current) return
      setRun({action: target, phase: 'failed', update: updates.current, failure: error instanceof RecoveryFailure ? error
        : new RecoveryFailure('Portal could not finish this step.', 'Try again.')})
    } finally {
      lock.current = false
    }
  }

  const shown = run && run.phase !== 'done' ? run.action : chosen
  // An older API names no access action: keep its link to Portal permissions.
  if (!shown) {
    if (status !== 'recovery') return null
    return <p role="alert" className="shrink-0 border-b border-theme-border px-4 py-2 text-xs text-theme-text-secondary sm:px-6">
      {detail} <Link to="/settings?section=access" className="underline">Portal permissions</Link>
    </p>
  }
  const working = run?.phase === 'working'
  const [label, progress] = COPY[shown]
  const message = HELD_STATES.has(status) && shown === chosen && detail ? detail : MESSAGES[shown]
  return <section aria-label="Portal recovery"
    className="shrink-0 border-b border-theme-border px-4 py-2 text-xs text-theme-text-secondary sm:px-6">
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <p role={status === 'available' ? 'status' : 'alert'} className="min-w-0 flex-1">{message}</p>
      <button type="button" disabled={working} onClick={() => { void start() }}
        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-theme-accent px-3 py-1.5 font-medium text-white transition hover:bg-theme-accent-hover disabled:cursor-wait disabled:opacity-60">
        {working && <Loader2 size={13} className="animate-spin" aria-hidden="true"/>}
        {label}
      </button>
    </div>
    {shown === 'restore-sandbox' && <p className="mt-1">If Full Access was on, this turns it off. You can turn it on again in Portal permissions.</p>}
    {working && <p role="status" className="mt-1">{progress}</p>}
    {run?.phase === 'done' && shown === run.action && <p role="status" className="mt-1">Done. Checking Portal again…</p>}
    {run?.phase === 'releasable' && <div className="mt-2">
      <p>ODS could not confirm the model Portal used before this switch, so it cannot finish the switch the usual way.</p>
      <p className="mt-1">The switch did not change anything, so ODS can release it and put Portal back on the model settings it had before. If that model was not working, choose another model or rerun the installer afterwards.</p>
      <button type="button" className="mt-1 underline" onClick={() => { void start(true) }}>Release without checking</button>
    </div>}
    {run?.phase === 'failed' && <p role="alert" className="mt-1">
      {run.failure.message} {run.failure.next}
      {run.failure.help && <> <HelpLink/></>}
    </p>}
  </section>
}
