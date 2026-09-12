import { AlertTriangle, CheckCircle2, Clock3, Loader2, ShieldCheck, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  approveExtensionTransaction,
  configureExtensionTransaction,
  executeExtensionTransaction,
  getExtensionTransaction,
  getExtensionTransactionConfiguration,
} from '../lib/extensionTransactions'

const TERMINAL_STATES = new Set(['committed', 'rolled_back', 'failed', 'manual_recovery_required'])
const WORKING_STATES = new Set(['reserved', 'downloading', 'staged', 'configuring', 'applying', 'verifying', 'reconciling'])

const LABELS = {
  planned: 'Proposed',
  awaiting_approval: 'Awaiting approval',
  approved: 'Approved',
  reserved: 'Reserving resources',
  downloading: 'Downloading',
  staged: 'Staged',
  configuring: 'Configuring',
  applying: 'Applying',
  verifying: 'Verifying',
  committed: 'Succeeded',
  failed: 'Failed',
  reconciling: 'Recovering',
  rolled_back: 'Rolled back',
  manual_recovery_required: 'Manual recovery required',
}

const errorLabel = code => ({
  'owner-session-required': 'An owner browser session is required to approve this exact plan.',
  'missing-configuration': 'Complete the required configuration before approval.',
  'plan-expired': 'This plan has expired. Close it and request a fresh plan.',
  'transaction-request-timeout': 'The request timed out. Refresh the transaction status before retrying.',
  'transaction-request-unavailable': 'The transaction service is temporarily unavailable.',
}[code] || `The transaction was rejected (${code || 'unknown-error'}).`)

