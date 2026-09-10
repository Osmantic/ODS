import { useEffect, useState } from 'react'
import { usePortalIdentity } from '../../contexts/PortalIdentityContext'

export default function AssistantIdentitySettings() {
  const { document, ready, busy, error, notice, reload, save } = usePortalIdentity()
  const [draft, setDraft] = useState('')
  useEffect(() => { if (document) setDraft(document.displayName) }, [document])
  return <section className="rounded border border-theme-border p-4 space-y-3" aria-labelledby="assistant-identity-title">
    <h2 id="assistant-identity-title" className="font-medium">Assistant identity</h2>
    <p className="text-sm text-theme-text-muted">Applies to this ODS installation across browsers. Changes display text only; no model restart or permission changes. Your own profile is separate.</p>
    <form onSubmit={event => { event.preventDefault(); void save(draft) }} className="space-y-3">
      <label className="block">Assistant display name
        <input className="block w-full rounded border border-theme-border bg-theme-bg p-2" autoComplete="off" maxLength={240}
          placeholder="Portal" value={draft} onChange={event => setDraft(event.target.value)} disabled={busy || !ready}/>
      </label>
      <p className="text-xs text-theme-text-muted">Up to 60 characters. An empty name resets to Portal.</p>
      <div className="flex flex-wrap gap-3">
        <button type="submit" disabled={busy || !ready}>Save name</button>
        <button type="button" disabled={busy || !ready} onClick={() => setDraft('Portal')}>Reset to Portal</button>
        <button type="button" disabled={busy} onClick={() => { void reload() }}>Refresh saved name</button>
      </div>
    </form>
    {busy && <p role="status">Checking saved identity…</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </section>
}
