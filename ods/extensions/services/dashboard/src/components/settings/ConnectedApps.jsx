import { useState } from 'react'
import HelpLink from '../HelpLink'
import CalendarActivity from './CalendarActivity'
import CalendarChanges from './CalendarChanges'
import GoogleConnectWizard, { Problem } from './GoogleConnectWizard'
import { GOOGLE_CONSOLE, capabilitySummary, consoleLink, googleCard } from './connectedAppsStatus'
import { disconnectGoogle, finishGoogle, savedProject, syncGoogle, useConnectedAppsStatus, useFullAccessOn } from './useConnectedApps'

const TONES = {
  ok: 'border-emerald-400/40 text-emerald-300',
  progress: 'border-theme-accent/40 text-theme-accent-light',
  warn: 'border-amber-400/40 text-amber-200',
  error: 'border-red-400/40 text-red-300',
  neutral: 'border-white/20 text-theme-text-muted',
}
const button = 'rounded-lg border border-white/20 px-3 py-2 disabled:opacity-40'

function when(value) {
  return value ? new Date(value).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'}) : null
}

// Labels for the card's actions; a failed or expired sign-in starts over.
function actionLabel(action, google, step) {
  if (action === 'connect') return step?.kind === 'failed' ? 'Try again' : step ? 'Start again' : 'Connect Google'
  if (action === 'reconnect') return google.state === 'missing-permission' ? 'Grant access'
    : google.state === 'connected' ? 'Change access' : 'Reconnect'
  return {sync: 'Sync now', disconnect: 'Disconnect', finish: 'Finish'}[action]
}

function SourceRows({google}) {
  const rows = [['calendar', 'Calendar', 'calendar.read'], ['email', 'Email', 'gmail.read']]
    .filter(([, , capability]) => google.capabilities?.[capability])
  if (!rows.length) return null
  return <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
    {rows.map(([name, label]) => {
      const source = google.sources?.[name]
      const text = !source ? 'Waiting for the first sync'
        : source.state === 'ok' ? `Up to date${source.lastSuccessAt ? `, ${when(source.lastSuccessAt)}` : ''}`
        : source.lastSuccessAt ? `Last synced ${when(source.lastSuccessAt)}` : 'Not synced yet'
      return [<dt key={`${name}-label`}>{label}</dt>, <dd key={`${name}-value`}>{text}</dd>]
    })}
  </dl>
}