const bytes = value => {
  if (!Number.isSafeInteger(value) || value < 0) return 'Unknown'
  if (value === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`
}

const stateTone = state => {
  if (state === 'committed') return 'border-green-500/25 bg-green-500/10 text-green-300'
  if (state === 'failed' || state === 'manual_recovery_required') return 'border-red-500/25 bg-red-500/10 text-red-300'
  if (state === 'rolled_back') return 'border-orange-500/25 bg-orange-500/10 text-orange-300'
  if (WORKING_STATES.has(state)) return 'border-blue-500/25 bg-blue-500/10 text-blue-300'
  return 'border-amber-500/25 bg-amber-500/10 text-amber-300'
}

const StateIcon = ({ state }) => {
  if (state === 'committed') return <CheckCircle2 size={14} aria-hidden="true" />
  if (state === 'failed' || state === 'manual_recovery_required') return <AlertTriangle size={14} aria-hidden="true" />
  if (WORKING_STATES.has(state)) return <Loader2 size={14} className="animate-spin" aria-hidden="true" />
  return <Clock3 size={14} aria-hidden="true" />
}

const unique = values => [...new Set(values.filter(value => typeof value === 'string' && value))].sort()

const impactSummary = definitions => ({
  ports: unique(definitions.flatMap(item => item.resources?.hostPorts?.map(port => `${port.port}/${port.protocol}`) || [])),
  volumes: unique(definitions.flatMap(item => item.resources?.volumes || [])),
  networks: unique(definitions.flatMap(item => item.resources?.networks || [])),
  devices: unique(definitions.flatMap(item => item.resources?.devices || [])),
  permissions: unique(definitions.flatMap(item => [
    ...(item.resources?.hostPermissions || []),
    ...(item.resources?.linuxCapabilities || []),
  ])),
})

const SummaryList = ({ title, values, empty = 'None declared' }) => (
  <div>
    <dt className="text-[9px] uppercase tracking-[0.14em] text-theme-text-muted/55">{title}</dt>
    <dd className="mt-1 text-[11px] text-theme-text-secondary break-words">{values.length ? values.join(', ') : empty}</dd>
  </div>
)

const PlanReview = ({ plan, planHash }) => {
  const definitions = Array.isArray(plan.definitions) ? plan.definitions : []
  const impacts = useMemo(() => impactSummary(definitions), [definitions])
  const requested = new Set(plan.requestedServices || [])
  const resources = plan.resourceDelta || {}
  const dependencyReason = definition => {
    if (requested.has(definition.id)) return 'Requested by you'
    const capabilities = (plan.providerBindings || [])
      .filter(binding => binding.serviceId === definition.id)
      .map(binding => binding.capability)
    if (capabilities.length) return `Provides ${capabilities.join(', ')}`
    const dependents = definitions
      .filter(candidate => (candidate.dependsOn || []).includes(definition.id))
      .map(candidate => candidate.id)
    return dependents.length ? `Required by ${dependents.join(', ')}` : 'Resolved dependency'
  }
  return (
    <section aria-labelledby="transaction-plan-title" className="space-y-4">
      <div>
        <h3 id="transaction-plan-title" className="text-sm font-semibold text-theme-text">Exact extension plan</h3>
        <p className="mt-1 text-[10px] text-theme-text-muted/65">Review the computed dependencies and impact. Approval is bound only to this SHA-256.</p>
        <code className="mt-2 block rounded-lg border border-theme-border/60 bg-theme-bg/60 px-3 py-2 text-[10px] text-theme-text-secondary break-all">{planHash}</code>
      </div>

      <div className="space-y-2">
        {definitions.map(definition => (
          <article key={definition.id} className="rounded-lg border border-theme-border/60 bg-theme-bg/35 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-semibold text-theme-text">{definition.id} <span className="font-normal text-theme-text-muted">v{definition.version}</span></span>
              <span className="rounded-full border border-theme-border px-2 py-0.5 text-[9px] uppercase tracking-[0.12em] text-theme-text-muted">{requested.has(definition.id) ? 'Requested' : 'Dependency'}</span>
            </div>
            <p className="mt-1 text-[10px] text-theme-text-secondary">{dependencyReason(definition)}</p>
            <p className="mt-1 text-[10px] text-theme-text-secondary">{definition.trust?.tier || 'unknown'} trust · {definition.trust?.publisher || 'publisher not declared'}</p>
          </article>
        ))}
      </div>

      <dl className="grid grid-cols-2 gap-3 rounded-lg border border-theme-border/60 p-3 sm:grid-cols-3">
        <SummaryList title="Download" values={[bytes(resources.downloadBytes)]} />
        <SummaryList title="Disk" values={[bytes(resources.diskBytes)]} />
        <SummaryList title="Runtime RAM" values={[bytes(resources.ramBytes)]} />
        <SummaryList title="VRAM" values={[bytes(resources.vramBytes)]} />
        <SummaryList title="CPU" values={[Number.isSafeInteger(resources.cpuMillicores) ? `${resources.cpuMillicores} millicores` : 'Unknown']} />
        <SummaryList title="GPU allocation" values={[Number.isSafeInteger(resources.gpuCount) ? `${resources.gpuCount}` : 'Unknown']} />
      </dl>

      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <SummaryList title="Host ports" values={impacts.ports} />
        <SummaryList title="Permissions" values={impacts.permissions} />
        <SummaryList title="Volumes" values={impacts.volumes} />
        <SummaryList title="Networks and devices" values={[...impacts.networks, ...impacts.devices]} />
        <SummaryList title="Data effects" values={(plan.dataEffects || []).map(item => item.serviceId)} empty="No persistent-data effect declared" />
        <SummaryList title="Rollback" values={(plan.rollbackEffects || []).map(item => `${item.serviceId}: ${item.contract}`)} />
      </dl>

      {(plan.warnings || []).length > 0 && (
        <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] p-3 text-[11px] text-amber-200" role="note">
          <p className="font-semibold">Warnings</p>
          <ul className="mt-1 list-disc space-y-1 pl-4">{plan.warnings.map((warning, index) => <li key={`${warning.code}-${index}`}>{warning.code}</li>)}</ul>
        </div>
      )}
      <p className="text-[10px] text-theme-text-muted">Plan expires {plan.validUntil}.</p>
    </section>
  )
}

const Field = ({ field, defaultValue }) => {
  const id = `transaction-config-${field.key}`
  const hint = `${id}-hint`
  const disabled = field.source !== 'user'
  const common = {
    id,
    name: field.key,
    disabled,
    required: field.required && field.source === 'user' && field.type !== 'boolean',
    'aria-describedby': field.secret ? hint : undefined,
    className: 'mt-1 min-h-10 w-full rounded-lg border border-theme-border bg-theme-bg/70 px-3 py-2 text-sm text-theme-text outline-none focus:border-theme-accent/60 disabled:opacity-50',
  }
  let control
  if (field.type === 'enum') {
    control = <select {...common} defaultValue={defaultValue ?? field.default ?? ''}><option value="">Select a value</option>{(field.validation?.choices || []).map(choice => <option key={choice} value={choice}>{choice}</option>)}</select>
  } else if (field.type === 'boolean') {
    control = <input {...common} className="mt-2 h-5 w-5 rounded border-theme-border" type="checkbox" defaultChecked={defaultValue ?? field.default ?? false} />
  } else {
    control = <input
      {...common}
      type={field.secret ? 'password' : field.type === 'integer' ? 'number' : field.type === 'url' ? 'url' : 'text'}
      defaultValue={field.secret ? '' : defaultValue ?? field.default ?? ''}
      autoComplete={field.secret ? 'new-password' : 'off'}
      minLength={field.validation?.minLength}
      maxLength={field.validation?.maxLength}
      min={field.validation?.minimum}
      max={field.validation?.maximum}
      step={field.type === 'integer' ? 1 : undefined}
    />
  }
  return (
    <label htmlFor={id} className="block text-[11px] text-theme-text-secondary">
      <span className="font-medium text-theme-text">{field.key}</span>{field.required && <span className="ml-1 text-amber-300">required</span>}
      {control}
      {field.secret && <span id={hint} className="mt-1 block text-[9px] text-theme-text-muted">Secret value is sent directly to host custody and is not retained by this page.</span>}
      {disabled && <span className="mt-1 block text-[9px] text-theme-text-muted">Managed by ODS; shown for review only.</span>}
    </label>
  )
}

export default function ExtensionTransactionPanel({ proposal, extensionName, onClose, onCommitted }) {
  const [status, setStatus] = useState(() => ({
    transactionId: proposal.transactionId,
    planHash: proposal.planHash,
    state: proposal.state,
    sequence: proposal.sequence,
    plan: proposal.envelope.plan,
    journal: [],
    approval: { approved: false },
  }))
  const [configuration, setConfiguration] = useState(null)
  const [busy, setBusy] = useState('loading')
  const [error, setError] = useState(null)
  const formRef = useRef(null)
  const dialogRef = useRef(null)
  const mounted = useRef(true)
  const actionInFlight = useRef(null)

  const refresh = async () => {
    const next = await getExtensionTransaction(proposal.transactionId, proposal.planHash)
    if (mounted.current) setStatus(next)
    return next
  }

  useEffect(() => {
    mounted.current = true
    Promise.all([refresh(), getExtensionTransactionConfiguration(proposal.transactionId, proposal.planHash)])
      .then(([, nextConfiguration]) => {
        if (mounted.current) setConfiguration(nextConfiguration)
      })
      .catch(caught => { if (mounted.current) setError(errorLabel(caught.code)) })
      .finally(() => { if (mounted.current) setBusy(null) })
    return () => { mounted.current = false }
  }, [proposal.transactionId])

  useEffect(() => {
    if (!WORKING_STATES.has(status.state)) return undefined
    const timer = setInterval(() => {
      refresh().catch(caught => { if (mounted.current) setError(errorLabel(caught.code)) })
    }, 1500)
    return () => clearInterval(timer)
  }, [status.state])

  const requiredConfiguration = (status.plan.requiredConfigKeys || []).length > 0 || (status.plan.requiredSecretKeys || []).length > 0
  const readyForApproval = !requiredConfiguration || configuration?.configured === true

  const submitConfiguration = async event => {
    event.preventDefault()
    if (actionInFlight.current) return
    actionInFlight.current = 'configuration'
    setBusy('configuration')
    setError(null)
    const form = formRef.current
    const values = {}
    const secretValues = {}
    try {
      if (!form) throw new Error('configuration-form-unavailable')
      for (const field of configuration.fields) {
        if (field.source !== 'user') continue
        const input = form.elements.namedItem(field.key)
        let value = field.type === 'boolean' ? input.checked : input.value
        if (field.type === 'integer' && value !== '') {
          value = Number(value)
          if (!Number.isSafeInteger(value)) {
            const invalidInteger = new Error('invalid-integer-value')
            invalidInteger.code = 'invalid-integer-value'
            throw invalidInteger
          }
        }
        if (value === '' && !field.required) continue
        if (field.secret) secretValues[field.key] = value
        else values[field.key] = value
      }
      const next = await configureExtensionTransaction(status.transactionId, status.planHash, configuration.schemaHash, values, secretValues)
      for (const field of configuration.fields.filter(item => item.secret)) {
        const input = form.elements.namedItem(field.key)
        if (input) input.value = ''
      }
      if (mounted.current) setConfiguration(next)
    } catch (caught) {
      for (const field of configuration.fields.filter(item => item.secret)) {
        const input = form?.elements.namedItem(field.key)
        if (input) input.value = ''
      }
      if (mounted.current) setError(errorLabel(caught.code))
    } finally {
      Object.keys(secretValues).forEach(key => { secretValues[key] = '' })
      actionInFlight.current = null
      if (mounted.current) setBusy(null)
    }
  }

  const approve = async () => {
    if (actionInFlight.current) return
    if (Date.parse(status.plan.validUntil) <= Date.now()) {
      setError(errorLabel('plan-expired'))
      return
    }
    actionInFlight.current = 'approval'
    setBusy('approval')
    setError(null)
    try {
      await approveExtensionTransaction(status.transactionId, status.planHash)
      await refresh()
    } catch (caught) {
      if (mounted.current) setError(errorLabel(caught.code))
    } finally {
      actionInFlight.current = null
      if (mounted.current) setBusy(null)
    }
  }

  const execute = async () => {
    if (actionInFlight.current) return
    if (Date.parse(status.plan.validUntil) <= Date.now()) {
      setError(errorLabel('plan-expired'))
      return
    }
    actionInFlight.current = 'execution'
    setBusy('execution')
    setError(null)
    try {
      const pending = executeExtensionTransaction(status.transactionId, status.planHash)
      setStatus(current => ({ ...current, state: 'applying' }))
      const result = await pending
      const next = await refresh()
      if (result.finalState === 'committed' && next.state === 'committed') onCommitted?.()
    } catch (caught) {
      if (mounted.current) {
        setError(errorLabel(caught.code))
        await refresh().catch(() => {})
      }
    } finally {
      actionInFlight.current = null
      if (mounted.current) setBusy(null)
    }
  }

  const configured = configuration?.configured === true
  const canClose = !busy && !WORKING_STATES.has(status.state)
  const handleDialogKeyDown = event => {
    if (event.key === 'Escape' && canClose) {
      event.preventDefault()
      onClose()
      return
    }
    if (event.key !== 'Tab') return
    const focusable = [...(dialogRef.current?.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])') || [])]
    if (!focusable.length) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-3" role="presentation">
      <div ref={dialogRef} onKeyDown={handleDialogKeyDown} className="max-h-[94vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-theme-border bg-theme-card p-5 shadow-2xl" role="dialog" aria-modal="true" aria-labelledby="extension-transaction-title">
        <div className="mb-5 flex items-start justify-between gap-3">
          <div>
            <h2 id="extension-transaction-title" className="text-lg font-semibold text-theme-text">{extensionName} change</h2>
            <div className={`mt-2 inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.1em] ${stateTone(status.state)}`} role="status" aria-live="polite">
              <StateIcon state={status.state} />{LABELS[status.state] || status.state}
            </div>
          </div>
          <button type="button" onClick={onClose} disabled={!canClose} autoFocus aria-label="Close extension plan" className="rounded-lg p-2 text-theme-text-muted hover:bg-theme-surface-hover hover:text-theme-text disabled:opacity-40"><X size={18} /></button>
        </div>

        {error && <div className="mb-4 rounded-lg border border-red-500/25 bg-red-500/10 p-3 text-[11px] text-red-200" role="alert">{error}</div>}
        <PlanReview plan={status.plan} planHash={status.planHash} />

        <section className="mt-6 border-t border-theme-border/70 pt-5" aria-labelledby="transaction-config-title">
          <h3 id="transaction-config-title" className="text-sm font-semibold text-theme-text">Configuration</h3>
          {busy === 'loading' && <p className="mt-2 flex items-center gap-2 text-[11px] text-theme-text-muted"><Loader2 size={12} className="animate-spin" /> Loading the plan-bound schema…</p>}
          {configuration && configuration.fields.length === 0 && <p className="mt-2 text-[11px] text-theme-text-muted">No configuration is required for this plan.</p>}
          {configuration && configuration.fields.length > 0 && (
            <form ref={formRef} onSubmit={submitConfiguration} autoComplete="off" className="mt-3 space-y-3">
              <fieldset disabled={configured || status.state !== 'awaiting_approval' || Boolean(busy)} className="space-y-3">
                {configuration.fields.map(field => <Field key={field.key} field={field} defaultValue={configuration.values[field.key]} />)}
              </fieldset>
              <div className="flex flex-wrap items-center gap-3">
                <button type="submit" disabled={configured || status.state !== 'awaiting_approval' || Boolean(busy)} className="min-h-10 rounded-lg bg-theme-accent px-4 py-2 text-[10px] font-semibold uppercase tracking-[0.1em] text-white disabled:opacity-45">
                  {busy === 'configuration' ? 'Saving…' : configured ? 'Configuration secured' : 'Validate and secure configuration'}
                </button>
                {configuration.presentSecretKeys.length > 0 && <span className="text-[10px] text-theme-text-muted">Host-held secrets present: {configuration.presentSecretKeys.join(', ')}</span>}
              </div>
            </form>
          )}
        </section>

        <section className="mt-6 border-t border-theme-border/70 pt-5" aria-labelledby="transaction-approval-title">
          <div className="flex items-start gap-2">
            <ShieldCheck size={17} className="mt-0.5 text-theme-accent-light" aria-hidden="true" />
            <div>
              <h3 id="transaction-approval-title" className="text-sm font-semibold text-theme-text">Owner approval and execution</h3>
              <p className="mt-1 text-[10px] text-theme-text-muted">Approval cannot change the plan, grant broader authority, or execute a different hash.</p>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {status.state === 'awaiting_approval' && (
              <button type="button" onClick={approve} disabled={!readyForApproval || Boolean(busy)} className="min-h-10 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-[10px] font-semibold uppercase tracking-[0.1em] text-amber-200 disabled:opacity-45">
                {busy === 'approval' ? 'Approving…' : 'Approve exact plan'}
              </button>
            )}
            {status.state === 'approved' && (
              <button type="button" onClick={execute} disabled={Boolean(busy)} className="min-h-10 rounded-lg bg-theme-accent px-4 py-2 text-[10px] font-semibold uppercase tracking-[0.1em] text-white disabled:opacity-45">Apply approved plan</button>
            )}
            {!readyForApproval && status.state === 'awaiting_approval' && <span className="self-center text-[10px] text-amber-300">Required configuration must be secured first.</span>}
          </div>
        </section>

        {(status.journal || []).length > 0 && (
          <details className="mt-5 rounded-lg border border-theme-border/60 p-3 text-[10px] text-theme-text-secondary">
            <summary className="cursor-pointer font-semibold text-theme-text">Transaction history</summary>
            <ol className="mt-2 space-y-1">{status.journal.map(entry => <li key={entry.sequence}>{entry.sequence}. {LABELS[entry.state] || entry.state}</li>)}</ol>
          </details>
        )}
        {TERMINAL_STATES.has(status.state) && <p className="mt-4 text-[11px] text-theme-text-secondary">This transaction ended in: {LABELS[status.state] || status.state}.</p>}
      </div>
    </div>
  )
}
