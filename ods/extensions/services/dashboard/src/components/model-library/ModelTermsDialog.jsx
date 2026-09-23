import { useEffect, useId, useRef, useState } from 'react'
import ModelTermsContent from './ModelTermsContent'

function validateResponse(body, modelId) {
  if (!body || body.modelId !== modelId || typeof body.recordValid !== 'boolean') {
    throw new Error('The terms response does not match this model. Close and retry.')
  }
  if (!body.recordValid) return body
  const terms = body.terms
  if (typeof body.termsDigest !== 'string' || !/^[a-f0-9]{64}$/.test(body.termsDigest) || !terms ||
      !['sources', 'license_documents', 'notice_documents', 'issues'].every(key => Array.isArray(terms[key])) ||
      terms.sources.length === 0 ||
      !terms.sources.every(source => source && typeof source.repository === 'string') ||
      ![...terms.license_documents, ...terms.notice_documents].every(document => document && typeof document.url === 'string') ||
      !terms.issues.every(issue => typeof issue === 'string') ||
      !['permitted_with_conditions', 'restricted', 'not_assessed'].includes(terms.commercial_use) ||
      !['required', 'required_by_observed_gating', 'not_required', 'not_assessed'].includes(terms.upstream_acceptance)) {
    throw new Error('Source and license information is incomplete. Close and retry.')
  }
  return body
}

export default function ModelTermsDialog(props) {
  // A different artifact or preview must never inherit checked acknowledgements.
  return <TermsReview key={`${props.modelId}:${props.preview?.termsDigest || ''}`} {...props} />
}

function TermsReview({ modelId, modelName, preview, onCancel, onConfirm, confirmLabel = 'Confirm download' }) {
  const titleId = useId()
  const dialogRef = useRef(null)
  const submitted = useRef(false)
  const [attempt, setAttempt] = useState(0)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [acknowledged, setAcknowledged] = useState(false)
  const [upstreamAccepted, setUpstreamAccepted] = useState(false)

  useEffect(() => {
    const previous = document.activeElement
    dialogRef.current?.focus()
    return () => previous?.focus?.()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    let current = true
    setResult(null)
    setError(null)
    setLoading(true)
    setAcknowledged(false)
    setUpstreamAccepted(false)
    const deadline = setTimeout(() => {
      if (!current) return
      current = false
      controller.abort()
      setLoading(false)
      setError('Sources and terms took too long to load. Retry terms.')
    }, 15000)
    ;(async () => {
      try {
        let body = preview
        if (preview === undefined) {
          const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/terms`, { signal: controller.signal })
          body = await response.json()
          if (!response.ok) throw new Error('Sources and terms could not be loaded. Retry terms.')
        }
        if (!current) return
        setResult(validateResponse(body, modelId))
      } catch (reason) {
        if (current) setError(reason.message || 'Sources and terms could not be loaded. Retry terms.')
      } finally {
        clearTimeout(deadline)
        if (current) setLoading(false)
      }
    })()
    return () => { current = false; clearTimeout(deadline); controller.abort() }
  }, [modelId, preview, attempt])

  const upstreamRequired = ['required', 'required_by_observed_gating'].includes(result?.terms?.upstream_acceptance)
  const canConfirm = !loading && !error && result?.recordValid === true && acknowledged && (!upstreamRequired || upstreamAccepted)
  const confirm = () => {
    if (!canConfirm || submitted.current) return
    submitted.current = true
    onConfirm({ termsDigest: result.termsDigest, acknowledged: true, ...(upstreamRequired ? { upstreamAccepted: true } : {}) })
  }
  const keyDown = event => {
    if (event.key === 'Escape') { event.preventDefault(); onCancel(); return }
    if (event.key !== 'Tab') return
    const controls = [...dialogRef.current.querySelectorAll('button:not(:disabled), input:not(:disabled), a[href]')]
    const first = controls[0]
    const last = controls.at(-1)
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) {
      event.preventDefault(); last?.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first?.focus()
    }
  }
  return <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/75 px-4 py-6 backdrop-blur-sm">
    <section ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={keyDown}
      className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-theme-border bg-theme-card p-5 text-sm text-theme-text-muted shadow-xl">
      <h2 id={titleId} className="mb-2 text-lg font-semibold text-theme-text">Review model terms</h2>
      <p className="mb-4 break-words font-medium text-theme-text">{modelName || result?.name || modelId}</p>
      {loading && <p role="status">Loading sources and terms…</p>}
      {error && <div role="alert"><p>{error}</p>{preview === undefined && <button type="button" className="underline" onClick={() => setAttempt(value => value + 1)}>Retry terms</button>}</div>}
      {result?.recordValid === false && <div role="alert"><p>Source and license information is incomplete. Download is blocked.</p>{Array.isArray(result.errors) && result.errors.filter(message => typeof message === 'string').map(message => <p key={message}>{message}</p>)}</div>}
      {result?.recordValid && <>
        <ModelTermsContent result={result} />
        <label className="mt-5 flex items-start gap-2"><input type="checkbox" className="mt-1" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} /><span>I have reviewed these sources, restrictions, and unresolved issues.</span></label>
        {upstreamRequired && <label className="mt-3 flex items-start gap-2"><input type="checkbox" className="mt-1" checked={upstreamAccepted} onChange={event => setUpstreamAccepted(event.target.checked)} /><span>{result.terms.upstream_acceptance === 'required_by_observed_gating' ? 'I have completed the acceptance or access step required by the publisher.' : 'I have read and accept the applicable terms under the recorded conditions.'}</span></label>}
      </>}
      <div className="mt-5 flex justify-end gap-3">
        <button type="button" onClick={onCancel} className="rounded-md border border-theme-border px-4 py-2 text-theme-text">Cancel</button>
        <button type="button" disabled={!canConfirm} onClick={confirm} className="rounded-md bg-theme-accent px-4 py-2 font-semibold text-theme-bg disabled:cursor-not-allowed disabled:opacity-40">{confirmLabel}</button>
      </div>
    </section>
  </div>
}
