import { useState, useEffect, useCallback } from 'react'
import {
  UserPlus, Copy, Check, Trash2, RefreshCw, QrCode, Share2, X,
  Loader2, AlertCircle, Clock, Users, KeyRound, ShieldCheck, Mic2,
  Printer, MessageSquare,
} from 'lucide-react'

// Auth: nginx injects "Authorization: Bearer ${DASHBOARD_API_KEY}" via
// proxy_set_header for all /api/ requests (see nginx.conf). All fetches
// use relative URLs so the proxy adds the header before forwarding to
// dashboard-api. No explicit auth in JS.

const fetchJson = async (url, init = {}, ms = 8000) => {
  const c = new AbortController()
  const t = setTimeout(() => c.abort(), ms)
  try {
    return await fetch(url, { ...init, signal: c.signal })
  } finally {
    clearTimeout(t)
  }
}

const SCOPES = [
  { value: 'chat', label: 'Chat', help: 'Guest lands in Open WebUI chat.' },
  { value: 'hermes', label: 'Advanced Hermes', help: 'Guest lands in the full Hermes Agent behind the same session gate.' },
]

const EXPIRY_PRESETS = [
  { value: 900, label: '15 minutes' },
  { value: 3600, label: '1 hour' },
  { value: 86400, label: '24 hours' },
]

function formatRelative(iso) {
  if (!iso) return null
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return null
  const diff = t - Date.now()
  const abs = Math.abs(diff)
  const minutes = Math.round(abs / 60_000)
  const hours = Math.round(abs / 3_600_000)
  const future = diff > 0
  if (minutes < 1) return future ? 'in seconds' : 'just now'
  if (minutes < 60) return future ? `in ${minutes}m` : `${minutes}m ago`
  if (hours < 24) return future ? `in ${hours}h` : `${hours}h ago`
  const days = Math.round(abs / 86_400_000)
  return future ? `in ${days}d` : `${days}d ago`
}

function isOwnerToken(token) {
  return token.token_type === 'owner'
}

function tokenStatus(token) {
  if (token.revoked_at) return { label: 'revoked', tone: 'bg-theme-border text-theme-text-muted' }
  if (!isOwnerToken(token) && token.expires_at && new Date(token.expires_at).getTime() < Date.now()) {
    return { label: 'expired', tone: 'bg-theme-border text-theme-text-muted' }
  }
  if (!isOwnerToken(token) && token.redemption_count > 0 && !token.reusable) {
    return { label: 'used', tone: 'bg-theme-border text-theme-text-muted' }
  }
  if (token.redemption_count > 0) {
    return { label: `used x ${token.redemption_count}`, tone: 'bg-blue-500/20 text-blue-400' }
  }
  return { label: 'active', tone: 'bg-green-500/20 text-green-400' }
}

function tokenCanRevoke(token) {
  const status = tokenStatus(token).label
  return status === 'active' || status.startsWith('used')
}

