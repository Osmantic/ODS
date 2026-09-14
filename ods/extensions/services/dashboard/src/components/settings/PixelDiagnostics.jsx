import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, Cpu, ShieldCheck, RefreshCw } from 'lucide-react'
import MetalMetricIcon from '../MetalMetricIcon'
import PixelDiagnosticsDownload from './PixelDiagnosticsDownload'

const checks = [
  { id: 'agent', label: 'Agent connection', path: '/api/pixel/status', icon: Activity },
  { id: 'model', label: 'ODS model', path: '/api/status', icon: Cpu },
  { id: 'access', label: 'Access verification', path: '/api/pixel/access-mode', icon: ShieldCheck },
]
const text = value => typeof value === 'string' && value.length <= 512 ? value : null
const context = value => Number.isInteger(value) && value > 0 && value <= 10_000_000
  ? `${value.toLocaleString()} tokens` : 'Not reported'
const mode = value => value === 'sandboxed' ? 'Safer mode' : value === 'full-access' ? 'Full Access' : 'Not verified'

export function summarizeCheck(id, data) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Invalid status')
  if (id === 'agent') {
    if (typeof data.available !== 'boolean') throw new Error('Invalid status')
    return { state: data.available ? 'Ready' : 'Unavailable', ok: data.available,
      detail: text(data.detail) || 'The gateway did not report additional detail.',
      rows: data.runtime ? [['Agent model', text(data.runtime.model) || 'Not reported'], ['Context window', context(data.runtime.contextLength)]] : [],
    }
  }
  if (id === 'model') {
    if (!('inference' in data) && !('model' in data)) throw new Error('Invalid status')
    // Modern status separates loaded runtime telemetry from configured metadata.
    const runtime = 'inference' in data
    const name = text(runtime ? data.inference?.loadedModel : data.model?.name)
    return { state: name ? 'Reported by ODS' : 'Not loaded', ok: Boolean(name),
      detail: 'Local model telemetry. A remote Pixel route may use a different model.',
      rows: [['Local model', name || 'Not reported'], ['Context window', context(runtime ? data.inference?.contextSize : data.model?.contextLength)]],
    }
  }
  if (typeof data.available !== 'boolean') throw new Error('Invalid status')
  const verified = data.available && data.runtime_verified === true && ['sandboxed', 'full-access'].includes(data.effective_mode)
  return { state: verified ? 'Verified' : 'Not verified', ok: verified,
    detail: data.pending ? 'An access transition is unfinished.' : data.busy ? 'Pixel is working; access changes must wait.'
      : verified ? 'The host verified the effective access mode.' : 'The host has not verified an effective access mode. Do not infer permissions from chat availability.',
    rows: [['Configured', mode(data.configured_mode)], ['Effective', verified ? mode(data.effective_mode) : 'Not verified']],
  }
}

export default function PixelDiagnostics() {
  const [results, setResults] = useState({})
  const [busy, setBusy] = useState(false)
  const [checkedAt, setCheckedAt] = useState(null)
  const request = useRef(null)
  const refresh = useCallback(async () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setBusy(true); setResults({}); setCheckedAt(null)
    const timeout = setTimeout(() => controller.abort(), 12000)
    await Promise.all(checks.map(async check => {
      let result
      try {
        const response = await fetch(check.path, { signal: controller.signal, cache: 'no-store' })
        if (!response.ok) throw new Error('Status unavailable')
        result = summarizeCheck(check.id, await response.json())
      } catch {
        result = { state: 'Unavailable', ok: false, detail: 'Could not verify this check. Retry after the host service is available.', rows: [] }
      }
      if (request.current === controller) setResults(previous => ({ ...previous, [check.id]: result }))
    }))
    clearTimeout(timeout)
    if (request.current === controller) { setBusy(false); setCheckedAt(new Date()) }
  }, [])
  useEffect(() => {
    void refresh()
    return () => { request.current?.abort(); request.current = null }
  }, [refresh])

  return <section className="pixel-diagnostics" aria-label="Pixel diagnostics checks">
    <div className="pixel-diagnostics-intro">
      <p>Inspect the agent, its model, and the host access boundary without changing configuration.</p>
      <button type="button" onClick={refresh} disabled={busy}><MetalMetricIcon icon={RefreshCw} size={14}/>{busy ? 'Checking…' : 'Refresh checks'}</button>
    </div>
    <p role="status" className="pixel-diagnostics-time">{busy ? 'Reading current host status…' : checkedAt ? `Checked ${checkedAt.toLocaleTimeString()}` : 'Not checked'}</p>
    <PixelDiagnosticsDownload results={results} checkedAt={checkedAt} busy={busy}/>
    {checks.map(check => {
      const result = results[check.id]
      return <section key={check.id} aria-labelledby={`pixel-check-${check.id}`} className="pixel-diagnostic-check">
        <header><h3 id={`pixel-check-${check.id}`}><MetalMetricIcon icon={check.icon} size={16}/>{check.label}</h3><span className={result?.ok ? 'is-confirmed' : ''}>{result?.state || 'Checking…'}</span></header>
        {result && <><p>{result.detail}</p><dl>{result.rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></>}
      </section>
    })}
    <p className="pixel-diagnostics-note">These checks report availability, not proof of a completed task. Model responses and tool results must still be verified. Refreshing does not send a prompt, restart a service, or change permissions.</p>
    <nav aria-label="Diagnostic next steps"><Link to="/models">Model settings</Link><Link to="/settings?section=access">Access settings</Link><Link to="/settings?section=connections">Provider settings</Link></nav>
  </section>
}
