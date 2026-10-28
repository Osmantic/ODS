import { useEffect, useRef, useState } from 'react'
import HelpLink from '../HelpLink'
import { GOOGLE_CONSOLE, connectionProblem, consoleLink, fileProblem, googleSignInOptions, readClientFile, signInProblem } from './connectedAppsStatus'
import { cancelGoogle, completeGoogle, connectGoogle, saveProject, savedProject } from './useConnectedApps'

const ZONE = /^[A-Za-z][A-Za-z0-9_+\-/]{0,63}$/
const focus = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-theme-accent'
const link = `underline ${focus}`
const input = `w-full rounded-lg bg-white/[0.04] px-3 py-2 ${focus}`
const button = `rounded-lg bg-white/[0.06] px-3 py-2 hover:bg-white/[0.1] disabled:opacity-40 ${focus}`
// What turning a capability on means, where the label alone can't say it.
const NOTES = {
  'calendar.write': 'Portal suggests each change; nothing happens until you approve it in Settings.',
  'calendar.direct': 'Only events without guests, and moving an event to a new time. At most 20 an hour, and paused while Portal has Full Access.',
  'gmail.drafts': 'Portal writes each draft; Gmail gets it only when you approve it in Settings. Portal never sends email: you send the draft from Gmail.',
}

function ownerTimeZone() {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
  return typeof zone === 'string' && ZONE.test(zone) ? zone : null
}

export function Problem({problem}) {
  return <div role="alert" className="space-y-1">
    <p>{problem.message}</p>
    {problem.fix ? <p>{problem.fix}</p> : null}
    {problem.help ? <HelpLink /> : null}
  </div>
}

