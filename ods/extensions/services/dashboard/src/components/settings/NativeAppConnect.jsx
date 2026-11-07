import { useEffect, useRef, useState } from 'react'
import { Problem } from './GoogleConnectWizard'
import { authorizationLink } from './nativeAppsStatus'
import { reviewedAppAction } from './useReviewedApps'

const focus = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-theme-accent'
const button = `rounded-lg bg-white/[0.06] px-3 py-2 hover:bg-white/[0.1] disabled:opacity-40 ${focus}`
const input = `w-full rounded-lg bg-white/[0.04] px-3 py-2 ${focus}`

export default function NativeAppConnect({app, current, refresh, onClose}) {
  const [flow, setFlow] = useState(null)
  const [address, setAddress] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)
  const attempted = useRef(current?.phase === 'consent')
  const before = useRef(current)
  const consenting = flow !== null || current?.phase === 'consent'

  useEffect(() => {
    if (!attempted.current || busy || !current || current === before.current) return
    if (['authorized', 'claiming', 'activating', 'verifying', 'ready', 'withdrawing', 'forgetting', 'failed', 'cancelled', 'disconnected'].includes(current.phase)) onClose()
  }, [current, busy, onClose])

  async function start() {
    if (busy) return
    setBusy(true)
    setProblem(null)
    attempted.current = true
    before.current = current
    let tab
    try {
      tab = window.open('', '_blank')
      if (tab) tab.opener = null
    } catch { tab = null }
    try {
      const reply = await reviewedAppAction(app.id, 'connect')
      if (reply.phase === 'consent') {
        const url = authorizationLink(reply.authUrl)
        if (!url) throw new Error('ODS could not confirm the sign-in address. Check the connection before trying again.')
        setFlow({...reply, authUrl: url})
        if (tab) tab.location.replace(url)
      } else {
        tab?.close()
        onClose()
      }
      void refresh()
    } catch (error) {
      tab?.close()
      setProblem({message: error.message, help: Boolean(error.help)})
      void refresh()
    } finally { setBusy(false) }
  }

  async function complete(event) {
    event.preventDefault()
    const flowId = flow?.flowId ?? current?.flowId
    if (busy || !flowId || !address.trim()) return
    setBusy(true)
    setProblem(null)
    const pasted = address.trim()
    setAddress('')
    try {
      const reply = await reviewedAppAction(app.id, 'complete', {flowId, address: pasted})
      // Authorization is not a ready result. The card waits for the verified
      // Root status, including a lost activation reply or a provider refusal.
      if (reply.phase === 'authorized') onClose()
      else setProblem({message: 'The sign-in did not finish. Check the connection before trying again.', help: true})
      void refresh()
    } catch (error) {
      setProblem({message: error.message, help: Boolean(error.help)})
      void refresh()
    } finally { setBusy(false) }
  }

  return <section aria-labelledby="reviewed-app-connect-title" className="space-y-3 rounded-lg bg-white/[0.025] p-4">
    <h4 id="reviewed-app-connect-title" className="font-semibold">Connect {app.label}</h4>
    {!consenting ? <>
      <ul className="list-disc space-y-1 pl-5 text-sm">{app.details.map(detail => <li key={detail}>{detail}</li>)}</ul>
      <p className="text-sm text-theme-text-muted">Review the access shown by Notion before allowing it. Its account authorization can be broader than the search and read tools offered to Portal here.</p>
      <button type="button" className={button} disabled={busy} onClick={() => { void start() }}>Continue to {app.label}</button>
    </> : <>
      <p role="status">Finish signing in to {app.label} in the tab opened for this connection. Portal access will be checked afterwards.</p>
      {flow?.authUrl ? <a className={`underline ${focus}`} href={flow.authUrl} target="_blank" rel="noopener noreferrer">Open the sign-in tab</a> : null}
      <form onSubmit={complete} className="space-y-2">
        <label className="block space-y-1">
          <span>On another computer, the last sign-in page may not load. Paste its whole address here.</span>
          <input type="text" inputMode="url" autoComplete="off" spellCheck={false} maxLength={8192} value={address} disabled={busy}
            onChange={event => setAddress(event.target.value)} className={input} />
        </label>
        <button type="submit" className={button} disabled={busy || !address.trim()}>Finish sign-in</button>
      </form>
    </>}
    {problem ? <Problem problem={problem} /> : null}
    <button type="button" onClick={onClose} disabled={busy} className={`text-sm underline ${focus}`}>Back to connection</button>
  </section>
}
