import { useState } from 'react'
import { Problem } from './GoogleConnectWizard'
import { changePlugin } from './useConnectedApps'

const button = 'rounded-lg bg-white/[0.06] px-3 py-2 hover:bg-white/[0.1] disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-theme-accent'

function Plugin({entry, on, busy, toggle}) {
  const id = `portal-plugin-${entry.id}`
  return <article aria-labelledby={id} className="space-y-2 rounded-lg bg-white/[0.025] p-4">
    <div className="flex items-start justify-between gap-3">
      <h4 id={id} className="font-medium">{entry.label}</h4>
      <span className="rounded-full bg-white/5 px-2 py-0.5 text-xs">{on ? 'On' : 'Off'}</span>
    </div>
    <p className="text-sm">{entry.summary}</p>
    <ul className="list-disc space-y-1 pl-5 text-sm text-theme-text-muted">
      {entry.details.map(line => <li key={line}>{line}</li>)}
    </ul>
    <button type="button" disabled={busy} className={button} onClick={() => toggle(entry, !on)}>
      {on ? 'Turn off' : 'Turn on'}</button>
  </article>
}

// Reviewed OpenClaw plugins the owner can give Portal. Each one starts off; turning
// one on or off restarts Portal's gateway with exactly its reviewed configuration.
export default function PortalPlugins({status, onChanged}) {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)
  const [notice, setNotice] = useState('')
  const catalog = status?.pluginCatalog ?? []
  if (!status?.available || !catalog.length) return null

  async function toggle(entry, enabled) {
    setBusy(true)
    setProblem(null)
    setNotice('')
    try {
      await changePlugin(entry.id, enabled)
      setNotice(enabled ? `${entry.label} is on. Portal can use it now.`
        : `${entry.label} is off. Portal no longer uses it.`)
    } catch (error) {
      setProblem({message: error.message, fix: null, help: Boolean(error.help)})
    } finally {
      setBusy(false)
      onChanged()
    }
  }

  return <section aria-labelledby="portal-plugins-title" className="space-y-3 rounded-xl bg-white/[0.03] p-5">
    <div className="space-y-1">
      <h3 id="portal-plugins-title" className="font-semibold">Portal plugins</h3>
      <p className="text-sm text-theme-text-muted">Extra abilities from OpenClaw, each one reviewed for ODS and set up the same way on every machine. They start off. Turning one on or off restarts Portal, so do it when Portal isn&apos;t busy.</p>
    </div>
    {notice ? <p role="status">{notice}</p> : null}
    {problem ? <Problem problem={problem} /> : null}
    {catalog.map(entry => <Plugin key={entry.id} entry={entry} on={status.plugins?.[entry.id] === true}
      busy={busy} toggle={toggle} />)}
  </section>
}
