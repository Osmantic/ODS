import { useCallback, useEffect, useRef, useState } from 'react'

const modeName = mode => mode === 'full-access' ? 'Full Access' : mode === 'sandboxed' ? 'Sandbox' : 'Not verified'
const surfaceName = surface => ({'linux-systemd':'Linux', 'wsl-systemd':'WSL', linux:'Linux', darwin:'macOS', windows:'Windows'})[surface] || 'Unavailable'

export default function PixelAccessCard({ showHeading = true }) {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [changing, setChanging] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [stale, setStale] = useState(true)
  const inspection = useRef(0)
  const pendingInspection = useRef(null)
  const mutation = useRef(false)
  const mounted = useRef(true)
  const refresh = useCallback(async ({forChange = false, preserveError = false, background = false} = {}) => {
    if (mutation.current && !forChange) return
    // Host inspections can take up to 30 seconds. A five-second poll must
    // not supersede a still-running read, including its response body.
    // Explicit refreshes retain their existing latest-request precedence.
    if (background && pendingInspection.current !== null) return
    const version = ++inspection.current
    pendingInspection.current = version
    setStale(true)
    try {
      const response = await fetch('/api/pixel/access-mode')
      if (!response.ok) throw new Error()
      const value = await response.json()
      if (!mounted.current || version !== inspection.current) return
      setStatus(value)
      setStale(false)
      if (!preserveError) setError('')
      return value
    } catch { if (mounted.current && version === inspection.current) setError('Portal permissions could not be checked on the agent runtime. The current mode is unconfirmed. Refresh to check the actual status before requesting another change.') }
    finally { if (pendingInspection.current === version) pendingInspection.current = null }
  }, [])
  useEffect(() => {
    mounted.current = true
    void refresh()
    return () => { mounted.current = false; inspection.current++; pendingInspection.current = null }
  }, [refresh])
  useEffect(() => {
    if (!status?.pending && !status?.busy) return undefined
    const timer = setInterval(() => { void refresh({background: true}) }, 5000)
    return () => clearInterval(timer)
  }, [status?.pending, status?.busy, refresh])

  async function change(mode) {
    if (!status?.revision || stale || mutation.current || (mode === 'full-access' && !confirmed)) return
    mutation.current = true
    setChanging(true); setError('')
    try {
      // Runs can change the inspection revision while Settings remains open.
      // The host still checks this revision atomically before changing access.
      const current = await refresh({forChange: true})
      if (!mounted.current) return
      if (!current?.available || !current?.revision) {
        setError('Current access status could not be verified. No change was requested. Refresh the status before trying again.')
        return
      }
      if (current.busy || (current.pending && mode === 'full-access')) {
        setError('Portal is working or recovering an access transition. No change was requested. Wait for it to finish, or restore Sandbox when available.')
        return
      }
      setStale(true)
      const response = await fetch('/api/pixel/access-mode', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({mode, revision: current.revision, confirmed: mode === 'full-access' && confirmed})})
      if (!response.ok) throw new Error()
      const value = await response.json()
      if (!mounted.current) return
      setStatus(value); setStale(false); setConfirming(false); setConfirmed(false)
    } catch {
      if (mounted.current) {
        setError('The change was not verified. Refresh the status and restore Sandbox if recovery is required.')
        await refresh({forChange: true, preserveError: true})
      }
    } finally { mutation.current = false; if (mounted.current) setChanging(false) }
  }

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
      <dt>Effective</dt><dd>{!stale && status.runtime_verified ? modeName(status.effective_mode) : 'Not verified'}</dd>
      <dt>Agent runtime</dt><dd>{surfaceName(status.surface)}</dd>
    </dl> : !error ? <p role="status">Checking Portal permissions…</p> : null}
    {!status?.available && status ? <p role="status">{status.pending
      ? 'Checking Portal while the access transition is unfinished. Controls return when the running gateway can be verified.'
      : 'The access controller is unavailable on the agent runtime. Install or repair the managed runtime integration before changing permissions.'}</p> : null}
    {status?.busy ? <p role="status">Portal is working. Access changes wait until its runs and tools finish.</p> : null}
    {status?.pending ? <p role="alert">The access transition is unfinished and new work is held. Restore Sandbox if recovery is required.</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    <div className="flex flex-wrap gap-3">
      <button type="button" disabled={disabled} onClick={() => { void change('sandboxed') }} className="rounded-lg border border-white/20 px-3 py-2 disabled:opacity-40">
        {status?.configured_mode === 'sandboxed' && !status?.pending ? 'Verify Sandbox' : 'Restore Sandbox'}
      </button>
      <button type="button" disabled={disabled || status?.pending} onClick={() => { setConfirming(true); setConfirmed(false) }} className="rounded-lg border border-amber-500/50 px-3 py-2 disabled:opacity-40">Enable Full Access</button>
    </div>
    {confirming ? <div role="dialog" aria-labelledby="pixel-access-confirm-title" className="rounded-lg border border-amber-500/50 p-4 space-y-3">
      <h3 id="pixel-access-confirm-title" className="font-semibold">Confirm Full Access</h3>
      <p>Portal can modify or delete files the owner account can access on the agent runtime, including files outside its workspace. Existing operating system restrictions remain. The gateway restarts to verify access; new requests may need to be retried during the change.</p>
      <label className="flex items-start gap-2"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />I understand and authorize Full Access.</label>
      <div className="flex gap-3">
        <button type="button" disabled={disabled || !confirmed} onClick={() => { void change('full-access') }} className="rounded-lg bg-amber-600 px-3 py-2 disabled:opacity-40">Confirm and enable</button>
        <button type="button" disabled={changing} onClick={() => setConfirming(false)}>Cancel</button>
      </div>
    </div> : null}
    {changing ? <p role="status">Changing access and checking the running tools…</p> : null}
  </section>
}
