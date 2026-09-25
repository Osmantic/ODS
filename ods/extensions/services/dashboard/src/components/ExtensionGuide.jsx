// "How to use" for one extension: what it is for, how to open it, how to sign
// in and where its documentation lives. Everything comes from the extension's
// own definition through GET /api/extensions/{id}: its manifest (guide), the
// recipe README (integration.documentation) and upstream.json (docsUrl).
// Settings are reported by presence only; values are never sent here, and the
// owner reads them on the host with the command shown.

import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { BookOpen, Check, Copy, ExternalLink, Loader2, Pin, PinOff, X } from 'lucide-react'
import { fallbackServiceUrl, serviceUrl } from '../lib/serviceUrls'
import { isPinned, setPinned } from '../lib/applicationPins'

const ROLE_ORDER = { sign_in: 0, api_key: 1, setting: 2, internal: 3 }
const ROLE_LABELS = { sign_in: 'Sign-in', api_key: 'API key', setting: 'Setting', internal: 'Used by the service' }

const markdownComponents = {
  // Only https links leave the dashboard; examples such as localhost URLs
  // stay readable text. Images never load remote content from a README.
  a: ({ children, href }) => (/^https:\/\//.test(href || '')
    ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
    : <span title={href}>{children}</span>),
  img: ({ alt }) => <span>[Image: {alt || 'untitled'}]</span>,
}

// The same host command the Details view shows: values print on the host,
// never in the dashboard.
export function credentialsCommand(keys) {
  return keys.length ? `grep -E '^[[:space:]]*(export[[:space:]]+)?(${keys.join('|')})[[:space:]]*=' .env` : ''
}

// Which page an extension offers: its manifest's declaration from the API,
// with the dashboard's built-in API-only services kept as they were.
export function extensionKind(ext, headless) {
  if (headless) return 'api'
  return ['web', 'api', 'none'].includes(ext?.usage_kind) ? ext.usage_kind : 'web'
}

// The clipboard API exists only in secure contexts (https, localhost); on a
// plain-http LAN address the text stays selectable without a copy button.
function CopyLine({ value, label }) {
  const [copied, setCopied] = useState(false)
  const clipboard = globalThis.navigator?.clipboard
  const copy = () => clipboard.writeText(value)
    .then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) })
    .catch(() => {})
  return (
    <div className="extension-guide-copy">
      <code>{value}</code>
      {clipboard && (
        <button type="button" onClick={copy} aria-label={`Copy ${label}`} title="Copy">
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </button>
      )}
    </div>
  )
}

function Section({ title, children }) {
  return <section className="extension-guide-section"><h4>{title}</h4>{children}</section>
}

function OpenSection({ ext, kind, url, running, guide, listed }) {
  const name = ext.name
  if (kind === 'web') {
    if (!url) return <p>{name} has a web page, but ODS does not publish it on a port of this computer.</p>
    return <>
      <p>{running ? `Open ${name} in a new tab:` : `Start ${name} first. When it is running it opens at:`}</p>
      <CopyLine value={url} label="address" />
      {listed && <p className="extension-guide-muted">It is also listed under Applications in the sidebar.</p>}
    </>
  }
  if (kind === 'api') {
    const hostUrl = guide?.hostPort ? fallbackServiceUrl(guide.hostPort) : null
    return <>
      <p>{name} has no page to open. It is a service that other apps call.</p>
      {guide?.internalUrl && <><p className="extension-guide-muted">From other ODS services:</p><CopyLine value={guide.internalUrl} label="address for ODS services" /></>}
      {hostUrl && <><p className="extension-guide-muted">From this computer:</p><CopyLine value={hostUrl} label="address on this computer" /></>}
      <p className="extension-guide-muted">
        To have Portal use it for you, start a message with <code>/extensions @{ext.id}</code> and describe the task.
      </p>
    </>
  }
  return ext.status === 'cli_installed'
    ? <><p>{name} is a command-line tool. Run it on the host from your ODS folder:</p><CopyLine value={`docker compose run --rm ${ext.id}`} label="command" /></>
    : <p>{name} runs in the background and has no page or network address to open.</p>
}

function SignInSection({ ext, kind, settings }) {
  const listed = settings.filter(item => item.secret || item.role === 'sign_in' || item.role === 'api_key')
    .sort((a, b) => ROLE_ORDER[a.role] - ROLE_ORDER[b.role])
  if (!listed.length) {
    return kind === 'web'
      ? <p>ODS did not create a login for {ext.name}. If it asks you to create an account the first time you open it, that account belongs to {ext.name} and is kept with its data.</p>
      : <p>ODS did not configure credentials for {ext.name}.</p>
  }
  return <>
    <p>These settings were saved on this machine for {ext.name}. Their values stay in the ODS settings file (.env) and are not shown here.</p>
    <ul className="extension-guide-settings">
      {listed.map(item => (
        <li key={item.key}>
          <div>
            <code>{item.key}</code>
            <span className="extension-guide-role">{ROLE_LABELS[item.role] || 'Setting'}</span>
            <span className={item.configured ? 'extension-guide-saved' : 'extension-guide-missing'}>{item.configured ? 'Saved' : 'Not set'}</span>
          </div>
          {item.description && <small>{item.description}</small>}
        </li>
      ))}
    </ul>
    <p className="extension-guide-muted">To see the values, run this on the ODS host from your ODS folder:</p>
    <CopyLine value={credentialsCommand(listed.map(item => item.key))} label="command to show the values" />
    <p className="extension-guide-muted">A password changed inside the application may differ from the value saved at install.</p>
  </>
}