export default function Invites() {
  const [tokens, setTokens] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showOwnerCreate, setShowOwnerCreate] = useState(false)
  const [showGuestCreate, setShowGuestCreate] = useState(false)
  const [generated, setGenerated] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [ownerCardStatus, setOwnerCardStatus] = useState(null)

  const refresh = useCallback(async () => {
    setRefreshing(true)
    try {
      const [resp, ownerStatusResp] = await Promise.all([
        fetchJson('/api/auth/magic-link/list'),
        fetchJson('/api/auth/magic-link/owner-card/status'),
      ])
      if (!resp.ok) throw new Error(`list failed: ${resp.status}`)
      const data = await resp.json()
      if (ownerStatusResp.ok) {
        setOwnerCardStatus(await ownerStatusResp.json())
      } else {
        setOwnerCardStatus({
          ready: false,
          reason: `Owner-card status unavailable (${ownerStatusResp.status})`,
        })
      }
      setTokens(data.tokens || [])
      setError(null)
    } catch (err) {
      setOwnerCardStatus(current => current || {
        ready: false,
        reason: 'Owner-card status unavailable.',
      })
      setError(err.message)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const handleRevoke = async (prefix) => {
    try {
      const resp = await fetchJson(`/api/auth/magic-link/${prefix}`, { method: 'DELETE' })
      if (!resp.ok && resp.status !== 404) {
        const body = await resp.json().catch(() => ({}))
        throw new Error(body.detail || `revoke failed: ${resp.status}`)
      }
      await refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  if (loading) {
    return (
      <div className="p-8">
        <div className="animate-pulse">
          <div className="h-8 bg-theme-card rounded w-1/3 mb-8" />
          <div className="h-40 bg-theme-card rounded-xl mb-4" />
          <div className="space-y-3">
            {[...Array(3)].map((_, i) => <div key={i} className="h-20 bg-theme-card rounded-xl" />)}
          </div>
        </div>
      </div>
    )
  }

  const ownerTokens = tokens.filter(isOwnerToken)
  const guestTokens = tokens.filter(t => !isOwnerToken(t))
  const ownerCardUnavailable = ownerCardStatus?.ready === false

  return (
    <div className="p-8">
      <div className="mb-8 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-theme-text">Setup / Owner</h1>
          <p className="text-theme-text-muted mt-1">
            Create factory owner cards for Dream Talk, and keep guest chat invites available when you need them.
          </p>
        </div>
        <button
          onClick={refresh}
          disabled={refreshing}
          className="p-2 text-theme-text-muted hover:text-theme-text hover:bg-theme-surface-hover rounded-lg transition-colors disabled:opacity-50"
          title="Refresh"
          aria-label="Refresh setup owner links"
        >
          <RefreshCw size={20} className={refreshing ? 'animate-spin' : ''} />
        </button>
      </div>

      {error && (
        <div className="mb-6 p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-red-400 text-sm flex items-start gap-2">
          <AlertCircle size={18} className="flex-shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      <VoiceReadiness />

      <section className="mb-8 bg-theme-card border border-theme-border rounded-xl p-5">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div>
            <div className="flex items-center gap-2 text-theme-text">
              <KeyRound size={20} className="text-theme-accent" />
              <h2 className="text-lg font-semibold">Factory owner card</h2>
            </div>
            <p className="mt-2 max-w-2xl text-sm text-theme-text-muted">
              This QR is a physical key for the shipped device. It creates normal 12-hour Dream sessions
              and lands the holder in Dream Talk; the QR itself remains valid until revoked.
            </p>
          </div>
          <button
            onClick={() => setShowOwnerCreate(true)}
            disabled={ownerCardUnavailable}
            className="inline-flex items-center justify-center gap-2 bg-theme-accent text-white px-4 py-2 rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            <Printer size={18} />
            Print owner card
          </button>
        </div>

        {ownerCardUnavailable && (
          <div className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-100 flex items-start gap-2">
            <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
            <span>{ownerCardStatus.reason || 'Enable Dream proxy before generating owner cards.'}</span>
          </div>
        )}

        {ownerTokens.length === 0 ? (
          <EmptyOwnerState
            onCreate={() => setShowOwnerCreate(true)}
            disabled={ownerCardUnavailable}
          />
        ) : (
          <div className="mt-5 space-y-3">
            {ownerTokens.map(t => (
              <TokenRow key={t.token_hash_prefix} token={t} onRevoke={() => handleRevoke(t.token_hash_prefix)} />
            ))}
          </div>
        )}
      </section>

      <section className="bg-theme-card border border-theme-border rounded-xl p-5">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex items-center gap-2 text-theme-text">
              <MessageSquare size={20} className="text-theme-text-muted" />
              <h2 className="text-lg font-semibold">Guest access</h2>
            </div>
            <p className="mt-2 max-w-2xl text-sm text-theme-text-muted">
              Time-limited magic links still work for short-term access to chat or advanced Hermes.
            </p>
          </div>
          <button
            onClick={() => setShowGuestCreate(true)}
            className="inline-flex items-center justify-center gap-2 bg-theme-bg border border-theme-border text-theme-text px-4 py-2 rounded-lg hover:bg-theme-surface-hover transition-colors"
          >
            <UserPlus size={18} />
            New guest invite
          </button>
        </div>

        {guestTokens.length === 0 ? (
          <EmptyGuestState onCreate={() => setShowGuestCreate(true)} />
        ) : (
          <div className="mt-5 space-y-3">
            {guestTokens.map(t => (
              <TokenRow key={t.token_hash_prefix} token={t} onRevoke={() => handleRevoke(t.token_hash_prefix)} />
            ))}
          </div>
        )}
      </section>

      {showOwnerCreate && (
        <CreateOwnerModal
          ownerCardStatus={ownerCardStatus}
          onClose={() => setShowOwnerCreate(false)}
          onCreated={(record) => {
            setShowOwnerCreate(false)
            setGenerated(record)
            refresh()
          }}
        />
      )}

      {showGuestCreate && (
        <CreateGuestModal
          onClose={() => setShowGuestCreate(false)}
          onCreated={(record) => {
            setShowGuestCreate(false)
            setGenerated(record)
            refresh()
          }}
        />
      )}

      {generated && (
        <GeneratedTokenModal
          record={generated}
          onClose={() => setGenerated(null)}
        />
      )}
    </div>
  )
}

function VoiceReadiness() {
  const secure = typeof window === 'undefined' ? true : window.isSecureContext
  return (
    <div className={`mb-6 rounded-xl border p-4 text-sm flex items-start gap-3 ${
      secure
        ? 'border-green-500/20 bg-green-500/10 text-green-100'
        : 'border-amber-500/20 bg-amber-500/10 text-amber-100'
    }`}>
      <Mic2 size={18} className="mt-0.5 flex-shrink-0" />
      <div>
        <p className="font-medium text-theme-text">Voice readiness</p>
        <p className="mt-1 text-theme-text-muted">
          {secure
            ? 'This browser origin is secure, so live microphone access can be offered when Dream Talk voice services are ready.'
            : 'Mobile browsers usually block live microphone access on plain HTTP. Dream Talk text still works, with phone-native audio capture when the browser offers it.'}
        </p>
      </div>
    </div>
  )
}

function EmptyOwnerState({ onCreate, disabled }) {
  return (
    <div className="mt-5 rounded-xl border border-dashed border-theme-border p-6 text-center">
      <ShieldCheck size={32} className="mx-auto mb-3 text-theme-text-muted" />
      <h3 className="text-base font-semibold text-theme-text mb-1">No owner cards yet</h3>
      <p className="text-sm text-theme-text-muted mb-4 max-w-lg mx-auto">
        Generate one for a factory card or first owner handoff. Revoke it if the printed card is lost.
      </p>
      <button
        onClick={onCreate}
        disabled={disabled}
        className="inline-flex items-center gap-2 bg-theme-accent text-white px-4 py-2 rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
      >
        <Printer size={18} />
        Create owner card
      </button>
    </div>
  )
}

function EmptyGuestState({ onCreate }) {
  return (
    <div className="mt-5 rounded-xl border border-dashed border-theme-border p-6 text-center">
      <Users size={32} className="mx-auto mb-3 text-theme-text-muted" />
      <h3 className="text-base font-semibold text-theme-text mb-1">No guest invites yet</h3>
      <p className="text-sm text-theme-text-muted mb-4 max-w-lg mx-auto">
        Guest links are temporary credentials. Anyone who opens one gets the selected access until it expires or is used.
      </p>
      <button
        onClick={onCreate}
        className="inline-flex items-center gap-2 bg-theme-bg border border-theme-border text-theme-text px-4 py-2 rounded-lg hover:bg-theme-surface-hover transition-colors"
      >
        <UserPlus size={18} />
        Create guest invite
      </button>
    </div>
  )
}

function TokenRow({ token, onRevoke }) {
  const status = tokenStatus(token)
  const expires = isOwnerToken(token) ? null : formatRelative(token.expires_at)
  const lastRedeemed = formatRelative(token.last_redeemed_at)
  const canRevoke = tokenCanRevoke(token)

  return (
    <div className="bg-theme-bg border border-theme-border rounded-xl p-4 flex items-center justify-between gap-4">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <span className="font-medium text-theme-text">{token.target_username}</span>
          <span className={`text-xs px-2 py-0.5 rounded ${status.tone}`}>{status.label}</span>
          {isOwnerToken(token) ? (
            <span className="text-xs px-2 py-0.5 rounded bg-theme-accent/20 text-theme-accent-light">owner</span>
          ) : token.reusable && (
            <span className="text-xs px-2 py-0.5 rounded bg-purple-500/20 text-purple-300">reusable</span>
          )}
          <span className="text-xs text-theme-text-muted">scope: {token.scope}</span>
        </div>
        {token.note && (
          <p className="text-xs text-theme-text-muted mb-1 truncate">{token.note}</p>
        )}
        <div className="flex items-center gap-3 text-xs text-theme-text-muted flex-wrap">
          <span className="inline-flex items-center gap-1">
            <Clock size={12} />
            {isOwnerToken(token) ? 'revoke-only' : `expires ${expires}`}
          </span>
          {lastRedeemed && <span>last used {lastRedeemed}</span>}
          <span className="font-mono opacity-70">{token.token_hash_prefix}...</span>
        </div>
      </div>
      {canRevoke && (
        <button
          onClick={onRevoke}
          className="p-2 text-theme-text-muted hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
          title="Revoke"
          aria-label={`Revoke ${isOwnerToken(token) ? 'owner card' : 'invite'} for ${token.target_username}`}
        >
          <Trash2 size={18} />
        </button>
      )}
    </div>
  )
}

function CreateOwnerModal({ ownerCardStatus, onClose, onCreated }) {
  const [username, setUsername] = useState('')
  const [note, setNote] = useState('Factory owner card')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState(null)
  const ownerCardUnavailable = ownerCardStatus?.ready === false

  const handleSubmit = async (e) => {
    e.preventDefault()
    setFormError(null)
    if (ownerCardUnavailable) {
      setFormError(ownerCardStatus.reason || 'Enable Dream proxy before generating owner cards.')
      return
    }
    const trimmed = username.trim()
    if (!/^[A-Za-z0-9._-]+$/.test(trimmed)) {
      setFormError('Username may only contain letters, numbers, dot, dash, and underscore.')
      return
    }
    setSubmitting(true)
    try {
      const resp = await fetchJson('/api/auth/magic-link/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_username: trimmed,
          token_type: 'owner',
          scope: 'hermes',
          url_mode: 'lan',
          note: note.trim() || null,
        }),
      })
      if (!resp.ok) throw await responseError(resp, 'generate')
      onCreated(await resp.json())
    } catch (err) {
      setFormError(err.message)
      setSubmitting(false)
    }
  }

  return (
    <Modal title="Create owner card" label="Create owner card" onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <UsernameInput value={username} onChange={setUsername} autoFocus />
        <label className="block mb-4">
          <span className="text-sm text-theme-text-muted">Note</span>
          <input
            type="text"
            value={note}
            onChange={e => setNote(e.target.value)}
            maxLength={200}
            className="mt-1 w-full bg-theme-bg border border-theme-border rounded-lg px-3 py-2 text-theme-text focus:outline-none focus:border-theme-accent"
          />
          <span className="text-xs text-theme-text-muted">
            Owner cards are reusable until revoked and redirect to Dream Talk.
          </span>
        </label>
        {ownerCardUnavailable && (
          <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-100 flex items-start gap-2">
            <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
            <span>{ownerCardStatus.reason || 'Enable Dream proxy before generating owner cards.'}</span>
          </div>
        )}
        <FormError message={formError} />
        <ModalActions
          onCancel={onClose}
          submitting={submitting}
          submitLabel="Generate owner QR"
          disabled={!username.trim() || ownerCardUnavailable}
        />
      </form>
    </Modal>
  )
}

function CreateGuestModal({ onClose, onCreated }) {
  const [username, setUsername] = useState('')
  const [scope, setScope] = useState('chat')
  const [expiresIn, setExpiresIn] = useState(3600)
  const [reusable, setReusable] = useState(false)
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setFormError(null)
    const trimmed = username.trim()
    if (!/^[A-Za-z0-9._-]+$/.test(trimmed)) {
      setFormError('Username may only contain letters, numbers, dot, dash, and underscore.')
      return
    }
    setSubmitting(true)
    try {
      const resp = await fetchJson('/api/auth/magic-link/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_username: trimmed,
          token_type: 'guest',
          scope,
          expires_in: expiresIn,
          reusable,
          note: note.trim() || null,
        }),
      })
      if (!resp.ok) throw await responseError(resp, 'generate')
      onCreated(await resp.json())
    } catch (err) {
      setFormError(err.message)
      setSubmitting(false)
    }
  }

  return (
    <Modal title="Create guest invite" label="Create guest invite" onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <UsernameInput value={username} onChange={setUsername} autoFocus />
        <label className="block mb-3">
          <span className="text-sm text-theme-text-muted">Access target</span>
          <select
            value={scope}
            onChange={e => setScope(e.target.value)}
            className="mt-1 w-full bg-theme-bg border border-theme-border rounded-lg px-3 py-2 text-theme-text focus:outline-none focus:border-theme-accent"
          >
            {SCOPES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
          <span className="text-xs text-theme-text-muted">{SCOPES.find(s => s.value === scope)?.help}</span>
        </label>
        <label className="block mb-3">
          <span className="text-sm text-theme-text-muted">Expires in</span>
          <select
            value={expiresIn}
            onChange={e => setExpiresIn(parseInt(e.target.value, 10))}
            className="mt-1 w-full bg-theme-bg border border-theme-border rounded-lg px-3 py-2 text-theme-text focus:outline-none focus:border-theme-accent"
          >
            {EXPIRY_PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
        </label>
        <label className="flex items-start gap-2 mb-3 cursor-pointer">
          <input type="checkbox" checked={reusable} onChange={e => setReusable(e.target.checked)} className="mt-1" />
          <span className="text-sm">
            <span className="text-theme-text">Reusable until expiry</span>
            <span className="block text-xs text-theme-text-muted">Every redemption is logged.</span>
          </span>
        </label>
        <label className="block mb-4">
          <span className="text-sm text-theme-text-muted">Note (optional)</span>
          <input
            type="text"
            value={note}
            onChange={e => setNote(e.target.value)}
            placeholder="for mom's iPad"
            maxLength={200}
            className="mt-1 w-full bg-theme-bg border border-theme-border rounded-lg px-3 py-2 text-theme-text focus:outline-none focus:border-theme-accent"
          />
        </label>
        <FormError message={formError} />
        <ModalActions onCancel={onClose} submitting={submitting} submitLabel="Generate" disabled={!username.trim()} />
      </form>
    </Modal>
  )
}

function Modal({ title, label, onClose, children }) {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-theme-card border border-theme-border rounded-xl p-6 w-full max-w-md"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={label}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-theme-text">{title}</h2>
          <button type="button" onClick={onClose} className="text-theme-text-muted hover:text-theme-text" aria-label="Close">
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

function UsernameInput({ value, onChange, autoFocus = false }) {
  return (
    <label className="block mb-3">
      <span className="text-sm text-theme-text-muted">Username</span>
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        required
        autoFocus={autoFocus}
        placeholder="alice"
        className="mt-1 w-full bg-theme-bg border border-theme-border rounded-lg px-3 py-2 text-theme-text focus:outline-none focus:border-theme-accent"
        maxLength={64}
      />
      <span className="text-xs text-theme-text-muted">Recorded with the token audit trail.</span>
    </label>
  )
}

function FormError({ message }) {
  if (!message) return null
  return (
    <div className="mb-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm flex items-start gap-2">
      <AlertCircle size={16} className="flex-shrink-0 mt-0.5" />
      <span>{message}</span>
    </div>
  )
}

function ModalActions({ onCancel, submitting, submitLabel, disabled }) {
  return (
    <div className="flex justify-end gap-2">
      <button type="button" onClick={onCancel} className="px-4 py-2 text-theme-text-muted hover:text-theme-text">
        Cancel
      </button>
      <button
        type="submit"
        disabled={submitting || disabled}
        className="flex items-center gap-2 bg-theme-accent text-white px-4 py-2 rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
      >
        {submitting && <Loader2 size={16} className="animate-spin" />}
        {submitLabel}
      </button>
    </div>
  )
}

async function responseError(resp, label) {
  const body = await resp.json().catch(() => ({}))
  const detail = Array.isArray(body.detail) ? body.detail[0]?.msg : body.detail
  return new Error(detail || `${label} failed: ${resp.status}`)
}

function GeneratedTokenModal({ record, onClose }) {
  const [copied, setCopied] = useState(false)
  const [qrDataUrl, setQrDataUrl] = useState(null)
  const [qrError, setQrError] = useState(null)
  const owner = record.token_type === 'owner'

  useEffect(() => {
    let cancelled = false
    const loadQr = async () => {
      try {
        const resp = await fetchJson(`/api/auth/magic-link/qr?url=${encodeURIComponent(record.url)}`)
        if (!resp.ok) {
          setQrError('QR generation unavailable on the server.')
          return
        }
        const data = await resp.json()
        if (!cancelled) setQrDataUrl(data.data_url)
      } catch (err) {
        if (!cancelled) setQrError(err.message)
      }
    }
    loadQr()
    return () => { cancelled = true }
  }, [record.url])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(record.url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Fallback: select the visible input manually.
    }
  }

  const share = async () => {
    if (!navigator.share) {
      copy()
      return
    }
    try {
      await navigator.share({
        title: owner ? `Dream Server owner card for ${record.target_username}` : `Dream Server invite for ${record.target_username}`,
        text: owner ? 'Scan to open Dream Talk on this Dream Server' : 'Tap to open Dream Server',
        url: record.url,
      })
    } catch {
      // User cancelled the share sheet.
    }
  }

  const print = () => {
    if (typeof window !== 'undefined') window.print()
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-theme-card border border-theme-border rounded-xl p-6 w-full max-w-lg"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={owner ? 'Owner card created' : 'Invite created'}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-theme-text">
            {owner ? 'Owner card ready' : 'Invite ready'} for {record.target_username}
          </h2>
          <button onClick={onClose} className="text-theme-text-muted hover:text-theme-text" aria-label="Close">
            <X size={20} />
          </button>
        </div>

        <p className="text-sm text-theme-text-muted mb-4">
          {owner
            ? 'Print this as QR #2 on the factory card. It lands in Dream Talk and remains usable until revoked.'
            : 'Share this temporary link with the intended guest. Each redemption is logged.'}
        </p>

        {qrDataUrl ? (
          <div className="bg-white p-4 rounded-xl flex justify-center mb-4">
            <img src={qrDataUrl} alt={owner ? 'QR code for owner card' : 'QR code for invite link'} className="w-56 h-56" />
          </div>
        ) : (
          <div className="bg-theme-bg border border-theme-border rounded-xl p-6 flex flex-col items-center justify-center mb-4 min-h-56">
            <QrCode size={48} className="text-theme-text-muted mb-2" />
            <p className="text-xs text-theme-text-muted text-center">{qrError || 'Generating QR code...'}</p>
          </div>
        )}

        <label className="block mb-4">
          <span className="text-xs text-theme-text-muted">{owner ? 'Owner URL' : 'Invite URL'}</span>
          <div className="mt-1 flex gap-2">
            <input
              type="text"
              readOnly
              value={record.url}
              onFocus={e => e.target.select()}
              className="flex-1 bg-theme-bg border border-theme-border rounded-lg px-3 py-2 text-theme-text font-mono text-xs"
            />
            <button
              onClick={copy}
              className="flex items-center gap-1 px-3 py-2 bg-theme-bg border border-theme-border rounded-lg text-theme-text hover:bg-theme-surface-hover text-sm"
              title="Copy link"
            >
              {copied ? <Check size={16} className="text-green-400" /> : <Copy size={16} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
        </label>

        <div className="flex justify-between items-center gap-4">
          <p className="text-xs text-theme-text-muted">
            {owner ? 'Revoke-only owner card' : `Expires ${formatRelative(record.expires_at)}`}
          </p>
          <div className="flex gap-2">
            {owner && (
              <button
                onClick={print}
                className="flex items-center gap-2 px-4 py-2 bg-theme-bg border border-theme-border rounded-lg text-theme-text hover:bg-theme-surface-hover text-sm"
              >
                <Printer size={16} />
                Print
              </button>
            )}
            {typeof navigator !== 'undefined' && navigator.share && (
              <button
                onClick={share}
                className="flex items-center gap-2 px-4 py-2 bg-theme-bg border border-theme-border rounded-lg text-theme-text hover:bg-theme-surface-hover text-sm"
              >
                <Share2 size={16} />
                Share
              </button>
            )}
            <button onClick={onClose} className="px-4 py-2 bg-theme-accent text-white rounded-lg hover:opacity-90 text-sm">
              Done
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
