import { useCallback, useEffect, useState } from 'react'

const modeName = mode => mode === 'full-access' ? 'Full Access' : mode === 'sandboxed' ? 'Safer mode' : 'Not verified'

export default function PixelAccessCard() {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [changing, setChanging] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const refresh = useCallback(async () => {
    try {
      const response = await fetch('/api/pixel/access-mode')
      if (!response.ok) throw new Error()
      const value = await response.json()
      setStatus(value)
      return value
    } catch { setError('Pixel access status is unavailable. No effective mode has been verified.') }
  }, [])
  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (!status?.pending && !status?.busy) return undefined
    const timer = setInterval(() => { void refresh() }, 5000)
    return () => clearInterval(timer)
  }, [status?.pending, status?.busy, refresh])

  async function change(mode) {
    if (!status?.revision || changing || (mode === 'full-access' && !confirmed)) return
    setChanging(true); setError('')
    try {
      // Runs can change the inspection revision while Settings remains open.
      // The host still checks this revision atomically before changing access.
      const current = await refresh()
      if (!current?.available || !current?.revision) {
        setError('Current access status could not be verified. No change was requested. Refresh the status before trying again.')
        return
      }
      if (current.busy || (current.pending && mode === 'full-access')) {
        setError('Pixel is working or recovering an access transition. No change was requested. Wait for it to finish, or restore safer mode when available.')
        return
      }
      const response = await fetch('/api/pixel/access-mode', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({mode, revision: current.revision, confirmed: mode === 'full-access' && confirmed})})
      if (!response.ok) throw new Error()
      setStatus(await response.json()); setConfirming(false); setConfirmed(false)
    } catch {
      setError('The change was not verified. Refresh the status and restore safer mode if recovery is required.')
      await refresh()
    } finally { setChanging(false) }
  }

  const disabled = changing || !status?.available || status?.busy || !status?.revision
  return <section aria-labelledby="pixel-access-title" className="rounded-xl border border-white/10 bg-white/[0.03] p-5 space-y-3">
    <div className="flex items-center justify-between gap-4">
      <h2 id="pixel-access-title" className="font-semibold">Pixel access</h2>
      <button type="button" onClick={() => { setError(''); void refresh() }} disabled={changing} className="text-sm underline">Refresh status</button>
    </div>
    <p>Safer mode confines Pixel tools to their configured sandbox. Full Access lets Pixel act with the owner account’s filesystem permissions, including outside the workspace.</p>
    <p className="text-sm text-gray-400">Full Access keeps the owner’s UID and existing group permissions, privilege restrictions, and protected program files. Existing owner permissions may include service administration. Current support: Linux or WSL with systemd.</p>
    {status ? <dl className="grid grid-cols-2 gap-2 text-sm">
      <dt>Configured</dt><dd>{modeName(status.configured_mode)}</dd>
      <dt>Effective</dt><dd>{status.runtime_verified ? modeName(status.effective_mode) : 'Not verified'}</dd>
      <dt>Platform</dt><dd>{status.surface}</dd>
    </dl> : <p role="status">Inspecting Pixel access…</p>}
    {!status?.available && status ? <p role="status">{status.pending
      ? 'Checking Pixel while the access transition is unfinished. Controls return when the running gateway can be verified.'
      : 'The required host adapter or admission gate is unavailable on this installation.'}</p> : null}
    {status?.busy ? <p role="status">Pixel is working. Access changes wait until its runs and tools finish.</p> : null}
    {status?.pending ? <p role="alert">The access transition is unfinished and new work is held. Restore safer mode if recovery is required.</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    <div className="flex flex-wrap gap-3">
      <button type="button" disabled={disabled} onClick={() => { void change('sandboxed') }} className="rounded-lg border border-white/20 px-3 py-2 disabled:opacity-40">
        {status?.configured_mode === 'sandboxed' && !status?.pending ? 'Verify safer mode' : 'Restore safer mode'}
      </button>
      <button type="button" disabled={disabled || status?.pending} onClick={() => { setConfirming(true); setConfirmed(false) }} className="rounded-lg border border-amber-500/50 px-3 py-2 disabled:opacity-40">Enable Full Access</button>
    </div>
    {confirming ? <div role="dialog" aria-labelledby="pixel-access-confirm-title" className="rounded-lg border border-amber-500/50 p-4 space-y-3">
      <h3 id="pixel-access-confirm-title" className="font-semibold">Confirm Full Access</h3>
      <p>Pixel can modify or delete files the owner account can access, including files outside its workspace. The gateway restarts to verify access; new requests may need to be retried during the change.</p>
      <label className="flex items-start gap-2"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />I understand and authorize Full Access.</label>
      <div className="flex gap-3">
        <button type="button" disabled={disabled || !confirmed} onClick={() => { void change('full-access') }} className="rounded-lg bg-amber-600 px-3 py-2 disabled:opacity-40">Confirm and enable</button>
        <button type="button" disabled={changing} onClick={() => setConfirming(false)}>Cancel</button>
      </div>
    </div> : null}
    {changing ? <p role="status">Changing access and checking the running tools…</p> : null}
  </section>
}
