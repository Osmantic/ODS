import { useCallback, useEffect, useState } from 'react'
import { APPS, browserTimezone } from './portalIntegrationsForm'

const inputStyle = 'w-full rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text focus:outline-none focus:ring-2 focus:ring-theme-accent disabled:opacity-50'
const buttonStyle = 'rounded border border-theme-border px-3 py-2 text-sm hover:bg-white/5 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-theme-accent'

function Toggle({ checked, disabled, onChange, children }) {
  return <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} />{children}</label>
}

const watchDetail = watch => watch.below === null
  ? 'Notifies when the page changes'
  : `Notifies at or under ${watch.below}` + (watch.lastPrice === null ? '' : ` · last seen at ${watch.lastPrice}`)

// What changedetection.io watches now, read when the saved settings allow it.
function WatchList({ ready, revision, onLoad }) {
  const [state, setState] = useState({ loading: false, error: '', list: null })
  const load = useCallback(async () => {
    setState(previous => ({ ...previous, loading: true, error: '' }))
    try {
      setState({ loading: false, error: '', list: await onLoad() })
    } catch (failure) {
      setState({ loading: false, error: failure.message, list: null })
    }
  }, [onLoad])
  useEffect(() => { if (ready) load() }, [ready, revision, load])
  if (!ready) return <p className="text-xs text-theme-text-muted md:col-span-2">Turn this on with its API key and save to see the watched pages here.</p>
  const { loading, error, list } = state
  return <div className="space-y-2 md:col-span-2" aria-label="Watched pages list">
    <div className="flex items-center justify-between gap-3">
      <h5 className="text-sm font-medium">Watched now{list ? ` (${list.total})` : ''}</h5>
      <button className={buttonStyle} disabled={loading} onClick={load}>{loading ? 'Loading…' : 'Refresh'}</button>
    </div>
    {error && <p role="alert" className="text-sm text-red-400 break-words">{error}</p>}
    {list && !list.watches.length && <p className="text-sm text-theme-text-muted">No pages are watched yet. Ask Portal, for example: “Watch this page and tell me when it changes.”</p>}
    {list && list.watches.length > 0 && <ul className="divide-y divide-theme-border rounded border border-theme-border">
      {list.watches.map(watch => <li key={watch.id} className="px-3 py-2 text-sm min-w-0">
        {/^https?:\/\//i.test(watch.url)
          ? <a className="block truncate text-theme-text hover:underline" href={watch.url} target="_blank" rel="noreferrer">{watch.title || watch.url}</a>
          : <span className="block truncate text-theme-text">{watch.title || watch.url}</span>}
        <span className="block text-xs text-theme-text-muted">{watchDetail(watch)}</span>
      </li>)}
    </ul>}
    {list && list.total > list.watches.length && <p className="text-xs text-theme-text-muted">Showing {list.watches.length} of {list.total}. See them all in changedetection.io.</p>}
  </div>
}

function WatchOptions({ app, saved, busy, revision, onChange, onLoadWatches }) {
  return <>
    <label className="block text-sm md:col-span-2">Adding and removing watched pages
      <select className={inputStyle} value={app.changes} disabled={busy} onChange={event => onChange({ changes: event.target.value })}>
        <option value="ask">Ask first</option>
        <option value="allow">Allowed</option>
      </select>
    </label>
    <p className="text-xs text-theme-text-muted md:col-span-2">You get a notification through ntfy when a watched page changes, or when a product reaches the price you set. Portal looks every 2 minutes.</p>
    <WatchList ready={Boolean(saved?.enabled && saved?.hasKey)} revision={revision} onLoad={onLoadWatches} />
  </>
}

function NewsOptions({ app, busy, onChange }) {
  const setDigest = patch => onChange({ digest: { ...app.digest, timezone: browserTimezone(), ...patch } })
  return <>
    <div className="space-y-2 md:col-span-2">
      <Toggle checked={app.digest.enabled} disabled={busy} onChange={enabled => setDigest({ enabled })}>Send me a daily news digest</Toggle>
      <label className="block text-sm">Digest time ({app.digest.timezone})
        <input className={inputStyle} type="time" value={app.digest.time} disabled={busy || !app.digest.enabled} onChange={event => setDigest({ time: event.target.value })} /></label>
      <p className="text-xs text-theme-text-muted">Each day at this time Portal sends a short brief of your new unread stories through ntfy, up to 15 of them, grouped by feed. Each story comes in one digest only. It is a scheduled task, kept in step with this setting.</p>
      {app.digest.enabled && !app.enabled && <p role="note" className="text-xs text-amber-300">The digest starts once you turn on “Let Portal read my news feeds”.</p>}
    </div>
  </>
}

function AppCard({ id, app, saved, secret, busy, result, revision, notifications, onChange, onSecret, onTest, onSendDigest, onLoadWatches }) {
  const hasKey = Boolean(secret || saved?.hasKey)
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
      {id === 'watch' && <WatchOptions app={app} saved={saved} busy={busy} revision={revision} onChange={onChange} onLoadWatches={onLoadWatches} />}
      {id === 'news' && <NewsOptions app={app} busy={busy} onChange={onChange} />}
    </div>
    <div className="flex flex-wrap gap-2">
      <button className={buttonStyle} disabled={busy || !hasKey} onClick={onTest}>Test {meta.product}</button>
      {id === 'news' && <button className={buttonStyle} disabled={busy || !hasKey || !app.enabled || !notifications}
        onClick={onSendDigest}>Send a digest now</button>}
    </div>
    {id === 'news' && !notifications && <p className="text-xs text-theme-text-muted">Turn on notifications through ntfy above to get the digest on your phone.</p>}
    {result && <p role={result.ok ? 'status' : 'alert'} className={`text-sm break-words ${result.ok ? 'text-theme-text-muted' : 'text-red-400'}`}>{result.message}</p>}
  </li>
}

export default function PortalAppsSettings({ apps, saved, typed, busy, result, revision, notifications, onChange, onSecret, onTest, onSendDigest, onLoadWatches }) {
  return <section className="rounded-xl border border-theme-border bg-theme-card p-5 space-y-4" aria-labelledby="integrations-apps">
    <div>
      <h3 id="integrations-apps" className="font-semibold">Your apps</h3>
      <p className="text-sm text-theme-text-muted">Apps running on this computer, such as the ones in the ODS extension catalog. The addresses below are where the catalog installs them.</p>
    </div>
    <ul className="space-y-3">
      {Object.keys(APPS).map(id => <AppCard key={id} id={id} app={apps[id]} saved={saved[id]} secret={typed[id] || ''} busy={busy}
        result={result?.app === id ? result : null} revision={revision} notifications={notifications}
        onChange={patch => onChange(id, patch)} onSecret={value => onSecret(id, value)} onTest={() => onTest(id)}
        onSendDigest={onSendDigest} onLoadWatches={onLoadWatches} />)}
    </ul>
  </section>
}
