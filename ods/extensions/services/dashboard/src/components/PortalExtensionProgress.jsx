import { useEffect, useRef, useState } from 'react'
import { Check, Circle, LoaderCircle } from 'lucide-react'
import { extensionSetupTarget } from './PortalExtensionSetup'
import './portal-extension-setup.css'

const labels = {none: 'Ready', install: 'Waiting to install', enable: 'Waiting to start',
  wait: 'Installing', blocked: 'Needs attention'}

export default function PortalExtensionProgress({command, active = false, projectPath, installation, onStopInstallation, onRecheckInstallation}) {
  const boundTarget = installation && installation.command === command && /^[a-z0-9][a-z0-9_-]{0,63}$/.test(installation.target || '')
    ? installation.target : undefined
  const target = extensionSetupTarget(command) || boundTarget
  const installState = installation && installation.command === command && installation.target === target ? installation.state : undefined
  const canAssociate = installState === 'succeeded'
  const installing = installState === 'pending'
  const mentionedProjects = typeof command === 'string' ? command.match(/Playground\/[A-Za-z0-9][A-Za-z0-9._-]{0,127}/g) || [] : []
  const associationProject = mentionedProjects.some(path => path !== projectPath) ? undefined : projectPath
  const [plan, setPlan] = useState(null)
  const [error, setError] = useState(false)
  const [revision, setRevision] = useState(0)
  const linked = useRef(null)
  const [association, setAssociation] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    let alive = true, timer, timeout
    setPlan(null); setError(false); setAssociation('')
    if (!target) return () => controller.abort()
    async function load() {
      try {
        timeout = setTimeout(() => controller.abort(), 15000)
        const response = await fetch(`/api/extensions/${target}/install-plan`, {signal: controller.signal, cache: 'no-store'})
        if (!response.ok) throw new Error('plan')
        const next = await response.json()
        if (next?.schemaVersion !== 1 || next.extensionId !== target || !Array.isArray(next.steps) ||
            !next.steps.length || next.steps.length > 128 || next.steps.at(-1)?.extensionId !== target ||
            new Set(next.steps.map(s => s?.extensionId)).size !== next.steps.length ||
            next.steps.some(s => !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(s?.extensionId) ||
              !Object.hasOwn(labels, s.action) || !Array.isArray(s.missingConfiguration) ||
              (s.action === 'none' && !['enabled', 'cli_installed'].includes(s.status)) ||
              (s.action === 'wait' && !['installing', 'setting_up'].includes(s.status)))) throw new Error('plan')
        if (!alive) return
        setPlan(next); setError(false)
        if (canAssociate && next.steps.every(s => s.action === 'none') && /^Playground\/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(associationProject || '')) {
          const identity = `${target}/${projectPath}`
          if (linked.current !== identity) {
            try {
              const saved = await fetch(`/api/extensions/${target}/projects`, {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify({project: projectPath}),
                signal: controller.signal, cache: 'no-store'})
              if (!saved.ok) throw new Error('association')
              const receipt = await saved.json()
              if (receipt.extensionId !== target || receipt.scope !== 'project-association' ||
                  !Array.isArray(receipt.projects) || !receipt.projects.includes(projectPath)) throw new Error('association')
              linked.current = identity
            } catch {
              if (alive) setAssociation('Project association could not be confirmed.')
            }
          }
          if (alive && linked.current === identity) setAssociation(`Linked to ${projectPath}`)
        }
        // Sequential reads only. A pending download can outlive the model's
        // reply; observing it must never submit or replay an installation.
        if (active || installing || next.steps.some(s => s.action === 'wait')) timer = setTimeout(load, 5000)
      } catch {
        if (alive) setError(true)
      } finally { clearTimeout(timeout) }
    }
    load()
    return () => { alive = false; clearTimeout(timer); clearTimeout(timeout); controller.abort() }
  }, [target, active, installing, canAssociate, revision, projectPath, associationProject])
  if (!target && installation?.command === command && installation.state === 'reconciliation_required') {
    return <section className="portal-extension-progress" aria-label="Extension installation progress">
      <p role="status">The extension recipe could not be confirmed. The existing request has been preserved.</p>
      {onRecheckInstallation && <button type="button" onClick={onRecheckInstallation}>Recheck installation</button>}
    </section>
  }
  if (!target || (!plan && !error)) return null
  const ready = plan?.steps.filter(s => s.action === 'none').length || 0
  return <section className="portal-extension-progress" aria-label="Extension installation progress">
    <div><strong>@{target}</strong>{plan && !error && <span>{ready}/{plan.steps.length} ready</span>}</div>
    {error ? <p role="status">Current installation status could not be confirmed.</p> : <ul>
      {plan.steps.map(step => <li key={step.extensionId}>
        {step.action === 'none' ? <Check size={14}/> : step.action === 'wait' ? <LoaderCircle size={14}/> : <Circle size={14}/>}
        <span>{step.extensionId}</span><small>{step.missingConfiguration.length ? 'Configuration needed' : labels[step.action]}</small>
      </li>)}
    </ul>}
    {association && <p>{association}</p>}
    {installState === 'failed' && <p role="status">The host reported that this installation attempt failed. Inspect and correct the cause before starting another attempt.</p>}
    {installState === 'reconciliation_required' && <p role="status">Installation needs inspection before continuing. An accepted operation may still be running.</p>}
    {installState === 'reconciliation_required' && onRecheckInstallation && <button type="button" onClick={onRecheckInstallation}>Recheck installation</button>}
    {installState === 'blocked' && <p role="status">Installation cannot continue with the current host or extension state.</p>}
    {installing && onStopInstallation && <button type="button" onClick={onStopInstallation}>Stop further installation steps</button>}
    {!active && <button type="button" onClick={() => setRevision(value => value + 1)}>Refresh status</button>}
  </section>
}
