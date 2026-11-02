import { useState } from 'react'
import NativeAppConnect from './NativeAppConnect'
import { Problem } from './GoogleConnectWizard'
import { canStartReviewedApp, reviewedAppView } from './nativeAppsStatus'
import { reviewedAppAction, useReviewedApps } from './useReviewedApps'

const focus = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-theme-accent'
const button = `rounded-lg bg-white/[0.06] px-3 py-2 hover:bg-white/[0.1] disabled:opacity-40 ${focus}`

export default function ReviewedApps() {
  const {status, unavailable, legacy, refresh} = useReviewedApps()
  const [wizard, setWizard] = useState(null)
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(null)
  const [problem, setProblem] = useState(null)

  async function run(app, name, current) {
    setBusy(true)
    setProblem(null)
    try {
      await reviewedAppAction(app.id, name, ['disconnect', 'check'].includes(name) ? {} : {flowId: current.flowId})
    } catch (error) {
      setProblem({message: error.message, help: Boolean(error.help)})
    } finally { setBusy(false); void refresh() }
  }

  if (legacy) return null
  return <section aria-labelledby="reviewed-apps-title" className="space-y-4">
    <div className="space-y-1">
      <h3 id="reviewed-apps-title" className="font-semibold">Apps for Portal</h3>
      <p className="text-sm text-theme-text-muted">Connect an app reviewed for this ODS release. Its permissions are shown before you sign in.</p>
    </div>
    {!status && !unavailable ? <p role="status">Checking available apps…</p> : null}
    {unavailable ? <div role="alert" className="space-y-1">
      <p>App connection status is unavailable right now. ODS has kept the saved state.</p>
      <button type="button" className={`underline ${focus}`} onClick={() => { void refresh() }}>Check apps again</button>
    </div> : null}
    {status?.catalog?.filter(app => app.id === 'notion').map(app => {
      const current = status.apps?.[app.id]
      const view = reviewedAppView(current)
      const start = canStartReviewedApp(current)
      const showWizard = wizard === app.id
      const cancellable = ['consent', 'beginning', 'prepared'].includes(current?.phase)
      const observable = ['ready', 'configured'].includes(current?.phase)
      const removable = observable || current?.flowId && !['absent', 'cancelled', 'disconnected'].includes(current.phase)
      return <article key={app.id} aria-labelledby={`reviewed-${app.id}-title`} className="space-y-3 rounded-xl bg-white/[0.03] p-5">
        <header className="flex items-start justify-between gap-3">
          <div><h4 id={`reviewed-${app.id}-title`} className="font-semibold">{app.label}</h4>
            <p className="text-sm text-theme-text-muted">{app.summary}</p></div>
          <span className="rounded-full bg-white/5 px-2 py-0.5 text-xs text-theme-text-muted">{unavailable ? 'Status unavailable' : status.available ? view.label : 'Not available'}</span>
        </header>
        {!status.available ? <p>This app connection is not available on this system yet.</p> : <>
          {view.message ? <p>{view.message}</p> : null}
          {current?.checkedAt ? <p className="text-sm text-theme-text-muted">Access checked {new Date(current.checkedAt).toLocaleString()}.</p> : null}
          {showWizard ? <NativeAppConnect app={app} current={current} refresh={refresh}
            onClose={() => { setWizard(null); void refresh() }} /> : <div className="flex flex-wrap gap-3">
            {start ? <button type="button" className={button} disabled={busy || unavailable} onClick={() => { setProblem(null); setWizard(app.id) }}>
              {current?.phase === 'prepared' ? 'Continue sign-in' : `Connect ${app.label}`}</button> : null}
            {current?.phase === 'consent' ? <button type="button" className={button} disabled={busy || unavailable}
              onClick={() => { setWizard(app.id) }}>Finish sign-in</button> : null}
            {observable && current.error !== 'native-app-state-changed' ? <button type="button" className={button} disabled={busy || unavailable}
              onClick={() => { void run(app, 'check', current) }}>Check access</button> : null}
            {current?.flowId && !start && !observable ? <button type="button" className={button} disabled={busy || unavailable}
              onClick={() => { void run(app, 'finish', current) }}>Finish</button> : null}
            {cancellable ? <button type="button" className={button} disabled={busy || unavailable}
              onClick={() => { void run(app, 'cancel', current) }}>Cancel sign-in</button> : null}
            {removable ? <button type="button" className={button} disabled={busy || unavailable}
              onClick={() => setConfirming(app.id)}>Disconnect</button> : null}
          </div>}
          {confirming === app.id ? <div role="dialog" aria-labelledby="reviewed-app-disconnect-title" className="space-y-3 rounded-lg bg-white/[0.025] p-4">
            <h4 id="reviewed-app-disconnect-title" className="font-semibold">Disconnect {app.label}?</h4>
            <p>ODS will remove Portal access here and then remove the local sign-in. Authorization at Notion is managed separately.</p>
            <div className="flex gap-3">
              <button type="button" className={button} disabled={busy} onClick={() => { setConfirming(null); void run(app, 'disconnect', current) }}>Disconnect</button>
              <button type="button" className={`rounded-lg px-3 py-2 ${focus}`} disabled={busy} onClick={() => setConfirming(null)}>Keep connection</button>
            </div>
          </div> : null}
        </>}
      </article>
    })}
    {problem ? <Problem problem={problem} /> : null}
  </section>
}