// Ordinary sign-in uses the deployment's app. The owner's own app is an
// explicit Advanced choice, never a fallback for missing managed configuration.
export default function GoogleConnectWizard({catalog, initial = null, status, refresh, onClose, onConnected}) {
  const reconnect = initial !== null
  const signIn = googleSignInOptions(catalog.auth)
  const [authMode, setAuthMode] = useState('managed')
  const [choices, setChoices] = useState(() => Object.fromEntries(catalog.capabilities.map(capability =>
    [capability.id, capability.available && (initial ? Boolean(initial.capabilities?.[capability.id]) : capability.default)])))
  const [loginHint, setLoginHint] = useState('')
  const [client, setClient] = useState(null)
  const [projectId, setProjectId] = useState(savedProject)
  const [fileError, setFileError] = useState(null)
  const [flow, setFlow] = useState(null)
  const [address, setAddress] = useState('')
  const [problem, setProblem] = useState(null)
  const [busy, setBusy] = useState(false)
  const fileInput = useRef(null)
  const fileRead = useRef(0)
  const waitingFrom = useRef(null)
  const consentSeen = useRef(false)

  // The catalog lists each capability after the ones it needs.
  const requested = {}
  for (const capability of catalog.capabilities) {
    requested[capability.id] = capability.available && Boolean(choices[capability.id])
      && capability.requires.every(need => requested[need])
  }
  const offered = capability => capability.available && capability.requires.every(need => requested[need])
  const anything = Object.values(requested).some(Boolean)
  const labels = Object.fromEntries(catalog.capabilities.map(capability => [capability.id, capability.label]))
  const advanced = authMode === 'advanced'
  const canStart = anything && (advanced ? signIn.advancedAvailable && Boolean(client) : signIn.ready)

  useEffect(() => () => { fileRead.current += 1 }, [])
  useEffect(() => {
    if (authMode === 'advanced' && !signIn.advancedAvailable && !flow) {
      fileRead.current += 1
      setClient(null)
      setFileError(null)
      setLoginHint('')
      setAuthMode('managed')
      if (fileInput.current) fileInput.current.value = ''
    }
  }, [authMode, signIn.advancedAvailable, flow])

  function forgetClient() {
    // Keep an uploaded client only for the explicit Advanced request.
    fileRead.current += 1
    setClient(null)
    if (fileInput.current) fileInput.current.value = ''
  }

  function selectMode(next) {
    forgetClient()
    setFileError(null)
    setProblem(null)
    setLoginHint('')
    setAuthMode(next)
  }

  function backToForm(next) {
    setFlow(null)
    setAddress('')
    waitingFrom.current = null
    consentSeen.current = false
    setProblem(next)
  }

  function connected(missing) {
    onConnected(missing.map(id => labels[id] ?? id))
  }

  // Google's redirect reaches the host agent, which finishes the sign-in; status shows how it went.
  useEffect(() => {
    if (!flow || !status || status === waitingFrom.current) return
    const google = status.connectors?.google
    if (!google) return
    if (google.pending?.phase === 'consent') { consentSeen.current = true; return }
    if (google.pending?.phase === 'failed') { backToForm(signInProblem(google.pending.error, flow.authMode)); return }
    if (google.state !== 'not-connected' || google.pending?.phase === 'activating') {
      connected(Object.keys(requested).filter(id => requested[id] && !google.capabilities?.[id]))
      return
    }
    if (consentSeen.current) backToForm({message: 'The sign-in ended before Google connected.', fix: 'Start again.', help: false})
  }, [status, flow])

  async function chooseFile(event) {
    const file = event.target.files?.[0]
    const reading = ++fileRead.current
    setFileError(null)
    setClient(null)
    if (!file) return
    if (file.size > 16384) { setFileError('client-invalid'); return }
    let result
    try { result = readClientFile(await file.text()) }
    catch { result = {error: 'client-invalid'} }
    if (reading !== fileRead.current) return
    if (result.error) { setFileError(result.error); return }
    setClient(result.client)
    setProjectId(result.projectId)
    saveProject(result.projectId)
  }

  async function start(event) {
    event.preventDefault()
    if (!canStart || busy) return
    setBusy(true)
    setProblem(null)
    // Open the tab while the click still counts as the owner's, then send it to Google.
    let tab
    try {
      tab = window.open('', '_blank')
      if (tab) tab.opener = null
    } catch { tab = null }
    // Portal may use connected data on a cloud model (the owner's decision). The
    // per-connector switch that turns this off ships together with its enforcement.
    const choice = {capabilities: requested, cloudModels: true, ownerTimeZone: ownerTimeZone(),
      loginHint: advanced ? loginHint.trim() || null : null}
    if (advanced) {
      choice.client = client
      // An older host accepts the exact original contract only.
      if (!signIn.legacy) choice.authMode = 'advanced'
    } else choice.authMode = 'managed'
    forgetClient()
    try {
      const reply = await connectGoogle(choice)
      waitingFrom.current = status
      consentSeen.current = false
      setFlow({...reply, opened: Boolean(tab), authMode})
      if (tab) {
        tab.opener = null
        tab.location.replace(reply.authUrl)
      }
      void refresh()
    } catch (error) {
      tab?.close()
      setProblem(connectionProblem(error, authMode))
      void refresh()
    } finally {
      setBusy(false)
    }
  }

  async function paste(event) {
    event.preventDefault()
    if (!flow || !address.trim() || busy) return
    setBusy(true)
    setProblem(null)
    const pasted = address.trim()
    setAddress('')
    try {
      const reply = await completeGoogle(flow.flowId, pasted)
      if (reply.outcome === 'connected') connected(reply.missing)
      else backToForm(signInProblem(reply.errorClass, flow.authMode))
      void refresh()
    } catch (error) {
      // A wrong address leaves the sign-in open; an ended sign-in starts over.
      if (['sign-in-flow-expired', 'sign-in-flow-unknown'].includes(error.code)) backToForm(connectionProblem(error, flow.authMode))
      else setProblem(connectionProblem(error, flow.authMode))
    } finally {
      setBusy(false)
    }
  }

  async function cancel() {
    if (flow) {
      setBusy(true)
      try { await cancelGoogle(flow.flowId) } catch (error) { setProblem(connectionProblem(error, flow.authMode)); setBusy(false); return }
      void refresh()
    }
    onClose()
  }

  return <section aria-labelledby="google-connect-title" className="space-y-4 rounded-lg bg-white/[0.025] p-4">
    <h4 id="google-connect-title" className="font-semibold">{reconnect ? 'Reconnect Google' : 'Connect Google'}</h4>
    {flow ? <div className="space-y-3">
      <p role="status">{flow.opened ? 'Sign in to Google in the tab that opened, and allow access. This page updates by itself.'
        : 'Open the Google sign-in below, choose your account and allow access. This page updates by itself.'}</p>
      <p><a className={link} href={flow.authUrl} target="_blank" rel="noopener noreferrer">Open the Google sign-in again</a></p>
      <form onSubmit={paste} className="space-y-2">
        <label className="block space-y-1">
          <span>On a different computer from the one running ODS, Google&apos;s last page won&apos;t load. Copy its whole address and paste it here.</span>
          <input type="text" inputMode="url" autoComplete="off" spellCheck={false} value={address} disabled={busy}
            onChange={event => setAddress(event.target.value)} className={input} />
        </label>
        <button type="submit" disabled={!address.trim() || busy} className={button}>Finish connecting</button>
      </form>
      {problem ? <Problem problem={problem} /> : null}
      <button type="button" onClick={() => { void cancel() }} disabled={busy} className={`text-sm ${link}`}>Cancel sign-in</button>
    </div> : <form onSubmit={start} className="space-y-4">
      <fieldset className="space-y-2">
        <legend className="font-medium">What Portal may use</legend>
        {catalog.capabilities.map(capability => <label key={capability.id} className="flex items-start gap-2">
          <input type="checkbox" className={focus} checked={requested[capability.id]} disabled={!offered(capability) || busy}
            onChange={event => setChoices(previous => ({...previous, [capability.id]: event.target.checked}))} />
          <span>{capability.label}{capability.available ? '' : ' (coming in a later update)'}
            {capability.available && NOTES[capability.id] ? <span className="block text-sm text-theme-text-muted">{NOTES[capability.id]}</span> : null}</span>
        </label>)}
        <p className="text-sm text-theme-text-muted">Portal reads summaries of your mail and the details of your events, never attachments. When Portal runs on a cloud model, that model&apos;s provider sees what Portal reads.</p>
      </fieldset>
      {!advanced && signIn.ready ? <p className="text-sm text-theme-text-muted">Choose what Portal may use, then sign in and select your account at Google.</p> : null}
      {!advanced && !signIn.ready ? <div role="status" className="space-y-1 text-sm">
        <p>{signIn.state === 'not-configured' ? 'Standard Google sign-in is not available in this ODS installation yet.'
          : signIn.legacy ? 'This ODS version does not provide standard Google sign-in yet.'
          : 'ODS could not load its Google sign-in configuration.'}</p>
        <p>{signIn.state === 'unavailable' && !signIn.legacy ? 'Try again later, or ask for help with this ODS installation.'
          : 'Wait for an ODS update that provides it.'}</p>
      </div> : null}
      {advanced ? <div className="space-y-3">
      <p className="text-sm text-theme-text-muted">Advanced setup uses a Google app you manage. It is optional and requires your own Google Cloud project.</p>
      <details open={!reconnect} className="space-y-2">
        <summary className="cursor-pointer font-medium">Create your Google sign-in app (first time only)</summary>
        <ol className="list-decimal space-y-1 pl-5 text-sm">
          <li><a className={link} href={consoleLink(GOOGLE_CONSOLE.createProject)} target="_blank" rel="noopener noreferrer">Create a Google Cloud project</a>.</li>
          <li>Turn on the <a className={link} href={consoleLink(GOOGLE_CONSOLE.enableGmail, projectId)} target="_blank" rel="noopener noreferrer">Gmail API</a> and
            the <a className={link} href={consoleLink(GOOGLE_CONSOLE.enableCalendar, projectId)} target="_blank" rel="noopener noreferrer">Google Calendar API</a>.</li>
          <li>On the <a className={link} href={consoleLink(GOOGLE_CONSOLE.branding, projectId)} target="_blank" rel="noopener noreferrer">Branding page</a>, give the app a name and your email, and choose External as its audience.</li>
          <li>On the <a className={link} href={consoleLink(GOOGLE_CONSOLE.audience, projectId)} target="_blank" rel="noopener noreferrer">Audience page</a>, press Publish app, or Google ends the sign-in after 7 days.</li>
          <li>On the <a className={link} href={consoleLink(GOOGLE_CONSOLE.clients, projectId)} target="_blank" rel="noopener noreferrer">Clients page</a>, create a client of type Desktop app and download its JSON file.</li>
        </ol>
        <p className="text-sm text-theme-text-muted">Google will say it hasn&apos;t verified the app. That&apos;s expected for your own app: choose Advanced, then continue to your app.</p>
      </details>
      <label className="block space-y-1">
        <span>Google client file</span>
        <input ref={fileInput} type="file" accept=".json,application/json" disabled={busy} onChange={event => { void chooseFile(event) }}
          className={`block w-full text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-white/[0.06] file:px-3 file:py-2 file:text-inherit ${focus}`} />
      </label>
      {fileError ? <Problem problem={fileProblem(fileError)} />
        : client ? <p className="text-sm">Desktop app client for the project {projectId}.</p> : null}
      <label className="block space-y-1">
        <span>Google account to use (optional)</span>
        <input type="email" autoComplete="email" value={loginHint} disabled={busy} onChange={event => setLoginHint(event.target.value)}
          className={input} />
      </label>
      <button type="button" onClick={() => selectMode('managed')} disabled={busy} className={`text-sm ${link}`}>Use standard Google sign-in</button>
      </div> : signIn.advancedAvailable ? <button type="button" onClick={() => selectMode('advanced')} disabled={busy}
        className={`text-sm ${link}`}>Advanced: use your own Google app</button> : null}
      {!anything ? <p className="text-sm">Choose at least one thing Portal may use.</p> : null}
      {problem ? <Problem problem={problem} /> : null}
      <div className="flex flex-wrap gap-3">
        <button type="submit" disabled={!canStart || busy} className={button}>Continue to Google</button>
        <button type="button" onClick={onClose} disabled={busy} className={`rounded-lg px-3 py-2 ${focus}`}>Cancel</button>
      </div>
    </form>}
  </section>
}
