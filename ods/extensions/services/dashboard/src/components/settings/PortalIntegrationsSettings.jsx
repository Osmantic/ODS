import { useCallback, useEffect, useRef, useState } from 'react'
import McpServerCard from './McpServerCard'
import PortalAppsSettings from './PortalAppsSettings'
import { HOME_ASSISTANT, NO_SECRETS, POLICY_LABELS, TASK_NOTICES, approvalLinkFor, draftFrom, idsForSave, newServerId, renameServers, sameDraft, secretsFrom, settingsFrom, typedForDraft } from './portalIntegrationsForm'

const inputStyle = 'w-full rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text focus:outline-none focus:ring-2 focus:ring-theme-accent'
const buttonStyle = 'rounded border border-theme-border px-3 py-2 text-sm hover:bg-white/5 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-theme-accent'
const cardStyle = 'rounded-xl border border-theme-border bg-theme-card p-5 space-y-4'

async function call(action, payload) {
  const response = await fetch('/api/pixel/integrations' + (action ? '/' + action : ''), {
    method: action ? 'POST' : 'GET',
    ...(action ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) } : {}),
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Portal integrations are unavailable. Reload to try again.')
  return body
}

const when = seconds => new Date(seconds * 1000).toLocaleString()

function Toggle({ checked, disabled, onChange, children }) {
  return <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} />{children}</label>
}

function Approvals({ approvals, busy, onDecide }) {
  return <section className={cardStyle} aria-labelledby="integrations-approvals">
    <h3 id="integrations-approvals" className="font-semibold">Waiting for your approval</h3>
    {approvals.length === 0
      ? <p className="text-sm text-theme-text-muted">Nothing is waiting.</p>
      : <ul className="space-y-2" aria-label="Waiting approvals">{approvals.map(item => <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded border border-theme-border p-3 text-sm">
          <div className="min-w-[12rem] flex-1"><p className="font-medium break-words">{item.summary}</p><p className="text-theme-text-muted">Asked {when(item.createdAt)}</p></div>
          <div className="flex gap-2">
            <button className={buttonStyle} disabled={busy} onClick={() => onDecide(item.id, 'approve')}>Approve</button>
            <button className={buttonStyle} disabled={busy} onClick={() => onDecide(item.id, 'decline')}>Decline</button>
          </div>
        </li>)}</ul>}
    <p className="text-xs text-theme-text-muted">An approval allows that exact action once, within 24 hours. Portal then repeats it; a scheduled task goes ahead on its next run.</p>
  </section>
}

const ACTIVITY_LABELS = {
  settings: 'Settings', notify: 'Notification', 'notes-search': 'Searched notes', 'notes-read': 'Read a note',
  'notes-write': 'Changed a note', 'mcp-call': 'Used an MCP tool', 'mcp-discover': 'Checked MCP tools', 'mcp-test': 'Tested an MCP server',
  'mcp-restart': 'Restarted an MCP server', approval: 'Approval', 'app-test': 'Tested an app',
  'documents-search': 'Searched documents', 'documents-read': 'Read a document', 'photos-search': 'Searched photos',
  'news-digest': 'Read the news', 'watch-add': 'Watched a page', 'watch-remove': 'Stopped watching a page',
  'watch-check': 'Checked watched pages',
}

function Activity({ entries, busy, onClear }) {
  return <section className={cardStyle} aria-labelledby="integrations-activity">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h3 id="integrations-activity" className="font-semibold">Recent activity</h3>
      <button className={buttonStyle} disabled={busy || entries.length === 0} onClick={onClear}>Clear activity</button>
    </div>
    {entries.length === 0 ? <p className="text-sm text-theme-text-muted">No activity yet.</p>
      : <ul className="space-y-1 text-sm" aria-label="Integration activity">{entries.slice(0, 20).map((entry, index) => <li key={index} className="break-words">
          <span className="text-theme-text-muted">{when(entry.at)} · </span>{ACTIVITY_LABELS[entry.action] || entry.action}: {entry.target} ({entry.outcome})</li>)}</ul>}
    <p className="text-xs text-theme-text-muted">The latest 500 entries are kept.</p>
  </section>
}

