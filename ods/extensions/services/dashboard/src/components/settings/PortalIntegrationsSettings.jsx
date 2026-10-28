import { useCallback, useEffect, useRef, useState } from 'react'
import McpServerCard from './McpServerCard'
import { HOME_ASSISTANT, POLICY_LABELS, draftFrom, newServerId, sameDraft, secretsFrom, settingsFrom } from './portalIntegrationsForm'

const inputStyle = 'w-full rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text focus:outline-none focus:ring-2 focus:ring-theme-accent'
const buttonStyle = 'rounded border border-theme-border px-3 py-2 text-sm hover:bg-white/5 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-theme-accent'
const cardStyle = 'rounded-xl border border-theme-border bg-theme-card p-5 space-y-4'
const NO_SECRETS = { ntfyPassword: '', mcp: {} }

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
    <p className="text-xs text-theme-text-muted">An approval allows that exact action once. Portal then repeats it.</p>
  </section>
}

const ACTIVITY_LABELS = {
  settings: 'Settings', notify: 'Notification', 'notes-search': 'Searched notes', 'notes-read': 'Read a note',
  'notes-write': 'Changed a note', 'mcp-call': 'Used an MCP tool', 'mcp-discover': 'Checked MCP tools', approval: 'Approval',
}

function Activity({ entries }) {
  return <section className={cardStyle} aria-labelledby="integrations-activity">
    <h3 id="integrations-activity" className="font-semibold">Recent activity</h3>
    {entries.length === 0 ? <p className="text-sm text-theme-text-muted">No activity yet.</p>
      : <ul className="space-y-1 text-sm" aria-label="Integration activity">{entries.slice(0, 20).map((entry, index) => <li key={index} className="break-words">
          <span className="text-theme-text-muted">{when(entry.at)} · </span>{ACTIVITY_LABELS[entry.action] || entry.action}: {entry.target} ({entry.outcome})</li>)}</ul>}
  </section>
}

export default function PortalIntegrationsSettings() {
  const [view, setView] = useState(null)
  const [draft, setDraft] = useState(null)
  const [typed, setTyped] = useState(NO_SECRETS)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const dirty = Boolean(view && draft) && (!sameDraft(draft, draftFrom(view.settings)) || Object.keys(secretsFrom(typed)).length > 0)
  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty

  const show = useCallback((result, { keepDraft = false } = {}) => {
    setView(result)
    if (!keepDraft) { setDraft(draftFrom(result.settings)); setTyped(NO_SECRETS) }
  }, [])

  const run = useCallback(async (steps, done, { keepDraft = false } = {}) => {
    setBusy(true); setError(''); setNotice('')
    try {
      let result
      for (const [action, payload] of steps) result = await call(action, payload)
      show(result, { keepDraft })
      if (done) setNotice(done)
    } catch (failure) {
      setError(failure.message)
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

  if (!view || !draft) return <section className={cardStyle}><p className="text-sm text-theme-text-muted" role={error ? 'alert' : 'status'}>{error || 'Loading Portal integrations…'}</p>
    {error && <button className={buttonStyle} onClick={() => run([[null]])}>Reload</button>}</section>

  const saveStep = () => ['save', { expectedRevision: view.settings.revision, settings: settingsFrom(draft), secrets: secretsFrom(typed) }]
  const withSave = steps => (dirty ? [saveStep(), ...steps] : steps)
  const setNotifications = patch => setDraft(previous => ({ ...previous, notifications: { ...previous.notifications, ...patch } }))
  const setNotes = patch => setDraft(previous => ({ ...previous, notes: { ...previous.notes, ...patch } }))
  const setServer = (id, patch) => setDraft(previous => ({ ...previous, servers: previous.servers.map(server => server.id === id ? { ...server, ...patch } : server) }))
  const addServer = template => setDraft(previous => ({ ...previous, servers: [...previous.servers, {
    id: newServerId(template.name, previous.servers), enabled: false, url: '', command: '', policies: {}, preset: '', kind: 'http', ...template }] }))
  const removeServer = id => setDraft(previous => ({ ...previous, servers: previous.servers.filter(server => server.id !== id) }))
  const savedServer = id => view.settings.mcp.servers.find(server => server.id === id)
  const notifications = draft.notifications

  return <div className="space-y-4">
    <section className={cardStyle} aria-labelledby="portal-integrations-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0"><h2 id="portal-integrations-title" className="text-lg font-semibold">Portal integrations</h2>
          <p className="text-sm text-theme-text-muted">Let Portal read your notes, send you notifications and use MCP servers such as Home Assistant. Passwords and tokens stay on this computer.</p></div>
        <div className="flex gap-2">
          <button className={buttonStyle} disabled={busy} onClick={() => run([[null]])}>Reload</button>
          <button className={buttonStyle} disabled={busy || !dirty} onClick={() => run([saveStep()], 'Saved.')}>Save changes</button>
        </div>
      </div>
      {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
      {notice && <p role="status" className="text-sm text-theme-text-muted">{notice}</p>}
      {dirty && !error && <p className="text-sm text-theme-text-muted">You have unsaved changes.</p>}
    </section>

    {/* First: an approval ping from ntfy brings the owner here. */}
    <Approvals approvals={view.approvals} busy={busy} onDecide={(approvalId, decision) => run([['decide', { approvalId, decision }]], decision === 'approve' ? 'Approved. Portal can now repeat the action.' : 'Declined.', { keepDraft: dirty })} />

    <section className={cardStyle} aria-labelledby="integrations-notifications">
      <h3 id="integrations-notifications" className="font-semibold">Notifications (ntfy)</h3>
      <Toggle checked={notifications.enabled} disabled={busy} onChange={enabled => setNotifications({ enabled })}>Send notifications through ntfy</Toggle>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-sm">ntfy address<input className={inputStyle} value={notifications.url} disabled={busy} spellCheck={false} onChange={event => setNotifications({ url: event.target.value })} /></label>
        <label className="text-sm">Topic<input className={inputStyle} value={notifications.topic} disabled={busy} spellCheck={false} onChange={event => setNotifications({ topic: event.target.value })} /></label>
        <label className="text-sm">User (optional)<input className={inputStyle} value={notifications.username} disabled={busy} autoComplete="off" onChange={event => setNotifications({ username: event.target.value })} /></label>
        <label className="text-sm">Password or access token<input className={inputStyle} type="password" autoComplete="off" value={typed.ntfyPassword} disabled={busy}
          placeholder={view.settings.notifications.hasPassword ? 'Saved. Type to replace.' : ''} onChange={event => setTyped(previous => ({ ...previous, ntfyPassword: event.target.value }))} /></label>
      </div>
      <Toggle checked={notifications.notifyApprovals} disabled={busy} onChange={notifyApprovals => setNotifications({ notifyApprovals })}>Notify me when Portal is waiting for approval</Toggle>
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
        {draft.servers.map(server => <McpServerCard key={server.id} server={server} saved={savedServer(server.id)} busy={busy}
          token={typed.mcp[server.id] || ''}
          onChange={patch => setServer(server.id, patch)}
          onToken={token => setTyped(previous => ({ ...previous, mcp: { ...previous.mcp, [server.id]: token } }))}
          onCheck={() => run(withSave([['discover', { serverId: server.id }]]), `Checked the tools of ${server.name}. Choose their permissions, then turn the server on and save.`)}
          onRemove={() => removeServer(server.id)} />)}
      </ul>
    </section>

    <Activity entries={view.activity} />
  </div>
}
