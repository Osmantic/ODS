import { useEffect, useRef, useState } from 'react'
import usePixelSettingsRuntime from './usePixelSettingsRuntime'

function SettingsConfirmation({ confirmation, onCancel, onConfirm }) {
  const cancel = useRef(null)
  useEffect(() => {
    cancel.current?.focus()
    return () => {
      if (confirmation.trigger.isConnected && !confirmation.trigger.disabled) confirmation.trigger.focus()
    }
  }, [confirmation])
  const applying = confirmation.operation === 'apply'
  return <div role="dialog" aria-labelledby="pixel-settings-confirm-title" aria-describedby="pixel-settings-confirm-description"
    className="rounded-lg border border-theme-border p-4 space-y-3"
    onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onCancel() } }}>
    <h4 id="pixel-settings-confirm-title" className="font-medium">{applying ? 'Confirm saved preferences' : 'Confirm settings recovery'}</h4>
    <p id="pixel-settings-confirm-description" className="text-sm">{applying
      ? `Apply saved revision ${confirmation.savedRevision}? Pixel will restart only when idle. Existing access mode will be preserved.`
      : 'Recover this interrupted change? The controller will finish a verified change or restore the previous configuration; Pixel may restart.'}</p>
    <div className="flex flex-wrap gap-3">
      <button type="button" ref={cancel} onClick={onCancel} className="rounded border border-theme-border px-3 py-2 text-sm">Cancel</button>
      <button type="button" onClick={onConfirm} className="rounded border border-theme-border px-3 py-2 text-sm">{applying ? 'Confirm and apply' : 'Confirm and recover'}</button>
    </div>
  </div>
}

export default function PixelSettingsRuntime({ savedRevision, saving, blocked, onBusyChange }) {
  const { runtime, running, stale, error, notice, stage, inspect, change } = usePixelSettingsRuntime({ savedRevision, saving, blocked, onBusyChange })
  const [confirmation, setConfirmation] = useState(null)
  const consent = useRef(null)
  const canApply = !running && !saving && !blocked && !stale &&
    ['not-applied', 'saved-changes', 'restored'].includes(runtime?.status) && runtime.settingsRevision === savedRevision
  const canRecover = !running && !saving && !stale && runtime?.status === 'pending'
  const confirmationValid = confirmation && confirmation.savedRevision === savedRevision &&
    confirmation.runtime === runtime && (confirmation.operation === 'apply' ? canApply : canRecover)
  useEffect(() => {
    if (confirmation && !confirmationValid) { consent.current = null; setConfirmation(null) }
  }, [confirmation, confirmationValid])
  const caps = !stale && runtime?.capabilities
  const mismatch = runtime && runtime.status !== 'unavailable' && runtime.status !== 'pending' &&
    savedRevision !== null && runtime.settingsRevision !== savedRevision
  const descriptions = {
    'not-applied': 'No current applied-settings verification is available.',
    restored: 'Previous runtime configuration is restored and verified. Saved preferences are not applied.',
    applied: `Saved revision ${runtime?.settingsRevision} is applied and verified.`,
    'saved-changes': `Runtime revision ${runtime?.appliedRevision} is verified; saved revision ${runtime?.settingsRevision} is not applied.`,
    pending: 'A settings change is incomplete. Inspect and recover it before applying another change.',
    unavailable: runtime?.reason === 'settings-store-not-initialized'
      ? 'Save Pixel preferences once to initialize runtime controls. Saving does not apply them.'
      : 'Runtime control is unavailable on this installation. Saving preferences is still available.',
  }
  const confirmChange = (operation, trigger) => {
    if (consent.current || !(operation === 'apply' ? canApply : canRecover)) return
    const next = { operation, savedRevision, runtime, trigger }
    consent.current = next
    setConfirmation(next)
  }
  const cancelConfirmation = () => { consent.current = null; setConfirmation(null) }
  const submitConfirmation = () => {
    if (!confirmationValid || consent.current !== confirmation) return
    cancelConfirmation()
    void change(confirmation.operation)
  }
  return (
    <section aria-labelledby="pixel-settings-runtime-status-title" className="space-y-3 min-w-0 rounded-lg border border-theme-border p-4">
      <h3 id="pixel-settings-runtime-status-title" className="font-medium">Apply saved preferences</h3>
      <p className="text-sm text-theme-text-muted">Applying restarts an idle Pixel with the saved revision. It does not change sandbox or Full Access mode.</p>
      <div aria-live="polite" className="text-sm space-y-2 break-words">
        {stage && <p role="status">{stage}</p>}
        {!running && stale && <p>Runtime status is unknown or stale. Refresh before changing it.</p>}
        {!stale && runtime && <p>{descriptions[runtime.status]}</p>}
        {!stale && runtime?.reason && <p className="text-theme-text-muted">Status code: {runtime.reason}</p>}
        {!stale && runtime?.lastVerifiedAt && <p>Last runtime verification: {runtime.lastVerifiedAt}</p>}
        {mismatch && <p>Saved preferences and runtime inspection differ. Reload preferences and refresh runtime status.</p>}
        {blocked && <p>Save or cancel edits and reload stale preferences before applying. Recovery does not discard your edits.</p>}
        {error && <p role="alert" className="text-red-600">{error}</p>}
        {notice && <p role="status">{notice}</p>}
      </div>
      {caps && (
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm break-words">
          <div><dt className="text-theme-text-muted">Declared context / output limits</dt><dd>{caps.providerContextTokens.toLocaleString()} / {caps.providerMaxOutputTokens.toLocaleString()} tokens ({caps.capacitySource})</dd></div>
          <div><dt className="text-theme-text-muted">Configured Pixel context / output caps</dt><dd>{caps.activeContextTokens.toLocaleString()} / {caps.activeMaxOutputTokens.toLocaleString()} tokens</dd></div>
          <div><dt className="text-theme-text-muted">Measured backend capacity</dt><dd>{caps.backendContextTokens === null ? 'Not verified' : `${caps.backendContextTokens.toLocaleString()} tokens`}</dd></div>
          <div><dt className="text-theme-text-muted">Qualified reasoning levels</dt><dd>{caps.supportedThinkingLevels.length ? caps.supportedThinkingLevels.join(', ') : 'Not qualified'}</dd></div>
          <div><dt className="text-theme-text-muted">Sampling controls</dt><dd>{caps.samplingSupported ? 'Supported' : 'Not qualified for Apply'}</dd></div>
        </dl>
      )}
      <div className="flex flex-wrap gap-2">
        <button className="rounded border border-theme-border px-3 py-2 text-sm disabled:opacity-40" disabled={Boolean(running) || saving} onClick={inspect}>Refresh runtime status</button>
        <button className="rounded border border-theme-border px-3 py-2 text-sm disabled:opacity-40" disabled={!canApply} onClick={event => confirmChange('apply', event.currentTarget)}>Apply saved Pixel preferences</button>
        <button className="rounded border border-theme-border px-3 py-2 text-sm disabled:opacity-40" disabled={!canRecover} onClick={event => confirmChange('recover', event.currentTarget)}>Recover interrupted settings change</button>
      </div>
      {confirmationValid && <SettingsConfirmation confirmation={confirmation} onCancel={cancelConfirmation} onConfirm={submitConfirmation} />}
    </section>
  )
}
