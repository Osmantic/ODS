import { useState } from 'react'
import { usePortalIdentity } from '../../contexts/PortalIdentityContext'

export default function AssistantIdentitySettings() {
  const { document, ready, busy, error, notice, reload, save } = usePortalIdentity()
  // A null draft follows the saved document. Keep edits separately so a late
  // refresh cannot overwrite text entered before a document effect settles.
  const [draft, setDraft] = useState(null)
  const shown = draft ?? document?.displayName ?? ''
  function edit(value) {
    setDraft(value === document?.displayName ? null : value)
  }
  async function submit(event) {
    event.preventDefault()
    if (await save(shown)) setDraft(null)
  }
  return <section className="profile-settings assistant-identity-settings" aria-labelledby="assistant-identity-title">
    <h2 id="assistant-identity-title">Assistant identity</h2>
    <p>The assistant’s name across this ODS installation.</p>
    <form onSubmit={submit} className="space-y-3">
      <label className="profile-name-label">Assistant display name
        <input autoComplete="off" maxLength={60}
          placeholder="Assistant name" value={shown} onChange={event => edit(event.target.value)} disabled={busy || !ready}/>
      </label>
      {document && <p>Last confirmed name: {document.displayName}</p>}
      <div className="profile-settings-actions assistant-identity-actions">
        <button type="submit" disabled={busy || !ready}>Save name</button>
        <button type="button" disabled={busy || !ready} onClick={() => edit('Portal')}>Reset to Portal</button>
        <button type="button" disabled={busy} onClick={() => { void reload() }}>Refresh saved name</button>
        {document && shown !== document.displayName && <button type="button" disabled={busy || !ready} onClick={() => setDraft(null)}>Use saved name</button>}
      </div>
    </form>
    {busy && <p role="status">Checking saved identity…</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </section>
}