export default function PortalIntegrationsSettings() {
  const [view, setView] = useState(null)
  const [draft, setDraft] = useState(null)
  const [typed, setTyped] = useState(NO_SECRETS)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [appResult, setAppResult] = useState(null)
  const typedSecrets = draft ? secretsFrom(typedForDraft(typed, draft)) : {}
  const dirty = Boolean(view && draft) && (!sameDraft(draft, draftFrom(view.settings)) || Object.keys(typedSecrets).length > 0)
  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty

  const show = useCallback((result, { keepDraft = false } = {}) => {
    setView(result)
    if (!keepDraft) { setDraft(draftFrom(result.settings)); setTyped(NO_SECRETS) }
  }, [])

  // What happened, also returned so a card far down the page can show it next to its button.
  const run = useCallback(async (steps, done, { keepDraft = false } = {}) => {
    setBusy(true); setError(''); setNotice('')
    let result
    try {
      for (const [action, payload] of steps) result = await call(action, payload)
      show(result, { keepDraft })
      if (result.notice || done) setNotice(result.notice || done)
      return { ok: true, message: result.notice || done || '' }
    } catch (failure) {
      // A save before the failed step went through; show it, or the page would still look unsaved.
      if (result) show(result, { keepDraft })
      setError(failure.message)
      return { ok: false, message: failure.message }
    } finally {
      setBusy(false)
    }
  }, [show])

  useEffect(() => { run([[null]]) }, [run])
  // New approvals arrive while this page is open; keep the owner's unsaved edits.
  useEffect(() => {
    const timer = setInterval(() => call(null).then(result => show(result, { keepDraft: dirtyRef.current }))
      .catch(failure => setError(failure.message)), 15000)
    return () => clearInterval(timer)
  }, [show])
  // Leaving the page with unsaved changes asks first.
  useEffect(() => {
    if (!dirty) return undefined
    const warn = event => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  if (!view || !draft) return <section className={cardStyle}><p className="text-sm text-theme-text-muted" role={error ? 'alert' : 'status'}>{error || 'Loading Portal integrations…'}</p>
    {error && <button className={buttonStyle} onClick={() => run([[null]])}>Reload</button>}</section>

  const ids = idsForSave(draft, new Set(view.settings.mcp.servers.map(server => server.id)))
  const saveStep = () => ['save', { expectedRevision: view.settings.revision, ...renameServers(settingsFrom(draft), typedSecrets, ids) }]
  const withSave = steps => (dirty ? [saveStep(), ...steps] : steps)
  const save = () => run([saveStep()], 'Saved.')
  const discard = () => { setDraft(draftFrom(view.settings)); setTyped(NO_SECRETS); setError(''); setNotice('Changes discarded.') }
  const setNotifications = patch => setDraft(previous => ({ ...previous, notifications: { ...previous.notifications, ...patch } }))
  const setNotes = patch => setDraft(previous => ({ ...previous, notes: { ...previous.notes, ...patch } }))
  const setServer = (id, patch) => setDraft(previous => ({ ...previous, servers: previous.servers.map(server => server.id === id ? { ...server, ...patch } : server) }))
  const addServer = template => setDraft(previous => ({ ...previous, servers: [...previous.servers, {
    id: newServerId(template.name, previous.servers), enabled: false, url: '', command: '', workingFolder: '', envNames: [], policies: {}, preset: '', kind: 'http', ...template }] }))
  const removeServer = id => setDraft(previous => ({ ...previous, servers: previous.servers.filter(server => server.id !== id) }))
  const savedServer = id => view.settings.mcp.servers.find(server => server.id === id)
  const setApp = (id, patch) => setDraft(previous => ({ ...previous, apps: { ...previous.apps, [id]: { ...previous.apps[id], ...patch } } }))
  const setAppSecret = (id, value) => setTyped(previous => ({ ...previous, apps: { ...previous.apps, [id]: value } }))
  const setEnvValue = (id, name, value) => setTyped(previous => ({ ...previous, mcpEnv: { ...previous.mcpEnv, [id]: { ...previous.mcpEnv[id], [name]: value } } }))
  const notifications = draft.notifications
  // Turning notifications on suggests this page for the "Open approvals" button.
  const turnNotifications = enabled => setNotifications(enabled && !notifications.approvalLink
    ? { enabled, approvalLink: approvalLinkFor(window.location.origin) } : { enabled })

  return <div className="space-y-4">
    <section className={cardStyle} aria-labelledby="portal-integrations-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0"><h2 id="portal-integrations-title" className="text-lg font-semibold">Portal integrations</h2>
          <p className="text-sm text-theme-text-muted">Let Portal read your notes, send you notifications, use MCP servers such as Home Assistant, and work with your documents, photos, watched pages and news. Passwords and tokens stay on this computer.</p></div>
        <div className="flex gap-2">
          <button className={buttonStyle} disabled={busy} onClick={() => run([[null]], '', { keepDraft: dirty })}>Reload</button>
          <button className={buttonStyle} disabled={busy || !dirty} onClick={save}>Save changes</button>
        </div>
      </div>
      {error && <p role="alert" className="text-sm text-red-400 whitespace-pre-wrap break-words">{error}</p>}
      {notice && <p role="status" className="text-sm text-theme-text-muted">{notice}</p>}
    </section>

    {/* First: an approval ping from ntfy brings the owner here. */}
    <Approvals approvals={view.approvals} busy={busy} onDecide={(approvalId, decision) => run([['decide', { approvalId, decision }]], decision === 'approve' ? 'Approved. Portal can now repeat the action.' : 'Declined.', { keepDraft: dirty })} />

    <section className={cardStyle} aria-labelledby="integrations-notifications">
      <h3 id="integrations-notifications" className="font-semibold">Notifications (ntfy)</h3>
      <Toggle checked={notifications.enabled} disabled={busy} onChange={turnNotifications}>Send notifications through ntfy</Toggle>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-sm">ntfy address<input className={inputStyle} value={notifications.url} disabled={busy} spellCheck={false} onChange={event => setNotifications({ url: event.target.value })} /></label>
        <label className="text-sm">Topic<input className={inputStyle} value={notifications.topic} disabled={busy} spellCheck={false} onChange={event => setNotifications({ topic: event.target.value })} /></label>
        <label className="text-sm">User (optional)<input className={inputStyle} value={notifications.username} disabled={busy} autoComplete="off" onChange={event => setNotifications({ username: event.target.value })} /></label>
        <label className="text-sm">Password or access token<input className={inputStyle} type="password" autoComplete="off" value={typed.ntfyPassword} disabled={busy}
          placeholder={view.settings.notifications.hasPassword ? 'Saved. Type to replace.' : ''} onChange={event => setTyped(previous => ({ ...previous, ntfyPassword: event.target.value }))} /></label>
      </div>
      <Toggle checked={notifications.notifyApprovals} disabled={busy} onChange={notifyApprovals => setNotifications({ notifyApprovals })}>Notify me when Portal is waiting for approval</Toggle>
      <label className="block text-sm">“Open approvals” button opens (optional)<input className={inputStyle} value={notifications.approvalLink} disabled={busy} spellCheck={false}
        placeholder={approvalLinkFor(window.location.origin)} onChange={event => setNotifications({ approvalLink: event.target.value })} /></label>
      <p className="text-xs text-theme-text-muted">Use an address your phone can open. The button only opens this page; you still sign in to approve.</p>
      <label className="block text-sm">Notify me when a scheduled task
        <select className={inputStyle} value={notifications.scheduledTasks} disabled={busy} onChange={event => setNotifications({ scheduledTasks: event.target.value })}>
          {Object.entries(TASK_NOTICES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <button className={buttonStyle} disabled={busy || !notifications.enabled} onClick={() => run(withSave([['test-notification']]), 'Test notification sent.')}>Send test notification</button>
    </section>

    <section className={cardStyle} aria-labelledby="integrations-notes">
      <h3 id="integrations-notes" className="font-semibold">Notes (Obsidian vault)</h3>
      <Toggle checked={draft.notes.enabled} disabled={busy} onChange={enabled => setNotes({ enabled })}>Let Portal search and read my notes</Toggle>
      <label className="block text-sm">Vault folder (full path)<input className={inputStyle} value={draft.notes.vaultPath} disabled={busy} spellCheck={false} placeholder="/home/you/Documents/Obsidian Vault" onChange={event => setNotes({ vaultPath: event.target.value })} /></label>
      <label className="block text-sm">Adding to notes
        <select className={inputStyle} value={draft.notes.write} disabled={busy} onChange={event => setNotes({ write: event.target.value })}>
          {Object.entries(POLICY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <p className="text-xs text-theme-text-muted">Portal never deletes or overwrites a note. It can add to the end of a note or create a new one. Hidden folders such as .obsidian are skipped.</p>
    </section>

    <section className={cardStyle} aria-labelledby="integrations-mcp">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 id="integrations-mcp" className="font-semibold">MCP servers</h3>
        <div className="flex flex-wrap gap-2">
          <button className={buttonStyle} disabled={busy} onClick={() => addServer(HOME_ASSISTANT)}>Add Home Assistant</button>
          <button className={buttonStyle} disabled={busy} onClick={() => addServer({ name: 'MCP server' })}>Add MCP server</button>
        </div>
      </div>
      {draft.servers.length === 0 && <p className="text-sm text-theme-text-muted">No MCP servers yet.</p>}
      <ul className="space-y-3">
        {draft.servers.map(server => <McpServerCard key={server.id} server={server} saved={savedServer(server.id)} process={view.processes?.[server.id]} busy={busy}
          token={typed.mcp[server.id] || ''} envValues={typed.mcpEnv[server.id] || {}}
          onChange={patch => setServer(server.id, patch)}
          onToken={token => setTyped(previous => ({ ...previous, mcp: { ...previous.mcp, [server.id]: token } }))}
          onEnvValue={(name, value) => setEnvValue(server.id, name, value)}
          onCheck={() => run(withSave([['discover', { serverId: ids[server.id] }]]), `Checked the tools of ${server.name}. Choose their permissions, then turn the server on and save.`)}
          onTest={() => run(withSave([['test', { serverId: ids[server.id] }]]))}
          onRestart={() => run([['restart', { serverId: server.id }]], `${server.name} was stopped. It starts again when it is next used.`, { keepDraft: dirty })}
          onRemove={() => removeServer(server.id)} />)}
      </ul>
    </section>

    <PortalAppsSettings apps={draft.apps} saved={view.settings.apps} typed={typed.apps} busy={busy} onChange={setApp} onSecret={setAppSecret}
      result={appResult} onTest={async app => { setAppResult(null); setAppResult({ app, ...await run(withSave([['test-app', { app }]])) }) }} />

    <Activity entries={view.activity} busy={busy} onClear={() => run([['clear-activity']], 'Activity cleared.', { keepDraft: dirty })} />

    {/* Always in reach, wherever the change was made on this long page. */}
    {dirty && <div role="region" aria-label="Unsaved changes" className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-theme-accent bg-theme-card p-3 shadow-lg">
      <p className="text-sm">You have unsaved changes.</p>
      <div className="flex gap-2">
        <button className={buttonStyle} disabled={busy} onClick={discard}>Discard</button>
        <button className="rounded bg-theme-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-theme-accent" disabled={busy} onClick={save}>Save</button>
      </div>
    </div>}
  </div>
}
