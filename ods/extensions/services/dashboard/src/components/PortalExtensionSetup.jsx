import { useEffect, useRef, useState } from 'react'
import './portal-extension-setup.css'

export function extensionSetupTarget(command) {
  return typeof command === 'string'
    ? /^[ \t]*(?:\/goal[ \t]+)?\/extensions?[ \t]+@([a-z0-9][a-z0-9_-]{0,63})(?:[ \t]+[^\r\n@;|&`]*?)?[ \t]*$/i.exec(command)?.[1]?.toLowerCase()
    : undefined
}

function requiredFields(plan, id) {
  if (plan?.schemaVersion !== 1 || plan.extensionId !== id || !Array.isArray(plan.steps) || plan.steps.length > 128) throw new Error('plan')
  const groups = [], seen = new Set()
  for (const step of plan.steps) {
    if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(step?.extensionId) || seen.has(step.extensionId) ||
        !Array.isArray(step.missingConfiguration) || !Array.isArray(step.configuration)) throw new Error('plan')
    seen.add(step.extensionId)
    const fields = step.missingConfiguration.map(key => {
      const matches = step.configuration.filter(field => field.key === key)
      if (!/^[A-Z][A-Z0-9_]{0,127}$/.test(key) || matches.length !== 1 ||
          matches[0].required !== true || matches[0].configured !== false || typeof matches[0].secret !== 'boolean') throw new Error('plan')
      return matches[0]
    })
    if (new Set(step.missingConfiguration).size !== fields.length) throw new Error('plan')
    if (fields.length) groups.push({id: step.extensionId, fields})
  }
  return groups
}

export default function PortalExtensionSetup({ command, disabled = false, onConfigured, installation }) {
  const boundTarget = installation?.command === command && /^[a-z0-9][a-z0-9_-]{0,63}$/.test(installation.target || '')
    ? installation.target : undefined
  const target = extensionSetupTarget(command) || boundTarget
  const [revision, setRevision] = useState(0)
  const [groups, setGroups] = useState([])
  const [values, setValues] = useState({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const controller = useRef(null)
  const resume = useRef(onConfigured)
  resume.current = onConfigured
  useEffect(() => {
    const request = new AbortController()
    controller.current = request
    let alive = true
    setValues({}); setGroups([]); setError(''); setBusy(false)
    if (!target) return () => { alive = false; request.abort() }
    const timeout = setTimeout(() => request.abort(), 15000)
    fetch(`/api/extensions/${target}/install-plan`, {signal: request.signal, cache: 'no-store'})
      .then(async response => {
        if (!response.ok) throw new Error('plan')
        const fields = requiredFields(await response.json(), target)
        if (alive) setGroups(fields)
      }).catch(() => { if (alive) setError('Could not check extension configuration.') })
      .finally(() => clearTimeout(timeout))
    return () => { alive = false; clearTimeout(timeout); request.abort(); if (controller.current === request) controller.current = null }
  }, [target, revision])

  if (!target || (!groups.length && !error)) return null
  async function save(event) {
    event.preventDefault()
    if (disabled || busy) return
    const request = controller.current
    const submitted = groups.map(group => ({id: group.id, values: Object.fromEntries(
      group.fields.map(field => [field.key, values[`${group.id}/${field.key}`] || '']))}))
    if (submitted.some(group => Object.values(group.values).some(value => !value))) return
    setBusy(true); setError(''); setValues({})
    const timeout = setTimeout(() => request.abort(), 60000)
    try {
      for (const group of submitted) {
        if (request.signal.aborted || controller.current !== request) return
        const response = await fetch(`/api/extensions/${group.id}/configure`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({values: group.values}), signal: request.signal, cache: 'no-store',
        })
        if (!response.ok) throw new Error('save')
        const receipt = await response.json()
        if (receipt.service_id !== group.id || receipt.status !== 'saved' || !Array.isArray(receipt.saved_keys) ||
            JSON.stringify([...receipt.saved_keys].sort()) !== JSON.stringify(Object.keys(group.values).sort())) throw new Error('save')
      }
      if (request.signal.aborted || controller.current !== request) return
      const response = await fetch(`/api/extensions/${target}/install-plan`, {signal: request.signal, cache: 'no-store'})
      if (!response.ok) throw new Error('plan')
      const remaining = requiredFields(await response.json(), target)
      if (controller.current !== request || request.signal.aborted) return
      setGroups(remaining)
      if (!remaining.length) resume.current?.()
    } catch {
      if (controller.current === request) setError('Save could not be confirmed. Recheck configuration before entering values again.')
    } finally {
      clearTimeout(timeout)
      if (controller.current === request) setBusy(false)
    }
  }
  return <form className="portal-extension-setup" onSubmit={save} aria-label="Extension configuration" autoComplete="off">
    <strong>Configure @{target}</strong>
    <p>These values go directly to ODS settings, outside the conversation.</p>
    {groups.map(group => <fieldset key={group.id} disabled={disabled || busy || Boolean(error)}>
      <legend>{group.id}</legend>
      {group.fields.map(field => <label key={field.key}>
        <span>{field.key}</span>
        {typeof field.description === 'string' && <small>{field.description.slice(0, 500)}</small>}
        <input type={field.secret ? 'password' : 'text'} required maxLength={4096} autoComplete="off"
          spellCheck={false} value={values[`${group.id}/${field.key}`] || ''}
          onChange={event => setValues(current => ({...current, [`${group.id}/${field.key}`]: event.target.value}))}/>
      </label>)}
    </fieldset>)}
    {error && <p role="alert">{error}</p>}
    {error ? <button type="button" disabled={busy || disabled} onClick={() => setRevision(n => n + 1)}>Recheck configuration</button>
      : <button type="submit" disabled={busy || disabled}>{busy ? 'Saving…' : 'Save and continue installation'}</button>}
  </form>
}