export default function ExtensionGuide({ ext, headless = false, onClose }) {
  const [state, setState] = useState({ loading: true, detail: null, error: '' })
  const [pinned, setPinnedState] = useState(() => isPinned(ext.id))

  useEffect(() => {
    let alive = true
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 15000)
    fetch(`/api/extensions/${ext.id}`, { signal: controller.signal, cache: 'no-store' })
      .then(response => (response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`))))
      .then(detail => { if (alive) setState({ loading: false, detail, error: '' }) })
      .catch(() => { if (alive) setState({ loading: false, detail: null, error: 'The guide could not be loaded.' }) })
      .finally(() => clearTimeout(timeout))
    return () => { alive = false; clearTimeout(timeout); controller.abort() }
  }, [ext.id])

  useEffect(() => {
    const handler = event => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const detail = state.detail
  const guide = detail?.guide || null
  // The catalog's kind accounts for the running definition; the guide's is a fallback.
  const kind = extensionKind(ext.usage_kind ? ext : { usage_kind: guide?.kind }, headless)
  const running = ext.status === 'enabled'
  const url = kind === 'web' ? serviceUrl({
    public_url: detail?.public_url || ext.public_url,
    external_port: guide ? (guide.hostPort ?? 0) : ext.external_port,
    external_port_default: ext.external_port_default,
    port: ext.port,
    ui_path: guide?.uiPath || ext.ui_path,
  }) : null
  const documentation = detail?.integration?.documentation
  const docsUrl = guide?.docsUrl || ext.docs_url
  const canPin = kind === 'web' && ext.source === 'user' && Boolean(url)
  const togglePin = () => { setPinned(ext.id, !pinned); setPinnedState(!pinned) }

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60" onClick={onClose}>
      <div className="extension-guide" role="dialog" aria-modal="true" aria-label={`How to use ${ext.name}`} onClick={event => event.stopPropagation()}>
        <header>
          <div>
            <span className="extension-guide-eyebrow"><BookOpen size={13} /> How to use</span>
            <h3>{ext.name}</h3>
          </div>
          <button type="button" onClick={onClose} aria-label="Close guide" autoFocus><X size={18} /></button>
        </header>
        <div className="extension-guide-actions">
          {kind === 'web' && url && running && (
            <a href={url} target="_blank" rel="noopener noreferrer" className="extension-guide-open"><ExternalLink size={13} /> Open {ext.name}</a>
          )}
          {canPin && (
            <button type="button" onClick={togglePin} aria-pressed={pinned}>
              {pinned ? <><PinOff size={13} /> Unpin from Applications</> : <><Pin size={13} /> Pin to Applications</>}
            </button>
          )}
          {docsUrl && <a href={docsUrl} target="_blank" rel="noopener noreferrer"><ExternalLink size={13} /> Documentation</a>}
        </div>
        <Section title="What it is for"><p>{detail?.description || ext.description || 'No description available.'}</p></Section>
        {state.loading ? (
          <p className="extension-guide-muted flex items-center gap-2"><Loader2 size={13} className="animate-spin" /> Loading guide…</p>
        ) : state.error ? (
          <p role="alert" className="extension-guide-muted">{state.error}</p>
        ) : <>
          <Section title="How to open it">
            <OpenSection ext={ext} kind={kind} url={url} running={running} guide={guide}
              listed={ext.source === 'core' || (ext.source === 'user' && pinned)} />
          </Section>
          <Section title={kind === 'web' ? 'Signing in' : 'Credentials'}><SignInSection ext={ext} kind={kind} settings={guide?.settings || []} /></Section>
          {detail?.dependents?.length > 0 && (
            <Section title="Used by"><p>{detail.dependents.join(', ')}</p></Section>
          )}
          {documentation && (
            <Section title="Guide from the ODS recipe">
              <div className="extension-guide-readme"><ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{documentation}</ReactMarkdown></div>
              {detail.integration.documentationTruncated && <p className="extension-guide-muted">Only the first part of the recipe guide is shown here.</p>}
            </Section>
          )}
          {!documentation && !docsUrl && <Section title="Documentation"><p>No guide was published with this extension.</p></Section>}
        </>}
      </div>
    </div>
  )
}
