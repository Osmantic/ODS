import { useEffect, useRef, useState } from 'react'
import usePixelProviderRuntime from './usePixelProviderRuntime'
import PixelAdviceRuntime from '../PixelAdviceRuntime'

const button = 'rounded border border-theme-border px-3 py-2 text-sm disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-blue-500'
const labels = { apply: 'Apply saved providers', deactivate: 'Deactivate managed providers', recover: 'Recover interrupted provider change' }

function ProviderConfirmation({ confirmation, onCancel, onConfirm }) {
  const cancel = useRef(null)
  const [cloudAccepted, setCloudAccepted] = useState(false)
  const cloud = confirmation.operation === 'apply' && confirmation.allowCloud
  useEffect(() => {
    cancel.current?.focus()
    return () => {
      if (confirmation.trigger.isConnected && !confirmation.trigger.disabled) confirmation.trigger.focus()
    }
  }, [confirmation])
  return <div role="dialog" aria-labelledby="pixel-provider-confirm-title" aria-describedby="pixel-provider-confirm-description"
    className="rounded-lg border border-theme-border p-4 space-y-3"
    onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onCancel() } }}>
    <h4 id="pixel-provider-confirm-title" className="font-medium">Confirm provider {confirmation.operation}</h4>
    <p id="pixel-provider-confirm-description" className="text-sm">{confirmation.operation === 'apply'
      ? `Apply saved provider revision ${confirmation.savedRevision}? Pixel may restart when idle. Tool access and sandbox mode will not change.`
      : confirmation.operation === 'deactivate'
        ? 'Restore the original inference configuration? Saved provider settings and keys will be retained. Pixel may restart when idle.'
        : 'Recover this interrupted change? The controller will finish a verified change or restore its recorded prior configuration. It may restore an older provider revision; saved edits are retained.'}</p>
    {cloud && <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={cloudAccepted}
      onChange={event => setCloudAccepted(event.target.checked)} />I understand this saved policy permits requests to configured cloud providers and may incur provider charges.</label>}
    <div className="flex flex-wrap gap-3">
      <button type="button" className={button} ref={cancel} onClick={onCancel}>Cancel</button>
      <button type="button" className={button} disabled={cloud && !cloudAccepted}
        onClick={() => onConfirm(cloudAccepted)}>Confirm and {confirmation.operation}</button>
    </div>
  </div>
}

export default function PixelProviderRuntime({ savedRevision, saving, blocked, routingEnabled, allowCloud, onBusyChange }) {
  const { runtime, running, stale, error, notice, stage, inspect, change } = usePixelProviderRuntime({ savedRevision, saving, blocked, onBusyChange })
  const [confirmation, setConfirmation] = useState(null)
  const [workerReady, setWorkerReady] = useState(false)
  const consent = useRef(null)
  const idle = !running && !saving && !stale
  const matched = runtime?.providerRevision === savedRevision && Number.isSafeInteger(savedRevision)
  const eligible = {
    apply: idle && workerReady && !blocked && routingEnabled && matched && ['not-applied', 'inactive', 'saved-changes'].includes(runtime?.status),
    deactivate: idle && !blocked && matched && ['applied', 'saved-changes'].includes(runtime?.status),
    recover: idle && runtime?.status === 'pending',
  }
  const valid = confirmation && confirmation.runtime === runtime && confirmation.savedRevision === savedRevision &&
    confirmation.allowCloud === allowCloud && eligible[confirmation.operation]
  useEffect(() => {
    if (confirmation && !valid) { consent.current = null; setConfirmation(null) }
  }, [confirmation, valid])
  const open = (operation, trigger) => {
    if (consent.current || !eligible[operation]) return
    const next = { operation, runtime, savedRevision, allowCloud, trigger }
    consent.current = next
    setConfirmation(next)
  }
  const close = () => { consent.current = null; setConfirmation(null) }
  const submit = cloudAccepted => {
    if (!valid || consent.current !== confirmation ||
      (confirmation.operation === 'apply' && confirmation.allowCloud && cloudAccepted !== true)) return
    close()
    void change(confirmation.operation)
  }
  const descriptions = {
    'not-applied': 'Managed providers have not been applied.',
    inactive: 'Managed providers are inactive. The original inference configuration is restored and registered.',
    applied: `Saved provider revision ${runtime?.providerRevision} is registered in the current Pixel runtime.`,
    'saved-changes': `Provider revision ${runtime?.binding?.revision} is registered; saved revision ${runtime?.providerRevision} is not applied.`,
    pending: 'A provider change is incomplete. Inspect and recover it before another change.',
    unavailable: runtime?.reason === 'settings-store-not-initialized'
      ? 'Save provider settings once to initialize runtime controls. Saving does not apply them.'
      : 'Provider runtime control is unavailable on this installation. Saving provider settings remains separate.',
  }
  return <section aria-labelledby="pixel-provider-runtime-title" className="space-y-3 min-w-0 rounded-lg border border-theme-border p-4">
    <h3 id="pixel-provider-runtime-title" className="font-medium">Provider runtime</h3>
    <p className="text-sm text-theme-text-muted">Save keeps your desired configuration. Apply changes the current agent’s inference routing, not its tool permissions.</p>
    <PixelAdviceRuntime title="Provider worker runtime" onReadyChange={setWorkerReady} disabled={Boolean(running) || saving} />
    {!workerReady && <p className="text-sm">Prepare or repair the private worker runtime before Apply. Deactivate and recovery do not require it.</p>}
    <div aria-live="polite" className="space-y-2 text-sm break-words">
      {stage && <p role="status">{stage}</p>}
      {!running && stale && <p>Provider runtime status is unknown or stale. Refresh before changing it.</p>}
      {!stale && runtime && <p>{descriptions[runtime.status]}</p>}
      {!stale && runtime?.reason && <p>Status code: {runtime.reason}</p>}
      {!stale && runtime?.lastVerifiedAt && <p>Last registration verification: {runtime.lastVerifiedAt}</p>}
      {!stale && runtime?.registrationVerified && <p>Registration is verified. Model connectivity, answer quality, and fallback behavior are not verified by this status.</p>}
      {!stale && runtime?.binding && <p>Active policy permits cloud routing: {runtime.binding.allowCloud ? 'Yes' : 'No'}.</p>}
      {!stale && runtime && !['unavailable', 'pending'].includes(runtime.status) && savedRevision !== null && !matched &&
        <p>Saved providers and runtime inspection differ. Reload providers and refresh runtime status.</p>}
      {blocked && <p>Save or cancel edits and reload stale settings before Apply or Deactivate. Recovery does not discard your edits.</p>}
      {!routingEnabled && <p>Enable desired routing and save before Apply. Deactivate restores the original inference configuration.</p>}
      {error && <p role="alert" className="text-red-600">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </div>
    <div className="flex flex-wrap gap-2">
      <button type="button" className={button} disabled={Boolean(running) || saving} onClick={inspect}>Refresh provider runtime</button>
      {Object.entries(labels).map(([operation, label]) => <button type="button" key={operation} className={button}
        disabled={!eligible[operation]} onClick={event => open(operation, event.currentTarget)}>{label}</button>)}
    </div>
    {valid && <ProviderConfirmation confirmation={confirmation} onCancel={close} onConfirm={submit} />}
  </section>
}
