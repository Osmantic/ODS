import { useEffect, useRef, useState } from 'react'
import { usePortalIdentity } from '../../contexts/PortalIdentityContext'
import { normalizeDisplayName } from '../../lib/portalIdentity'

export default function AssistantIdentitySettings() {
  const { document, ready, busy, error, notice, reload, save } = usePortalIdentity()
  const [draft, setDraft] = useState('')
  const previousDocument = useRef(null)
  const refreshButtonRef = useRef(null)
  const restoreRefreshFocus = useRef(false)
  useEffect(() => {
    const previous = previousDocument.current
    previousDocument.current = document
    if (document) setDraft(current => !previous || current === previous.displayName ? document.displayName : current)
  }, [document])
  useEffect(() => {
    if (!busy && restoreRefreshFocus.current) {
      restoreRefreshFocus.current = false
      refreshButtonRef.current?.focus()
    }
  }, [busy])
  async function submit(event) {
    event.preventDefault()
    if (await save(draft)) setDraft(normalizeDisplayName(draft))
  }
  return <section className="profile-settings assistant-identity-settings" aria-labelledby="assistant-identity-title">
    <h2 id="assistant-identity-title">Assistant identity</h2>
    <p>The assistant’s name across this ODS installation.</p>
    <form onSubmit={submit} className="space-y-3">
      <label className="profile-name-label">Assistant display name
        <input autoComplete="off" maxLength={60}
          placeholder="Assistant name" value={draft} onChange={event => setDraft(event.target.value)} disabled={busy || !ready}/>
      </label>
      {document && <p>Last confirmed name: {document.displayName}</p>}
      <div className="profile-settings-actions assistant-identity-actions">
        <button type="submit" disabled={busy || !ready}>Save name</button>
        <button type="button" disabled={busy || !ready} onClick={() => setDraft('Portal')}>Reset to Portal</button>
        <button ref={refreshButtonRef} type="button" disabled={busy} onClick={() => { restoreRefreshFocus.current = true; void reload() }}>Refresh saved name</button>
        {document && draft !== document.displayName && <button type="button" disabled={busy || !ready} onClick={() => setDraft(document.displayName)}>Use saved name</button>}
      </div>
    </form>
    {busy && <p role="status">Checking saved identity…</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </section>
}
