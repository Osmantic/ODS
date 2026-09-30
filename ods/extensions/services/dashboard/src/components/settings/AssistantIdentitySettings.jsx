import { useEffect, useRef, useState } from 'react'
import { usePortalIdentity } from '../../contexts/PortalIdentityContext'
import { normalizeDisplayName } from '../../lib/portalIdentity'

export default function AssistantIdentitySettings() {
  const { document, ready, busy, error, notice, reload, save } = usePortalIdentity()
  const [draft, setDraft] = useState('')
  // A draft the user is holding. `null` means the field follows the saved name, so a refresh
  // adopts whatever the install reports. A non-null value is local intent (typed, reset, or
  // adopted) that a refresh must not overwrite. Intent is dropped only at a genuine user
  // decision point: an explicit adoption or a confirmed save. Timing (which document commit
  // happens to land before or after an edit) never decides it, and nothing mutates a ref
  // inside a state updater, so a StrictMode replay cannot fire a side effect twice.
  const [intent, setIntent] = useState(null)
  const appliedName = useRef('')
  useEffect(() => {
    if (!document || intent !== null) return
    if (document.displayName === appliedName.current) return
    appliedName.current = document.displayName
    setDraft(document.displayName)
  }, [document, intent])
  // Field is local intent while the user holds one; otherwise it mirrors the saved name.
  const named = document?.displayName
  const dirty = intent !== null && intent !== named
  const shown = intent !== null ? intent : (named ?? draft)
  const adopt = value => { appliedName.current = named; setIntent(null); setDraft(value) }
  async function submit(event) {
    event.preventDefault()
    const normalized = normalizeDisplayName(intent ?? shown)
    if (await save(normalized)) adopt(normalized)
  }
  return <section className="profile-settings assistant-identity-settings" aria-labelledby="assistant-identity-title">
    <h2 id="assistant-identity-title">Assistant identity</h2>
    <p>The assistant’s name across this ODS installation.</p>
    <form onSubmit={submit} className="space-y-3">
      <label className="profile-name-label">Assistant display name
        <input autoComplete="off" maxLength={60}
          placeholder="Assistant name" value={shown} onChange={event => setIntent(event.target.value)} disabled={busy || !ready}/>
      </label>
      {document && <p>Last confirmed name: {document.displayName}</p>}
      <div className="profile-settings-actions assistant-identity-actions">
        <button type="submit" disabled={busy || !ready}>Save name</button>
        <button type="button" disabled={busy || !ready} onClick={() => setIntent('Portal')}>Reset to Portal</button>
        <button type="button" disabled={busy} onClick={() => { void reload() }}>Refresh saved name</button>
        {document && dirty && <button type="button" disabled={busy || !ready} onClick={() => adopt(document.displayName)}>Use saved name</button>}
      </div>
    </form>
    {busy && <p role="status">Checking saved identity…</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </section>
}
