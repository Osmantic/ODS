import { useEffect, useState } from 'react'

function sourceHref(value) {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : undefined
  } catch { return undefined }
}

function SourceLink({ url, children }) {
  const href = sourceHref(url)
  return href ? <a href={href} target="_blank" rel="noopener noreferrer" className="underline">{children}</a> : <span>{children}</span>
}

const ROLES = { artifact_publisher: 'Artifact publisher', declared_base: 'Declared base', declared_ancestor: 'Declared ancestor' }

export default function ModelTermsDetails({ modelId }) {
  const [open, setOpen] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    let current = true
    setLoading(true)
    setError(null)
    setResult(null)
    const deadline = setTimeout(() => {
      if (!current) return
      current = false
      controller.abort()
      setLoading(false)
      setError('Sources and terms took too long to load. Retry.')
    }, 15000)
    ;(async () => {
      try {
        const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/terms`, { signal: controller.signal })
        const body = await response.json()
        if (!current) return
        if (!response.ok) throw new Error('Sources and terms could not be loaded.')
        if (body.modelId !== modelId || typeof body.recordValid !== 'boolean') throw new Error('The terms response does not match this model.')
        setResult(body)
      } catch (reason) {
        if (current && reason?.name !== 'AbortError') setError(reason.message || 'Sources and terms could not be loaded.')
      } finally {
        clearTimeout(deadline)
        if (current) setLoading(false)
      }
    })()
    return () => { current = false; clearTimeout(deadline); controller.abort() }
  }, [open, modelId, attempt])

  const terms = result?.terms
  return <div className="mt-2 text-xs text-theme-text-muted">
    <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)} className="text-theme-accent underline">Sources and terms</button>
    {open && <section aria-label="Model sources and terms" className="mt-2 space-y-2 rounded-md border border-theme-border p-3">
      {loading && <p role="status">Loading sources and terms…</p>}
      {error && <div role="alert"><p>{error}</p><button type="button" onClick={() => setAttempt(value => value + 1)} className="underline">Retry terms</button></div>}
      {result && !result.recordValid && <div role="status"><p>Source and license information is incomplete.</p>{result.errors?.map(message => <p key={message}>{message}</p>)}</div>}
      {terms && <>
        <p className="font-semibold text-theme-text">{result.releaseReady ? 'Terms reviewed' : 'License review pending'}</p>
        <p>Publisher declarations are recorded below. They do not establish permission for every use.</p>
        <p>Commercial use: {terms.commercial_use === 'permitted_with_conditions' ? 'Permitted subject to the recorded conditions' : terms.commercial_use === 'restricted' ? 'Restricted; read the publisher terms' : 'Not yet assessed'}.</p>
        <p>Upstream acceptance: {['required', 'required_by_observed_gating'].includes(terms.upstream_acceptance) ? 'Required; complete it with the upstream publisher' : terms.upstream_acceptance === 'not_required' ? 'Not required by the reviewed terms' : 'Not yet assessed'}.</p>
        <ul className="space-y-2">
          {terms.sources?.map(source => <li key={`${source.repository}:${source.revision}`}>
            <span>{ROLES[source.role] || 'Source'}: </span><SourceLink url={source.url}>{source.repository}</SourceLink>
            <div><SourceLink url={source.declaration_url}>{source.license_id || 'No license identifier declared'}{source.license_name ? ` (${source.license_name})` : ''}</SourceLink></div>
          </li>)}
        </ul>
        {terms.license_documents?.length ? <div><p className="font-semibold">Publisher license documents</p><ul>{terms.license_documents.map((document, index) => <li key={`${document.url}:${index}`}><SourceLink url={document.url}>{document.repo}</SourceLink></li>)}</ul></div> : <p>No separate license document was retrieved in this review.</p>}
        {terms.note && <p>{terms.note}</p>}
        {terms.artifacts?.some(artifact => !artifact.observed_present) && <p className="text-amber-400">The configured download file was not found at the reviewed source revision.</p>}
        <p>Retain the applicable license and attribution notices when redistributing model files.</p>
      </>}
    </section>}
  </div>
}
