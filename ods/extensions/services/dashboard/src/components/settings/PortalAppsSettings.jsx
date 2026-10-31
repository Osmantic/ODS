import { APPS, browserTimezone } from './portalIntegrationsForm'

const inputStyle = 'w-full rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text focus:outline-none focus:ring-2 focus:ring-theme-accent disabled:opacity-50'
const buttonStyle = 'rounded border border-theme-border px-3 py-2 text-sm hover:bg-white/5 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-theme-accent'

function Toggle({ checked, disabled, onChange, children }) {
  return <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} />{children}</label>
}

function WatchOptions({ app, busy, onChange }) {
  return <>
    <label className="block text-sm md:col-span-2">Adding and removing watched pages
      <select className={inputStyle} value={app.changes} disabled={busy} onChange={event => onChange({ changes: event.target.value })}>
        <option value="ask">Ask first</option>
        <option value="allow">Allowed</option>
      </select>
    </label>
    <p className="text-xs text-theme-text-muted md:col-span-2">You get a notification through ntfy when a watched page changes, or when a product reaches the price you set. Portal looks every 2 minutes.</p>
  </>
}

function NewsOptions({ app, busy, onChange }) {
  const setDigest = patch => onChange({ digest: { ...app.digest, timezone: browserTimezone(), ...patch } })
  return <>
    <div className="space-y-2 md:col-span-2">
      <Toggle checked={app.digest.enabled} disabled={busy} onChange={enabled => setDigest({ enabled })}>Send me a daily news digest</Toggle>
      <label className="block text-sm">Digest time ({app.digest.timezone})
        <input className={inputStyle} type="time" value={app.digest.time} disabled={busy || !app.digest.enabled} onChange={event => setDigest({ time: event.target.value })} /></label>
      <p className="text-xs text-theme-text-muted">Each day at this time Portal summarizes your unread stories and sends the digest through ntfy. It is a scheduled task, kept in step with this setting.</p>
    </div>
  </>
}

function AppCard({ id, app, saved, secret, busy, result, onChange, onSecret, onTest }) {
  const meta = APPS[id]
  return <li className="rounded-lg border border-theme-border p-4 space-y-3" aria-label={meta.title}>
    <div className="min-w-0">
      <h4 className="text-base font-semibold">{meta.title} <span className="text-sm font-normal text-theme-text-muted">· {meta.product}</span></h4>
      <p className="text-sm text-theme-text-muted">{meta.about}</p>
    </div>
    <Toggle checked={app.enabled} disabled={busy} onChange={enabled => onChange({ enabled })}>{meta.toggle}</Toggle>
    <div className="grid gap-3 md:grid-cols-2">
      <label className="text-sm md:col-span-2">{meta.product} address<input className={inputStyle} value={app.url} disabled={busy} spellCheck={false} onChange={event => onChange({ url: event.target.value })} /></label>
      {id === 'news' && <label className="text-sm md:col-span-2">FreshRSS user<input className={inputStyle} value={app.username} disabled={busy} autoComplete="off" spellCheck={false} onChange={event => onChange({ username: event.target.value })} /></label>}
      <label className="text-sm md:col-span-2">{meta.secret}
        <input className={inputStyle} type="password" autoComplete="off" value={secret} disabled={busy}
          placeholder={saved?.hasKey ? 'Saved. Type to replace.' : ''} onChange={event => onSecret(event.target.value)} /></label>
      <p className="text-xs text-theme-text-muted md:col-span-2">{meta.where}</p>
      {id === 'watch' && <WatchOptions app={app} busy={busy} onChange={onChange} />}
      {id === 'news' && <NewsOptions app={app} busy={busy} onChange={onChange} />}
    </div>
    <button className={buttonStyle} disabled={busy || !(secret || saved?.hasKey)} onClick={onTest}>Test {meta.product}</button>
    {result && <p role={result.ok ? 'status' : 'alert'} className={`text-sm break-words ${result.ok ? 'text-theme-text-muted' : 'text-red-400'}`}>{result.message}</p>}
  </li>
}

export default function PortalAppsSettings({ apps, saved, typed, busy, result, onChange, onSecret, onTest }) {
  return <section className="rounded-xl border border-theme-border bg-theme-card p-5 space-y-4" aria-labelledby="integrations-apps">
    <div>
      <h3 id="integrations-apps" className="font-semibold">Your apps</h3>
      <p className="text-sm text-theme-text-muted">Apps running on this computer, such as the ones in the ODS extension catalog. The addresses below are where the catalog installs them.</p>
    </div>
    <ul className="space-y-3">
      {Object.keys(APPS).map(id => <AppCard key={id} id={id} app={apps[id]} saved={saved[id]} secret={typed[id] || ''} busy={busy}
        result={result?.app === id ? result : null}
        onChange={patch => onChange(id, patch)} onSecret={value => onSecret(id, value)} onTest={() => onTest(id)} />)}
    </ul>
  </section>
}
