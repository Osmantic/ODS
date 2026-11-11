import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import McpServerCard from './McpServerCard'
import UnsavedIntegrationsNavigation from './UnsavedIntegrationsNavigation'
import PortalAppsSettings from './PortalAppsSettings'
import PortalMemorySettings from './PortalMemorySettings'
import PortalTelegramSettings from './PortalTelegramSettings'
import { IntegrationMark } from './portalIntegrationLogos'
import { HUB_TILES, editedTiles, tileStatus, viewFrom } from './portalIntegrationsHub'
import { APPS, HOME_ASSISTANT, NO_SECRETS, POLICY_LABELS, TASK_NOTICES, acknowledgeSecrets, approvalLinkFor, credentialsAfterRefresh, draftFrom, idsForSave, newServerId, rebaseDraft, renameServers, sameDraft, secretsFrom, settingsFrom, typedForDraft } from './portalIntegrationsForm'

const APP_IDS = Object.keys(APPS)

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

function ApprovalRequest({ item, busy, onDecide }) {
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const controller = useRef(null)
  useEffect(() => () => controller.current?.abort(), [])
  const review = async () => {
    controller.current?.abort()
    const current = new AbortController()
    controller.current = current
    setLoading(true); setError('')
    try {
      const result = await call('approval-detail', {approvalId: item.id}, current.signal)
      if (current.signal.aborted) return
      if (result.approval?.id !== item.id) throw new Error('This request could not be reviewed. Reload to try again.')
      setDetail(result.approval)
    } catch (failure) {
      if (!current.signal.aborted) setError(failure.message)
    } finally {
      if (!current.signal.aborted) setLoading(false)
    }
  }
  const request = detail?.request
  const reviewable = detail?.available && !detail.redacted && request
  return <li className="space-y-3 py-3 text-sm">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-[12rem] flex-1"><p className="font-medium break-words">{item.summary}</p><p className="text-theme-text-muted">Asked {when(item.createdAt)}</p></div>
      <div className="flex gap-2">
        {!detail && <button className={buttonStyle} disabled={busy || loading} onClick={review}>{loading ? 'Loading request…' : 'Review request'}</button>}
        <button className={buttonStyle} disabled={busy} onClick={() => onDecide(item.id, 'decline')}>Decline</button>
      </div>
    </div>
    {error && <p role="alert" className="text-theme-text-muted">{error}</p>}
    {detail && !detail.available && <p className="text-theme-text-muted">Full action details are unavailable. Decline this request if you cannot verify it.</p>}
    {request && <div className="space-y-2" aria-label="Proposed action">
      {item.kind === 'note' ? <>
        <p>{request.mode === 'create' ? 'Create note' : 'Append to note'}: <span className="font-mono break-all">{request.path}</span></p>
        <p className="text-theme-text-muted">Exact text</p>
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-mono text-sm">{request.text}</pre>
        <p className="text-xs text-theme-text-muted">Portal preserves this text. Appending may add spacing before it; a final newline is added only if missing.</p>
      </> : <>
        <p>Server: <span className="font-mono break-all">{request.server}</span> · Tool: <span className="font-mono break-all">{request.tool}</span></p>
        <p className="text-theme-text-muted">Exact arguments</p>
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-mono text-sm">{JSON.stringify(request.arguments, null, 2)}</pre>
      </>}
      {detail.redacted && <p className="text-theme-text-muted">Configured secrets are hidden. This preview cannot be approved; decline it and submit an action without secret contents.</p>}
    </div>}
    {reviewable && <button className={buttonStyle} disabled={busy} onClick={() => onDecide(item.id, 'approve')}>Approve</button>}
  </li>
}

function Approvals({ approvals, busy, onDecide }) {
  return <section className={cardStyle} aria-labelledby="integrations-approvals">
    <h3 id="integrations-approvals" className="font-semibold">Waiting for your approval</h3>
    {approvals.length === 0
      ? <p className="text-sm text-theme-text-muted">Nothing is waiting.</p>
      : <ul className="space-y-2" aria-label="Waiting approvals">{approvals.map(item => <ApprovalRequest key={item.id} item={item} busy={busy} onDecide={onDecide} />)}</ul>}
    <p className="text-xs text-theme-text-muted">An approval allows that exact action once, within 24 hours. Approving here does not resume a finished chat: ask Portal to try the same action again. A scheduled task can use the approval on a later run.</p>
  </section>
}