// Settings, Connected apps: one card per connector, Google first.
export default function ConnectedApps() {
  const {status, unavailable, refresh} = useConnectedAppsStatus()
  const fullAccess = useFullAccessOn()
  const [wizard, setWizard] = useState(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)
  const [notice, setNotice] = useState('')
  const [confirming, setConfirming] = useState(false)

  const google = status?.available ? status.connectors.google : null
  const catalog = status?.catalog?.find(entry => entry.id === 'google')
  const card = status ? googleCard(status) : null
  const actions = card ? [...(card.step?.actions ?? []), ...card.actions.filter(action => !(card.step && action === 'connect'))] : []

  async function run(work, done) {
    setBusy(true)
    setProblem(null)
    setNotice('')
    try {
      await work()
      setNotice(done)
    } catch (error) {
      setProblem({message: error.message, fix: null, help: Boolean(error.help)})
    } finally {
      setBusy(false)
      void refresh()
    }
  }

  function act(action) {
    if (action === 'connect') { setNotice(''); setWizard({initial: null}); return }
    if (action === 'reconnect') { setNotice(''); setWizard({initial: {capabilities: google.capabilities, cloudModels: google.cloudModels}}); return }
    if (action === 'disconnect') { setConfirming(true); return }
    if (action === 'sync') void run(syncGoogle, 'Sync started. New calendar events and mail appear within a few minutes.')
    if (action === 'finish') void run(finishGoogle, '')
  }

  function connected(missing) {
    setWizard(null)
    setNotice(missing.length
      ? `Google is connected, but didn't grant: ${missing.join(', ')}. Use Change access to ask again.`
      : 'Google is connected. Portal is turning on your calendar and email.')
    void refresh()
  }

  return <section aria-labelledby="connected-apps-title" className="space-y-4">
    <div className="space-y-1">
      <h2 id="connected-apps-title" className="text-lg font-semibold">Connected apps</h2>
      <p className="text-sm text-theme-text-muted">Let Portal read your calendar and email. ODS keeps the sign-in in its own account on this machine, and Portal sees summaries, not your mailbox.</p>
    </div>
    {!status && !unavailable ? <p role="status">Checking connected apps…</p> : null}
    {unavailable ? <div role="alert" className="space-y-1">
      <p>Connected apps status is unavailable right now.</p>
      <button type="button" onClick={() => { void refresh() }} className="text-sm underline">Try again</button>
    </div> : null}
    {card ? <article aria-labelledby="google-card-title" className="space-y-3 rounded-xl border border-white/10 bg-white/[0.03] p-5">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 id="google-card-title" className="font-semibold">Google</h3>
          <p className="text-sm text-theme-text-muted">Calendar and Gmail</p>
        </div>
        <span className={`rounded-full border px-2 py-0.5 text-xs ${TONES[card.tone]}`}>{card.chip}</span>
      </header>
      {google?.account ? <p>Connected as {google.account}{capabilitySummary(google.capabilities) ? ` · ${capabilitySummary(google.capabilities)}` : ''}</p> : null}
      {google?.account ? <SourceRows google={google} /> : null}
      {card.message ? <p>{card.message}</p> : null}
      {card.fix ? <p>{card.fix}</p> : null}
      {google?.reason === 'api-disabled' ? <p className="text-sm">
        Turn on the <a className="underline" href={consoleLink(GOOGLE_CONSOLE.enableGmail, savedProject())} target="_blank" rel="noopener noreferrer">Gmail API</a> and
        the <a className="underline" href={consoleLink(GOOGLE_CONSOLE.enableCalendar, savedProject())} target="_blank" rel="noopener noreferrer">Google Calendar API</a>.
      </p> : null}
      {google?.reason === 'invalid-grant' ? <p className="text-sm">
        <a className="underline" href={consoleLink(GOOGLE_CONSOLE.audience, savedProject())} target="_blank" rel="noopener noreferrer">Open your app&apos;s Audience page</a>
      </p> : null}
      {card.help ? <HelpLink /> : null}
      {card.step && !wizard ? <div role={card.step.tone === 'progress' ? 'status' : 'alert'} className="space-y-1">
        <p>{card.step.message}</p>
        {card.step.fix ? <p>{card.step.fix}</p> : null}
        {card.step.help ? <HelpLink /> : null}
      </div> : null}
      {fullAccess && google?.account ? <p role="note" className="text-sm">Full Access is on. Connected apps keep working, but Portal now runs commands as your account, and if that account can use Docker, it can act as root on this machine, which includes reading the Google sign-in ODS keeps here and approving its own calendar changes. {google.capabilities?.['calendar.direct'] ? 'Calendar changes Portal would make by itself wait for your approval until then. ' : ''}Switch Portal back to Sandbox in Portal access when you don&apos;t need Full Access.</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {problem ? <Problem problem={problem} /> : null}
      {!wizard && actions.length ? <div className="flex flex-wrap gap-3">
        {actions.map(action => <button key={action} type="button" disabled={busy} onClick={() => act(action)} className={button}>{actionLabel(action, google ?? {}, card.step)}</button>)}
      </div> : null}
      {confirming ? <div role="dialog" aria-labelledby="google-disconnect-title" className="space-y-3 rounded-lg border border-white/10 p-4">
        <h4 id="google-disconnect-title" className="font-semibold">Disconnect Google?</h4>
        <p>ODS stops syncing, removes its access at Google, and deletes the calendar and email summaries it saved on this machine. Portal stops using your calendar and email.</p>
        <div className="flex gap-3">
          <button type="button" disabled={busy} className={button} onClick={() => { setConfirming(false); void run(disconnectGoogle, 'Google is disconnected.') }}>Disconnect</button>
          <button type="button" disabled={busy} onClick={() => setConfirming(false)}>Keep connected</button>
        </div>
      </div> : null}
      {wizard && catalog ? <GoogleConnectWizard catalog={catalog} initial={wizard.initial} status={status} refresh={refresh}
        onClose={() => { setWizard(null); void refresh() }} onConnected={connected} /> : null}
      {google?.account && !wizard ? <CalendarChanges waiting={google.waitingChanges ?? 0}
        writable={Boolean(google.capabilities?.['calendar.write'])} onChanged={() => { void refresh() }} /> : null}
      {google?.account && !wizard && google.capabilities?.['calendar.direct'] ? <CalendarActivity onChanged={() => { void refresh() }} /> : null}
    </article> : null}
  </section>
}
