import { AlertTriangle } from 'lucide-react'
import { useEffect, useRef } from 'react'

const STATUS_DOTS = {
  enabled: 'bg-green-500',
  disabled: 'bg-theme-border',
  not_installed: 'bg-theme-border',
  incompatible: 'bg-orange-500',
  unknown: 'bg-theme-border',
}

/**
 * DependencyBadges — renders "Requires: X, Y" with colored status dots.
 * Place below the card description in ExtensionCard.
 */
export function DependencyBadges({ dependsOn, dependencyStatus }) {
  if (!dependsOn || dependsOn.length === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
      <span className="text-[10px] text-theme-text-muted">Requires:</span>
      {dependsOn.map(dep => {
        const status = dependencyStatus?.[dep] || 'unknown'
        const dotColor = STATUS_DOTS[status] || STATUS_DOTS.unknown
        return (
          <span
            key={dep}
            className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-theme-card/80 text-theme-text-muted"
            title={`${dep}: ${status}`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
            {dep}
          </span>
        )
      })}
    </div>
  )
}

/**
 * DependencyConfirmDialog — modal for auto-enable confirmation.
 * Shows when enabling a service that has missing dependencies.
 */
export function DependencyConfirmDialog({ ext, missingDeps, onConfirm, onCancel }) {
  const dialog = useRef(null), cancel = useRef(null)
  const visible = Boolean(ext && missingDeps?.length)
  useEffect(() => {
    if (!visible) return
    const previous = document.activeElement
    const element = dialog.current
    element.showModal()
    cancel.current?.focus()
    return () => { element.close(); if (previous?.isConnected) previous.focus() }
  }, [visible])
  if (!ext || !missingDeps || missingDeps.length === 0) return null

  return (
    <dialog ref={dialog} aria-label="Enable dependencies"
      className="fixed inset-0 m-0 h-screen w-screen max-h-none max-w-none border-0 p-0 bg-black/50 flex items-center justify-center z-50"
      onCancel={event => { event.preventDefault(); onCancel() }} onClick={onCancel}>
      <div
        className="bg-theme-card border border-theme-border rounded-xl p-6 max-w-md mx-4"
        onClick={e => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-theme-text mb-2">
          Enable Dependencies
        </h3>
        <p className="text-sm text-theme-text-muted mb-3">
          Enabling <span className="text-theme-text font-medium">{ext.name}</span> will also enable:
        </p>
        <div className="flex flex-wrap gap-2 mb-4">
          {missingDeps.map(dep => (
            <span
              key={dep}
              className="text-xs px-2 py-1 rounded bg-theme-accent/10 text-theme-accent-light border border-theme-accent/20"
            >
              {dep}
            </span>
          ))}
        </div>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            ref={cancel}
            className="px-4 py-2 text-sm text-theme-text-muted hover:text-theme-text transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 text-sm rounded-lg bg-theme-accent/20 text-theme-accent-light hover:bg-theme-accent/30 transition-colors"
          >
            Enable All
          </button>
        </div>
      </div>
    </dialog>
  )
}

/**
 * DisableDependentWarning — orange warning banner shown when disabling
 * a service that has active dependents.
 */
export function DisableDependentWarning({ dependents }) {
  if (!dependents || dependents.length === 0) return null

  return (
    <div className="flex items-start gap-2 mt-3 p-2.5 rounded-lg bg-orange-500/10 border border-orange-500/20">
      <AlertTriangle size={14} className="text-orange-400 mt-0.5 shrink-0" />
      <p className="text-xs text-orange-300">
        Disabling this may break: {dependents.join(', ')}
      </p>
    </div>
  )
}
