import { useCallback, useEffect, useRef, useState } from 'react'
import { PIXEL_RECOVERY_REQUIRED } from '../../lib/pixelReadiness'

const modeName = mode => mode === 'full-access' ? 'Full Access' : mode === 'sandboxed' ? 'Sandbox' : 'Not verified'
const surfaceName = surface => ({'linux-systemd':'Linux', 'wsl-systemd':'WSL', linux:'Linux', darwin:'macOS', windows:'Windows'})[surface] || 'Unavailable'
const verifiedMode = status => status?.available === true && status.runtime_verified === true && !status.pending && ['sandboxed', 'full-access'].includes(status.effective_mode)
const chatRecovery = status => status?.available === true && status.reason === 'chat-recovery-required'

export default function PixelAccessCard({ showHeading = true, active = true }) {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [changing, setChanging] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [stale, setStale] = useState(true)
  const [visible, setVisible] = useState(document.visibilityState !== 'hidden')
  const [inspectionDone, setInspectionDone] = useState(0)
  const inspection = useRef(0)
  const pendingInspection = useRef(null)
  const inspectionController = useRef(null)
  const recoveryAttempts = useRef(0)
  const mutation = useRef(false)
  const mounted = useRef(false)
  const activation = useRef(0)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const refresh = useCallback(async ({forChange = false, preserveError = false, background = false} = {}) => {
    if (!active || !mounted.current) return
    if (mutation.current && !forChange) return
    if (background && document.visibilityState === 'hidden') return
    // Host inspections can take up to 30 seconds. A five-second poll must
    // not supersede a still-running read, including its response body.
    // Explicit refreshes retain their existing latest-request precedence.
    if (background && pendingInspection.current !== null) return
    const version = ++inspection.current
    inspectionController.current?.abort()
    const controller = new AbortController()
    inspectionController.current = controller
    pendingInspection.current = version
    setStale(true)
    let timer
    try {
      // Include body parsing in the deadline. Superseded requests cannot
      // retain the single-flight slot or publish their eventual response.
      const value = await Promise.race([
        (async () => {
          const response = await fetch('/api/pixel/access-mode', {signal: controller.signal})
          if (!response.ok) throw new Error()
          return response.json()
        })(),
        new Promise((_, reject) => {
          const abort = () => reject(new Error('inspection-aborted'))
          controller.signal.addEventListener('abort', abort, {once: true})
          timer = setTimeout(() => controller.abort(), 45000)
        }),
      ])
      if (version !== inspection.current) return
      setStatus(value)
      setStale(false)
      recoveryAttempts.current = verifiedMode(value) && !chatRecovery(value) ? 0 : Math.min(recoveryAttempts.current + 1, 4)
      if (!preserveError) setError('')
      return value
    } catch {
      if (version === inspection.current) {
        recoveryAttempts.current = Math.min(recoveryAttempts.current + 1, 4)
        setError('Portal permissions could not be checked on the agent runtime. The current mode is unconfirmed. Refresh to check the actual status before requesting another change.')
      }
    } finally {
      clearTimeout(timer)
      if (pendingInspection.current === version) {
        pendingInspection.current = null
        inspectionController.current = null
        setInspectionDone(value => value + 1)
      }
    }
  }, [active])
  useEffect(() => {
    if (!active) return undefined
    activation.current++
    mounted.current = true
    const wake = () => {
      setVisible(document.visibilityState !== 'hidden')
      void refresh({background: true})
    }
    const recoveryRequired = () => {
      // A read started before the hold cannot confirm current permissions.
      // Supersede that read once, without racing an explicit access mutation.
      if (document.visibilityState !== 'hidden') void refresh()
    }
    wake()
    document.addEventListener('visibilitychange', wake)
    window.addEventListener('focus', wake)
    window.addEventListener('online', wake)
    window.addEventListener(PIXEL_RECOVERY_REQUIRED, recoveryRequired)
    return () => {
      activation.current++
      mounted.current = false
      inspection.current++
      pendingInspection.current = null
      inspectionController.current?.abort()
      document.removeEventListener('visibilitychange', wake)
      window.removeEventListener('focus', wake)
      window.removeEventListener('online', wake)
      window.removeEventListener(PIXEL_RECOVERY_REQUIRED, recoveryRequired)
    }
  }, [active, refresh])
  useEffect(() => {
    if (!active || !visible || changing || pendingInspection.current !== null) return undefined
    if (verifiedMode(status) && !status.busy && !chatRecovery(status) && !error) return undefined
    const delay = status?.pending || status?.busy || chatRecovery(status) ? 5000
      : Math.min(5000 * 2 ** Math.max(0, recoveryAttempts.current - 1), 30000)
    const timer = setTimeout(() => { void refresh({background: true}) }, delay)
    return () => clearTimeout(timer)
  }, [active, status, error, changing, visible, inspectionDone, refresh])

  async function change(mode) {
    if (!status?.revision || stale || mutation.current || (mode === 'full-access' && !confirmed)) return
    mutation.current = true
    const activationVersion = activation.current
    setChanging(true); setError('')
    try {
      // Runs can change the inspection revision while Settings remains open.
      // The host still checks this revision atomically before changing access.
      const current = await refresh({forChange: true})
      if (!mounted.current || activationVersion !== activation.current) return
      if (!current?.available || !current?.revision) {
        setError('Current access status could not be verified. No change was requested. Refresh the status before trying again.')
        return
      }
      if (current.busy || ((current.pending || chatRecovery(current)) && mode === 'full-access')) {
        setError('Portal is working or recovering an access transition. No change was requested. Wait for it to finish, or restore Sandbox when available.')
        return
      }
      setStale(true)
      const response = await fetch('/api/pixel/access-mode', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({mode, revision: current.revision, confirmed: mode === 'full-access' && confirmed})})
      if (!response.ok) throw new Error()
      const value = await response.json()
      if (!mounted.current || activationVersion !== activation.current) return
      setStatus(value); setStale(false); setConfirming(false); setConfirmed(false)
    } catch {
      if (!mounted.current) return
      setError('The change was not verified. Refresh the status and restore Sandbox if recovery is required.')
      if (activationVersion === activation.current) await refresh({forChange: true, preserveError: true})
    } finally {
      mutation.current = false
      if (alive.current) setChanging(false)
      // A hidden section can reopen while the request is still running.
      // Its previous receipt must not confirm the newly visible runtime.
      if (mounted.current && activationVersion !== activation.current) {
        await refresh({preserveError: true})
      }
    }
  }

  const modelPending = status?.available === true && status?.pending && status?.reason === 'model-transition-pending'
  const disabled = changing || stale || !status?.available || status?.busy || !status?.revision
  return <section aria-labelledby="pixel-access-title" className="rounded-xl border border-white/10 bg-white/[0.03] p-5 space-y-3">
    <div className="flex items-center justify-between gap-4">
      <h2 id="pixel-access-title" className={showHeading ? 'font-semibold' : 'sr-only'}>Portal permissions</h2>
      <button type="button" onClick={() => { setError(''); void refresh() }} disabled={changing} className="text-sm underline">Refresh status</button>
    </div>
    <p>Sandbox keeps tools inside their configured sandbox. Full Access uses the owner account’s permissions on the host running the agent, including outside its workspace.</p>
    <p className="text-sm text-theme-text-muted">The control follows the agent runtime, even when the Portal runs on a different device. Access switching currently requires Linux or WSL with systemd; native Windows and macOS adapters remain unavailable.</p>
    {status ? <dl className="grid grid-cols-2 gap-2 text-sm">
      <dt>{stale ? 'Last known configuration' : 'Configured'}</dt><dd>{modeName(status.configured_mode)}</dd>
      <dt>Effective</dt><dd>{!stale && verifiedMode(status) ? modeName(status.effective_mode) : 'Not verified'}</dd>
      <dt>Agent runtime</dt><dd>{!stale && status.available === true ? surfaceName(status.surface) : 'Not verified'}</dd>
    </dl> : !error ? <p role="status">Checking Portal permissions…</p> : null}
    {!status?.available && status && !modelPending ? <p role="status">{status.reason === 'managed-installation-incomplete'
      ? 'The Portal installation or update has not completed its runtime verification. Resume the ODS installer on the agent host, then refresh this status. Permission changes remain unavailable until verification completes.'
      : status.pending
      ? 'Checking Portal while the access transition is unfinished. Controls return when the running gateway can be verified.'
      : 'The access controller is unavailable on the agent runtime. Install or repair the managed runtime integration before changing permissions.'}</p> : null}
    {status?.busy ? <p role="status">Portal is working. Access changes wait until its runs and tools finish.</p> : null}
    {chatRecovery(status) ? <p role="alert">Portal stopped during an earlier turn and new messages are held. {status.configured_mode === 'sandboxed' ? 'Verify Sandbox' : 'Restore Sandbox'} to check the runtime before continuing.</p> : null}
    {modelPending ? <p role="status">A model transition is holding new messages. Check model update progress before restoring Sandbox if recovery is required.</p>
      : status?.pending ? <p role="alert">{status.reason === 'model-transition-recovery-required'
        ? 'The model update needs recovery and new work is held. Restore Sandbox if recovery is required.'
        : 'The access transition is unfinished and new work is held. Restore Sandbox if recovery is required.'}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    <div className="flex flex-wrap gap-3">
      <button type="button" disabled={disabled} onClick={() => { void change('sandboxed') }} className="rounded-lg border border-white/20 px-3 py-2 disabled:opacity-40">
        {status?.configured_mode === 'sandboxed' && !status?.pending ? 'Verify Sandbox' : 'Restore Sandbox'}
      </button>
      <button type="button" disabled={disabled || status?.pending || chatRecovery(status)} onClick={() => { setConfirming(true); setConfirmed(false) }} className="rounded-lg border border-theme-border px-3 py-2 disabled:opacity-40">Enable Full Access</button>
    </div>
    {confirming ? <div role="dialog" aria-labelledby="pixel-access-confirm-title" className="rounded-lg border border-theme-border p-4 space-y-3">
      <h3 id="pixel-access-confirm-title" className="font-semibold">Confirm Full Access</h3>
      <p>Full Access turns off Portal’s sandbox and per-command approval. Portal then runs commands directly as the owner account on the agent runtime and can read, change or delete any file that account can, including outside its workspace.</p>
      <p>Web search and page fetching stay on, so text from the web can influence those commands. If the owner account can use Docker (the docker group), that is equivalent to root on that machine.</p>
      <p>The gateway restarts to verify access; new requests may need to be retried during the change.</p>
      <label className="flex items-start gap-2"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />I understand and authorize Full Access.</label>
      <div className="flex gap-3">
        <button type="button" disabled={disabled || !confirmed} onClick={() => { void change('full-access') }} className="rounded-lg border border-theme-border bg-theme-surface-hover text-theme-text px-3 py-2 disabled:opacity-40">Confirm and enable</button>
        <button type="button" disabled={changing} onClick={() => setConfirming(false)}>Cancel</button>
      </div>
    </div> : null}
    {changing ? <p role="status">Changing access and checking the running tools…</p> : null}
  </section>
}
