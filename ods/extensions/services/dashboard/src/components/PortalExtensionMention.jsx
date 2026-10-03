import { useEffect, useRef, useState } from 'react'
import { Package, RefreshCw } from 'lucide-react'
import './portal-extension-mention.css'

export function extensionMentionQuery(input) {
  return typeof input === 'string' ? /^\s*\/extensions?\s+@([a-z0-9_-]*)$/i.exec(input)?.[1] : undefined
}

function unavailable(entry) {
  if (entry.category === 'core') return 'Managed by ODS'
  if (entry.status === 'incompatible') return 'Not compatible with this host'
  if (['installing', 'setting_up'].includes(entry.status)) return 'Installation in progress'
  // Existing built-ins can be enabled/reused without a downloadable recipe.
  // A user definition alone, however, does not establish an install path.
  if (!['enabled', 'cli_installed', 'disabled', 'stopped'].includes(entry.status) && entry.installable !== true) return 'No installable recipe'
  return null
}

function statusLabel(entry) {
  return ({
    enabled: 'Installed · running', cli_installed: 'Installed',
    disabled: 'Installed · disabled', stopped: 'Installed · stopped',
    unhealthy: 'Installed · needs attention', error: 'Installation needs attention',
  })[entry.status] || 'Available to install'
}

export default function PortalExtensionMention({ query, onSelect, onDismiss }) {
  const [state, setState] = useState({ loading: true, entries: [], error: '' })
  const [revision, setRevision] = useState(0)
  const root = useRef(null)
  const composer = useRef(null)
  useEffect(() => {
    if (document.activeElement?.tagName === 'TEXTAREA') composer.current = document.activeElement
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    // Catalog health checks can take longer than a single service timeout.
    const timeout = setTimeout(() => controller.abort(), 60000)
    let alive = true
    setState(previous => ({ ...previous, loading: true, error: '' }))
    async function load() {
      try {
        const response = await fetch('/api/extensions/catalog', { signal: controller.signal })
        if (!response.ok) throw new Error('catalog-unavailable')
        const data = await response.json()
        if (!Array.isArray(data.extensions)) throw new Error('invalid-catalog')
        const unique = new Map()
        for (const entry of data.extensions) {
          if (typeof entry?.id === 'string' && /^[a-z0-9][a-z0-9_-]{0,63}$/.test(entry.id)) unique.set(entry.id, entry)
        }
        if (alive) setState({ loading: false, entries: [...unique.values()], error: '' })
      } catch {
        if (alive) setState({ loading: false, entries: [], error: 'Could not load the extension catalog.' })
      } finally { clearTimeout(timeout) }
    }
    load()
    return () => { alive = false; clearTimeout(timeout); controller.abort() }
  }, [revision])
  useEffect(() => {
    const navigate = event => {
      if (event.key === 'Escape') { composer.current?.focus(); onDismiss(); return }
      if (event.key !== 'ArrowDown' || event.target?.tagName !== 'TEXTAREA') return
      const first = root.current?.querySelector('button:not(:disabled)')
      if (first) { composer.current = event.target; event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', navigate)
    return () => window.removeEventListener('keydown', navigate)
  }, [onDismiss])
  const needle = query.toLocaleLowerCase()
  const entries = state.entries.filter(entry => `${entry.id} ${entry.name || ''} ${entry.description || ''}`.toLocaleLowerCase().includes(needle))
    .sort((a, b) => Number(b.id === needle) - Number(a.id === needle) || a.id.localeCompare(b.id))
  return <div ref={root} className="portal-extension-mention" role="group" aria-label="Mention an ODS extension" onKeyDown={event => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    const buttons = [...event.currentTarget.querySelectorAll('button:not(:disabled)')]
    if (!buttons.length) return
    event.preventDefault()
    const index = buttons.indexOf(document.activeElement)
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
    buttons[next].focus()
  }}>
    <div className="portal-extension-mention-heading"><strong>Extensions</strong><small>Select, then send to install</small></div>
    {state.loading ? <p role="status">Loading catalog…</p> : state.error ? <div role="status"><p>{state.error}</p><button type="button" onClick={() => setRevision(value => value + 1)}><RefreshCw size={14}/> Retry</button></div>
      : <div className="portal-extension-mention-options">
        {!entries.length && <p role="status">No matching extensions.</p>}
        {entries.map(entry => <button key={entry.id} type="button" disabled={Boolean(unavailable(entry))} onClick={() => onSelect(`/extensions @${entry.id} `)}>
          <Package size={17}/><span><strong>{typeof entry.name === 'string' ? entry.name : entry.id}</strong><small>@{entry.id} · {unavailable(entry) || statusLabel(entry)}</small><small className="portal-extension-mention-description">{typeof entry.description === 'string' ? entry.description : ''}</small></span>
        </button>)}
      </div>}
  </div>
}
