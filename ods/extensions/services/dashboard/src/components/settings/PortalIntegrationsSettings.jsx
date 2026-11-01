import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import McpServerCard from './McpServerCard'
import UnsavedIntegrationsNavigation from './UnsavedIntegrationsNavigation'
import { HOME_ASSISTANT, NO_SECRETS, POLICY_LABELS, acknowledgeSecrets, approvalLinkFor, draftFrom, idsForSave, newServerId, rebaseDraft, renameServers, sameDraft, secretsFrom, settingsFrom, typedForDraft } from './portalIntegrationsForm'

const inputStyle = 'w-full rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text focus:outline-none focus:ring-2 focus:ring-theme-accent'
const buttonStyle = 'rounded border border-theme-border px-3 py-2 text-sm hover:bg-white/5 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-theme-accent'
const cardStyle = 'rounded-xl border border-theme-border bg-theme-card p-5 space-y-4'

async function call(action, payload, signal) {
  const response = await fetch('/api/pixel/integrations' + (action ? '/' + action : ''), {
    method: action ? 'POST' : 'GET',
    signal,
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
    <p className="text-xs text-theme-text-muted">An approval allows that exact action once, within 24 hours. Approving here does not resume a finished chat: ask Portal to try the same action again. A scheduled task can use the approval on a later run.</p>
  </section>
}

const ACTIVITY_LABELS = {
  settings: 'Settings', notify: 'Notification', 'notes-search': 'Searched notes', 'notes-read': 'Read a note',
  'notes-write': 'Changed a note', 'mcp-call': 'Used an MCP tool', 'mcp-discover': 'Checked MCP tools', 'mcp-test': 'Tested an MCP server',
  'mcp-restart': 'Restarted an MCP server', approval: 'Approval',
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
  const typedSecrets = draft ? secretsFrom(typedForDraft(typed, draft)) : {}
  const dirty = Boolean(view && draft) && (!sameDraft(draft, draftFrom(view.settings)) || Object.keys(typedSecrets).length > 0)
  const editor = useRef({ view: null, draft: null, typed: NO_SECRETS })
  const requests = useRef({ mounted: false, operation: 0, reads: 0, busy: false, polling: false, controllers: new Set() })

  const show = useCallback((result, submitted) => {
    const previous = editor.current
    if (previous.view && result.settings.revision < previous.view.settings.revision) return false
    const nextDraft = rebaseDraft(submitted?.draft || (previous.view && draftFrom(previous.view.settings)),
      previous.draft, draftFrom(result.settings), submitted?.ids)
    const nextTyped = submitted ? acknowledgeSecrets(previous.typed, submitted.secrets, submitted.ids) : previous.typed
    editor.current = { view: result, draft: nextDraft, typed: nextTyped }
    setView(result)
    setDraft(nextDraft); setTyped(nextTyped)
    return true
  }, [])

  const run = useCallback(async (steps, done) => {
    const state = requests.current
    if (state.busy) return
    state.busy = true; state.reads += 1
    const operation = ++state.operation, controller = new AbortController()
    state.controllers.add(controller)
    const current = () => state.mounted && operation === state.operation
    setBusy(true); setError(''); setNotice('')
    try {
      let result
      for (const [action, payload, submitted] of steps) {
        result = await call(action, payload, controller.signal)
        if (!current()) return
        // Reconcile each successful step, rather than hiding a committed Save
        // when the following discovery/test fails.
        show(result, action === 'save' ? submitted : undefined)
      }
      if (current() && (result.notice || done)) setNotice(result.notice || done)
    } catch (failure) {
      if (current() && failure.name !== 'AbortError') setError(failure.message)
    } finally {
      state.controllers.delete(controller)
      if (current()) { state.busy = false; setBusy(false) }
    }
  }, [show])

  useEffect(() => {
    const state = requests.current
    state.mounted = true
    run([[null]])
    return () => {
      state.mounted = false; state.operation += 1; state.reads += 1; state.busy = false
      for (const controller of state.controllers) controller.abort()
      state.controllers.clear()
    }
  }, [run])
  // New approvals arrive while this page is open; keep the owner's unsaved edits.
  useEffect(() => {
    const timer = setInterval(async () => {
      const state = requests.current
      if (state.busy || state.polling) return
      state.polling = true
      const read = ++state.reads, controller = new AbortController()
      state.controllers.add(controller)
      const current = () => state.mounted && !state.busy && read === state.reads
      try {
        const result = await call(null, undefined, controller.signal)
        if (current()) show(result)
      } catch (failure) {
        if (current() && failure.name !== 'AbortError') setError(failure.message)
      } finally {
        state.controllers.delete(controller); state.polling = false
      }
    }, 15000)
    return () => clearInterval(timer)
  }, [show])
  // Closing/reloading the browser with unsaved changes asks first.
  useEffect(() => {
    if (!dirty) return undefined
    const warn = event => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const editDraft = change => {
    const next = change(editor.current.draft)
    editor.current = { ...editor.current, draft: next }; setDraft(next)
  }
  const editTyped = change => {
    const next = change(editor.current.typed)
    editor.current = { ...editor.current, typed: next }; setTyped(next)
  }

  if (!view || !draft) return <section className={cardStyle}><p className="text-sm text-theme-text-muted" role={error ? 'alert' : 'status'}>{error || 'Loading Portal integrations…'}</p>
    {error && <button className={buttonStyle} onClick={() => run([[null]])}>Reload</button>}</section>

  const ids = idsForSave(draft, new Set(view.settings.mcp.servers.map(server => server.id)))
  const saveStep = () => {
    const saved = renameServers(settingsFrom(draft), typedSecrets, ids)
    return ['save', { expectedRevision: view.settings.revision, ...saved }, { draft, secrets: saved.secrets, ids }]
  }
  const withSave = steps => (dirty ? [saveStep(), ...steps] : steps)
  const save = () => run([saveStep()], 'Saved.')
  const discard = () => { editor.current = { ...editor.current, draft: draftFrom(view.settings), typed: NO_SECRETS }; setDraft(editor.current.draft); setTyped(NO_SECRETS); setError(''); setNotice('Changes discarded.') }
  const setNotifications = patch => editDraft(previous => ({ ...previous, notifications: { ...previous.notifications, ...patch } }))
  const setNotes = patch => editDraft(previous => ({ ...previous, notes: { ...previous.notes, ...patch } }))
  const setServer = (id, patch) => editDraft(previous => ({ ...previous, servers: previous.servers.map(server => {
    if (server.id !== id) return server
    const changedLaunch = ['kind', 'url', 'command', 'workingFolder', 'envNames'].some(key => Object.hasOwn(patch, key) && !sameDraft(server[key], patch[key]))
    return { ...server, ...patch, ...(changedLaunch ? { enabled: false } : {}) }
  }) }))
  const addServer = template => editDraft(previous => ({ ...previous, servers: [...previous.servers, {
    id: newServerId(template.name, previous.servers), enabled: false, url: '', command: '', workingFolder: '', envNames: [], policies: {}, preset: '', kind: 'http', ...template }] }))
  const removeServer = id => editDraft(previous => ({ ...previous, servers: previous.servers.filter(server => server.id !== id) }))
  const savedServer = id => view.settings.mcp.servers.find(server => server.id === id)
  const setToken = (id, value) => {
    editTyped(previous => ({ ...previous, mcp: { ...previous.mcp, [id]: value } }))
    if (value) setServer(id, { enabled: false })
  }
  const setEnvValue = (id, name, value) => {
    editTyped(previous => ({ ...previous, mcpEnv: { ...previous.mcpEnv, [id]: { ...previous.mcpEnv[id], [name]: value } } }))
    if (value) setServer(id, { enabled: false })
  }
  const notifications = draft.notifications
  // Turning notifications on suggests this page for the "Open approvals" button.
  const turnNotifications = enabled => setNotifications(enabled && !notifications.approvalLink
    ? { enabled, approvalLink: approvalLinkFor(window.location.origin) } : { enabled })

  return <div className="space-y-4">
    <UnsavedIntegrationsNavigation dirty={dirty} />
    <section className={cardStyle} aria-labelledby="portal-integrations-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0"><h2 id="portal-integrations-title" className="text-lg font-semibold">Portal integrations</h2>
          <p className="text-sm text-theme-text-muted">Let Portal read your notes, send you notifications and use MCP servers such as Home Assistant. Credentials are stored by the local integration service and used for the services you configure.</p></div>
        <div className="flex gap-2">
          <button className={buttonStyle} disabled={busy} onClick={() => run([[null]])}>Reload</button>
          <button className={buttonStyle} disabled={busy || !dirty} onClick={save}>Save changes</button>
        </div>
      </div>
      {error && <p role="alert" className="text-sm text-red-400 whitespace-pre-wrap break-words">{error}</p>}
      {notice && <p role="status" className="text-sm text-theme-text-muted">{notice}</p>}
      <p role="note" className="text-xs text-theme-text-muted">Off, Ask first and Allowed apply to these integration tools. Full Access also lets Portal use your account’s files and commands, which can reach locally stored settings and secrets. Review <Link to="/settings?section=access" className="underline">Portal access</Link> before adding credentials.</p>
    </section>

    {/* First: an approval ping from ntfy brings the owner here. */}
    <Approvals approvals={view.approvals} busy={busy} onDecide={(approvalId, decision) => run([['decide', { approvalId, decision }]], decision === 'approve' ? 'Approved. Portal can now repeat the action.' : 'Declined.')} />

    <section className={cardStyle} aria-labelledby="integrations-notifications">
      <h3 id="integrations-notifications" className="font-semibold">Notifications (ntfy)</h3>
      <Toggle checked={notifications.enabled} disabled={busy} onChange={turnNotifications}>Send notifications through ntfy</Toggle>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-sm">ntfy address<input className={inputStyle} value={notifications.url} disabled={busy} spellCheck={false} onChange={event => setNotifications({ url: event.target.value })} /></label>
        <label className="text-sm">Topic<input className={inputStyle} value={notifications.topic} disabled={busy} spellCheck={false} onChange={event => setNotifications({ topic: event.target.value })} /></label>
        <label className="text-sm">User (optional)<input className={inputStyle} value={notifications.username} disabled={busy} autoComplete="off" onChange={event => setNotifications({ username: event.target.value })} /></label>
        <label className="text-sm">Password or access token<input className={inputStyle} type="password" autoComplete="off" value={typed.ntfyPassword} disabled={busy}
          placeholder={view.settings.notifications.hasPassword ? 'Saved. Type to replace.' : ''} onChange={event => editTyped(previous => ({ ...previous, ntfyPassword: event.target.value }))} /></label>
      </div>
      <Toggle checked={notifications.notifyApprovals} disabled={busy} onChange={notifyApprovals => setNotifications({ notifyApprovals })}>Notify me when Portal is waiting for approval</Toggle>
      <label className="block text-sm">“Open approvals” button opens (optional)<input className={inputStyle} value={notifications.approvalLink} disabled={busy} spellCheck={false}
        placeholder={approvalLinkFor(window.location.origin)} onChange={event => setNotifications({ approvalLink: event.target.value })} /></label>
      <p className="text-xs text-theme-text-muted">Use an address your phone can open. The button only opens this page; you still sign in to approve.</p>
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
          onToken={token => setToken(server.id, token)}
          onEnvValue={(name, value) => setEnvValue(server.id, name, value)}
          onCheck={() => run(withSave([['discover', { serverId: ids[server.id] }]]), `Checked the tools of ${server.name}. Choose their permissions, then turn the server on and save.`)}
          onTest={() => run(withSave([['test', { serverId: ids[server.id] }]]))}
          onRestart={() => run([['restart', { serverId: server.id }]], `${server.name} was stopped. It starts again when it is next used.`)}
          onRemove={() => removeServer(server.id)} />)}
      </ul>
    </section>

    <Activity entries={view.activity} busy={busy} onClear={() => run([['clear-activity']], 'Activity cleared.')} />

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
