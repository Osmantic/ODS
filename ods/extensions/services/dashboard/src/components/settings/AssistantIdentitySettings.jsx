import { useEffect, useState } from 'react'
import { usePortalIdentity } from '../../contexts/PortalIdentityContext'
import { DEFAULT_ASSISTANT_NAME } from '../../lib/portalIdentity'

export default function AssistantIdentitySettings() {
  const { document, ready, busy, error, notice, reload, save } = usePortalIdentity()
  const [draft, setDraft] = useState('')
  useEffect(() => { if (document) setDraft(document.displayName) }, [document])
  return <section className="profile-settings assistant-identity-settings" aria-labelledby="assistant-identity-title">
    <h2 id="assistant-identity-title">Assistant identity</h2>
    <p>The assistant’s name across this ODS installation.</p>
    <form onSubmit={event => { event.preventDefault(); void save(draft) }} className="space-y-3">
      <label className="profile-name-label">Assistant display name
        <input autoComplete="off" maxLength={60}
          placeholder="Assistant name" value={draft} onChange={event => setDraft(event.target.value)} disabled={busy || !ready}/>
      </label>
      <div className="profile-settings-actions assistant-identity-actions">
        <button type="submit" disabled={busy || !ready}>Save name</button>
        <button type="button" disabled={busy || !ready} onClick={() => setDraft(DEFAULT_ASSISTANT_NAME)}>Reset to {DEFAULT_ASSISTANT_NAME}</button>
        <button type="button" disabled={busy} onClick={() => { void reload() }}>Refresh saved name</button>
      </div>
    </form>
    {busy && <p role="status">Checking saved identity…</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </section>
}