const ACTIVITY_LABELS = {
  settings: 'Settings', notify: 'Notification', 'notes-search': 'Searched notes', 'notes-read': 'Read a note',
  'notes-write': 'Changed a note', 'mcp-call': 'Used an MCP tool', 'mcp-discover': 'Checked MCP tools', 'mcp-test': 'Tested an MCP server',
  'mcp-restart': 'Restarted an MCP server', approval: 'Approval', 'app-test': 'Tested an app',
  'documents-search': 'Searched documents', 'documents-read': 'Read a document', 'photos-search': 'Searched photos',
  'news-unread': 'Read the news', 'news-digest': 'Read the news for the digest', 'watch-add': 'Watched a page', 'watch-remove': 'Stopped watching a page',
  'watch-check': 'Checked watched pages', 'reminder-set': 'Set a reminder', reminder: 'Reminder',
  'memory-propose': 'Offered to remember', 'memory-answer': 'Memory answer', 'memory-settings': 'Memory settings',
  'memory-edit': 'Changed a memory', 'memory-delete': 'Deleted a memory', 'memory-clear': 'Cleared memory',
  'memory-decide': 'Memory decision', 'memory-chat': 'Chat memory',
  telegram: 'Telegram', 'telegram-save': 'Telegram settings', 'telegram-test': 'Tested Telegram', 'telegram-message': 'Telegram message',
  'telegram-refused': 'Telegram',
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

const CHIP_STYLES = {
  on: 'bg-emerald-500/15 text-emerald-300',
  off: 'bg-theme-border/60 text-theme-text-muted',
  setup: 'bg-amber-500/15 text-amber-300',
  problem: 'bg-red-500/15 text-red-300',
  waiting: 'bg-theme-accent/15 text-theme-accent',
  info: 'bg-theme-border/60 text-theme-text-muted',
}

function StatusChip({ status }) {
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${CHIP_STYLES[status.tone]}`}>{status.label}</span>
}

function HubTile({ tile, status, edited, onOpen }) {
  return <li className="min-w-0">
    <button type="button" data-tile={tile.id} onClick={() => onOpen(tile.id)}
      aria-label={`${tile.title}, ${tile.about}, ${status.label}${edited ? ', unsaved changes' : ''}`}
      className="group relative flex h-full w-full min-w-0 flex-col items-center gap-2 rounded-xl border border-theme-border bg-theme-bg/40 px-2 pb-3 pt-4 text-center transition hover:border-theme-accent hover:bg-theme-accent/5 focus:outline-none focus:ring-2 focus:ring-theme-accent">
      {edited && <span aria-hidden="true" title="Unsaved changes" className="absolute right-2 top-2 h-2 w-2 rounded-full bg-theme-accent" />}
      <IntegrationMark logo={tile.logo} icon={tile.icon} />
      <span className="w-full text-sm font-semibold leading-tight [hyphens:manual] [overflow-wrap:anywhere]">{tile.title.replace(/^changedetection/, 'change­detection')}</span>
      <span className="line-clamp-2 w-full text-xs leading-snug text-theme-text-muted">{tile.about}</span>
      <span className="mt-auto pt-1"><StatusChip status={status} /></span>
    </button>
  </li>
}

// The integrations at a glance. An approval waiting is the first thing shown: an ntfy ping brings the owner here.
function Hub({ tiles, view, edited, onOpen }) {
  const waiting = view.approvals.length
  return <section className={cardStyle} aria-label="Integrations">
    {waiting > 0 && <div role="note" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-theme-accent bg-theme-accent/10 p-3">
      <p className="text-sm font-medium">{waiting === 1 ? 'Portal is waiting for your approval.' : `Portal is waiting for ${waiting} approvals.`}</p>
      <button type="button" className={buttonStyle} onClick={() => onOpen('approvals')}>Review</button>
    </div>}
    {/* Columns follow the width Settings gives this page (a side panel or the full window), not the screen. */}
    <ul className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(7rem, 1fr))' }} aria-label="Integrations list">
      {tiles.map(tile => <HubTile key={tile.id} tile={tile} status={tileStatus(tile.id, view)} edited={edited.has(tile.id)} onOpen={onOpen} />)}
    </ul>
  </section>
}

function ViewBar({ tile, edited, onBack, focusRef }) {
  return <nav aria-label="Integration" className="flex flex-wrap items-center gap-3">
    <button type="button" ref={focusRef} className={`${buttonStyle} inline-flex items-center gap-1.5`} onClick={onBack}>
      <ArrowLeft aria-hidden="true" className="h-4 w-4" />All integrations
    </button>
    <span className="flex min-w-0 items-center gap-2">
      <IntegrationMark logo={tile.logo} icon={tile.icon} size="h-8 w-8" />
      <span className="truncate font-semibold">{tile.title}</span>
      {edited && <span className="text-xs font-medium text-theme-accent">Unsaved changes</span>}
    </span>
  </nav>
}

export default function PortalIntegrationsSettings() {
  // The open view is in the address (?view=telegram), so a link can open it and Back returns to the hub.
  const [params, setParams] = useSearchParams()
  const current = viewFrom(params.get('view'))
  const viewTop = useRef(null)
  const lastTile = useRef('')
  const openView = useCallback(id => {
    lastTile.current = id
    setParams(previous => { const next = new URLSearchParams(previous); next.set('view', id); return next })
  }, [setParams])
  const closeView = useCallback(() => setParams(previous => { const next = new URLSearchParams(previous); next.delete('view'); return next }), [setParams])
  const [view, setView] = useState(null)
  const [draft, setDraft] = useState(null)
  const [typed, setTyped] = useState(NO_SECRETS)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [credentialCleared, setCredentialCleared] = useState(false)
  const [appResult, setAppResult] = useState(null)
  const typedSecrets = draft ? secretsFrom(typedForDraft(typed, draft)) : {}
  const dirty = Boolean(view && draft) && (!sameDraft(draft, draftFrom(view.settings)) || Object.keys(typedSecrets).length > 0)
  const editor = useRef({ view: null, draft: null, typed: NO_SECRETS })
  const requests = useRef({ mounted: false, operation: 0, reads: 0, busy: false, polling: false, controllers: new Set() })
  // The error a background refresh showed; the next refresh that works ends it.
  const refreshError = useRef('')
  // Telegram keeps its own edits (it saves with its own button); the page marks its tile and
  // asks before leaving while they are unsaved.
  const [telegramEdited, setTelegramEdited] = useState(false)
  const unsaved = dirty || telegramEdited
  const loaded = Boolean(view && draft)
  // Keyboard and screen-reader users land on the view they opened, and back on its tile; a link
  // to a view moves there once the page has loaded.
  useEffect(() => {
    if (current) viewTop.current?.focus()
    else if (lastTile.current) document.querySelector(`[data-tile="${lastTile.current}"]`)?.focus()
  }, [current, loaded])

  const show = useCallback((result, submitted) => {
    const previous = editor.current
    if (previous.view && result.settings.revision < previous.view.settings.revision) return false
    const nextDraft = rebaseDraft(submitted?.draft || (previous.view && draftFrom(previous.view.settings)),
      previous.draft, draftFrom(result.settings), submitted?.ids)
    const pending = submitted ? acknowledgeSecrets(previous.typed, submitted.secrets, submitted.ids) : previous.typed
    const nextTyped = credentialsAfterRefresh(pending, previous.draft, nextDraft, submitted?.ids)
    if (!sameDraft(secretsFrom(pending), secretsFrom(nextTyped))) setCredentialCleared(true)
    else if (submitted) setCredentialCleared(false)
    editor.current = { view: result, draft: nextDraft, typed: nextTyped }
    setView(result)
    setDraft(nextDraft); setTyped(nextTyped)
    return true
  }, [])

  // Return the result as well so app cards can show it beside their button.
  const run = useCallback(async (steps, done, { refreshOnFailure = false } = {}) => {
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
      if (current()) {
        if (result.notice || done) setNotice(result.notice || done)
        return { ok: true, message: result.notice || done || '' }
      }
    } catch (failure) {
      if (current() && failure.name !== 'AbortError') {
        // An uncertain delivery may have created a recovery record. Read its
        // current state without repeating the action or clearing the error.
        if (refreshOnFailure) {
          try {
            const latest = await call(null, undefined, controller.signal)
            if (!current()) return
            show(latest)
          } catch { /* The original delivery error remains the visible result. */ }
        }
        if (!current()) return
        setError(failure.message)
        return { ok: false, message: failure.message }
      }
    } finally {
      state.controllers.delete(controller)
      if (current()) { state.busy = false; setBusy(false) }
    }
  }, [show])

  // A watch-list read never reconciles settings or disturbs unsaved edits.
  const loadWatches = useCallback(async signal => (await call('watch-list', undefined, signal)).watches, [])

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
        const firstLoad = !editor.current.view
        if (current() && show(result)) {
          if (refreshError.current) {
            const failed = refreshError.current
            refreshError.current = ''
            setError(previous => (previous === failed ? '' : previous))
          }
          // The first load failed and this refresh loads the page: that error is over.
          if (firstLoad) setError('')
        }
      } catch (failure) {
        if (current() && failure.name !== 'AbortError') { refreshError.current = failure.message; setError(failure.message) }
      } finally {
        state.controllers.delete(controller); state.polling = false
      }
    }, 15000)
    return () => clearInterval(timer)
  }, [show])
  // Closing/reloading the browser with unsaved changes asks first.
  useEffect(() => {
    if (!unsaved) return undefined
    const warn = event => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [unsaved])

  const editDraft = change => {
    const next = change(editor.current.draft)
    const typed = typedForDraft(editor.current.typed, next)
    editor.current = { ...editor.current, draft: next, typed }
    setDraft(next); setTyped(typed)
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
  // A Test or digest result is about the settings it ran with; saving, discarding or reloading ends it.
  const save = () => { setAppResult(null); return run([saveStep()], 'Saved.') }
  const discard = () => { setAppResult(null); editor.current = { ...editor.current, draft: draftFrom(view.settings), typed: NO_SECRETS }; setDraft(editor.current.draft); setTyped(NO_SECRETS); setError(''); setNotice('Changes discarded.'); setCredentialCleared(false) }
  // A reload keeps the owner's unsaved edits on top of the newest settings (show rebases them).
  const reload = () => { setAppResult(null); return run([[null]], dirty ? 'Reloaded. Your unsaved changes are kept on top of the newest settings.' : '') }
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
  const setApp = (id, patch) => {
    setAppResult(previous => previous?.app === id ? null : previous)
    editDraft(previous => ({ ...previous, apps: { ...previous.apps, [id]: { ...previous.apps[id], ...patch } } }))
  }
  const setAppSecret = (id, value) => {
    setAppResult(previous => previous?.app === id ? null : previous)
    editTyped(previous => ({ ...previous, apps: { ...previous.apps, [id]: value } }))
  }
  const notifications = draft.notifications
  // Turning notifications on suggests this page for the "Open approvals" button.
  const turnNotifications = enabled => setNotifications(enabled && !notifications.approvalLink
    ? { enabled, approvalLink: approvalLinkFor(window.location.origin) } : { enabled })

  const tiles = HUB_TILES.filter(tile => (tile.id === 'telegram' ? Boolean(view.telegram) : tile.id === 'memory' ? Boolean(view.memory) : true))
  // Saved by the bar's Save; typed secrets of a removed server or variable are not saved, so not counted.
  const edited = editedTiles(draft, draftFrom(view.settings), typedForDraft(typed, draft))
  // Also marked on the hub: Telegram, saved with its own button.
  const marked = telegramEdited ? new Set([...edited, 'telegram']) : edited
  const open = tiles.find(tile => tile.id === current)
  // Every view stays mounted, hidden when not open, so typing in one survives a visit to another.
  const pane = id => ({ hidden: !open || open.id !== id })
  const decide = (approvalId, decision) => run([['decide', { approvalId, decision }]], decision === 'approve' ? 'Approved. Portal can now repeat the action.' : 'Declined.')
  const appsProps = {
    apps: draft.apps, saved: view.settings.apps, typed: typed.apps, busy, dirty, onChange: setApp, onSecret: setAppSecret,
    revision: view.settings.revision, notifications: notifications.enabled, onLoadWatches: loadWatches, result: appResult,
    digestRecovery: view.digestRecovery,
    onTest: async app => { setAppResult(null); const result = await run(withSave([['test-app', { app }]])); if (result) setAppResult({ app, ...result }) },
    onSendDigest: async () => { setAppResult(null); const result = await run(withSave([['send-digest']]), undefined, { refreshOnFailure: true }); if (result) setAppResult({ app: 'news', ...result }) },
    onRetryDigest: async attemptId => {
      if (dirty || busy) return
      setAppResult(null)
      const result = await run([['retry-digest', { attemptId, expectedRevision: view.settings.revision }]], undefined, { refreshOnFailure: true })
      if (result) setAppResult({ app: 'news', ...result })
    },
  }

  return <div className="space-y-4">
    <UnsavedIntegrationsNavigation dirty={unsaved} />
    <section className={cardStyle} aria-labelledby="portal-integrations-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0"><h2 id="portal-integrations-title" className="text-lg font-semibold">Portal integrations</h2>
          {!open && <p className="text-sm text-theme-text-muted">Connect Portal to the apps and services you use. Credentials are stored by the local integration service and used for the services you configure.</p>}</div>
        <div className="flex gap-2">
          <button className={buttonStyle} disabled={busy} onClick={reload}>Reload</button>
          <button className={buttonStyle} disabled={busy || !dirty} onClick={save}>Save changes</button>
        </div>
      </div>
      {/* With unsaved changes the error shows in the bar, where the owner pressed Save. */}
      {error && !dirty && <p role="alert" className="text-sm text-red-400 whitespace-pre-wrap break-words">{error}</p>}
      {credentialCleared && <p role="status" className="text-sm text-theme-text-muted">An integration destination changed. Re-enter any cleared credential for the new destination.</p>}
      {notice && <p role="status" className="text-sm text-theme-text-muted">{notice}</p>}
      <p role="note" className="text-xs text-theme-text-muted">Off, Ask first and Allowed apply to these integration tools. Full Access also lets Portal use your account’s files and commands, which can reach locally stored settings and secrets. Review <Link to="/settings?section=access" className="underline">Portal access</Link> before adding credentials.</p>
    </section>

    {!open && <Hub tiles={tiles} view={view} edited={marked} onOpen={openView} />}
    {open && <ViewBar tile={open} edited={marked.has(open.id)} onBack={closeView} focusRef={viewTop} />}

    <div {...pane('approvals')}><Approvals approvals={view.approvals} busy={busy} onDecide={decide} /></div>

    <section className={cardStyle} aria-labelledby="integrations-notifications" {...pane('notifications')}>
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
      <label className="block text-sm">Notify me when a scheduled task
        <select className={inputStyle} value={notifications.scheduledTasks} disabled={busy} onChange={event => setNotifications({ scheduledTasks: event.target.value })}>
          {Object.entries(TASK_NOTICES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <button className={buttonStyle} disabled={busy || !notifications.enabled} onClick={() => run(withSave([['test-notification']]), 'Test notification sent.')}>Send test notification</button>
    </section>

    <section className={cardStyle} aria-labelledby="integrations-notes" {...pane('notes')}>
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

    <section className={cardStyle} aria-labelledby="integrations-mcp" {...pane('mcp')}>
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

    {APP_IDS.map(id => <div key={id} {...pane(id)}><PortalAppsSettings ids={[id]} {...appsProps} /></div>)}

    {view.telegram && <div {...pane('telegram')}><PortalTelegramSettings telegram={view.telegram} busy={busy}
      onAct={steps => run(steps, '')} onEdited={setTelegramEdited} /></div>}

    {view.memory && <div {...pane('memory')}><PortalMemorySettings memory={view.memory} busy={busy} onAct={(steps, done) => run(steps, done)} /></div>}

    <div {...pane('activity')}><Activity entries={view.activity} busy={busy} onClear={() => run([['clear-activity']], 'Activity cleared.')} /></div>

    {/* Always in reach, in the hub and in every view. */}
    {dirty && <div role="region" aria-label="Unsaved changes" className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-theme-accent bg-theme-card p-3 shadow-lg">
      <div className="min-w-0 space-y-1">
        <p className="text-sm">You have unsaved changes{edited.size ? ` in ${tiles.filter(tile => edited.has(tile.id)).map(tile => tile.title).join(', ')}` : ''}.</p>
        {error && <p role="alert" className="text-sm text-red-400 whitespace-pre-wrap break-words">{error}</p>}
      </div>
      <div className="flex gap-2">
        <button className={buttonStyle} disabled={busy} onClick={discard}>Discard</button>
        <button className="rounded bg-theme-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-theme-accent" disabled={busy} onClick={save}>Save</button>
      </div>
    </div>}
  </div>
}
