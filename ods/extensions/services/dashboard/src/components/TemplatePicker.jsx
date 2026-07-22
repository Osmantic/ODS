import { useState } from 'react'
import {
  MessageSquare, Image, Code, Shield, Layers, Package,
  Loader2, X, Check, AlertTriangle, HardDrive,
} from 'lucide-react'

const ICON_MAP = { MessageSquare, Image, Code, Shield, Layers, Package }
const TEMPLATE_APPLY_TIMEOUT_MS = 30 * 60 * 1000

const fetchJson = async (url, options = {}) => {
  const c = new AbortController()
  const t = setTimeout(() => c.abort(), options.timeout || 30000)
  try {
    return await fetch(url, { ...options, signal: c.signal })
  } finally {
    clearTimeout(t)
  }
}

/**
 * TemplatePicker — card grid of templates. Click shows preview.
 *
 * Templates carry a `_status` field set by the parent: 'available',
 * 'in_progress', 'applied', or 'has_errors'. The card renders differently
 * per state and is only clickable in 'available'. Callers that prefer to
 * hide applied templates (Extensions page) can filter them out upstream.
 */
export function TemplatePicker({ templates, onApplied, compact = false }) {
  const [preview, setPreview] = useState(null)

  if (!templates || templates.length === 0) return null

  return (
    <>
      <div className={`grid gap-3 ${compact ? 'grid-cols-1 sm:grid-cols-2' : 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3'}`}>
        {templates.map(tmpl => {
          const Icon = ICON_MAP[tmpl.icon] || Package
          const status = tmpl._status || 'available'
          const inProgress = status === 'in_progress'
          const hasErrors = status === 'has_errors'
          const isApplied = status === 'applied'
          const disabled = inProgress || hasErrors || isApplied

          const cardBase = 'text-left rounded-xl p-4 transition-all group border'
          const cardByStatus = inProgress
            ? 'bg-theme-card border-blue-500/30 cursor-not-allowed opacity-80'
            : hasErrors
            ? 'bg-red-500/5 border-red-500/30 cursor-not-allowed'
            : isApplied
            ? 'bg-green-500/5 border-green-500/30 cursor-not-allowed'
            : 'bg-theme-card border-theme-border hover:border-theme-accent/40 hover:bg-theme-surface-hover'

          return (
            <button
              key={tmpl.id}
              onClick={() => !disabled && setPreview(tmpl)}
              disabled={disabled}
              aria-disabled={disabled}
              className={`${cardBase} ${cardByStatus}`}
            >
              <div className="flex items-center gap-3 mb-2">
                <div className={`p-2 rounded-lg ${hasErrors ? 'bg-red-500/10' : isApplied ? 'bg-green-500/10' : 'bg-theme-accent/10 group-hover:bg-theme-accent/20'} transition-colors`}>
                  {/* Status icons are decorative — the adjacent text label
                      ("Installing…" / "Has errors" / "Applied") carries the
                      semantic meaning, so hide icons from screen readers. */}
                  {inProgress
                    ? <Loader2 size={18} aria-hidden="true" className="animate-spin text-blue-400" />
                    : hasErrors
                    ? <AlertTriangle size={18} aria-hidden="true" className="text-red-400" />
                    : isApplied
                    ? <Check size={18} aria-hidden="true" className="text-green-400" />
                    : <Icon size={18} aria-hidden="true" className="text-theme-accent-light" />}
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-theme-text">{tmpl.name}</h4>
                  {inProgress && (
                    <span className="text-[10px] text-blue-400 uppercase tracking-wider">Installing…</span>
                  )}
                  {hasErrors && (
                    <span className="text-[10px] text-red-400 uppercase tracking-wider">Has errors</span>
                  )}
                  {isApplied && (
                    <span className="text-[10px] text-green-400 uppercase tracking-wider">Applied</span>
                  )}
                  {!inProgress && !hasErrors && !isApplied && tmpl.tier_minimum && (
                    <span className="text-[10px] text-theme-text-muted uppercase tracking-wider">
                      {tmpl.tier_minimum}+
                    </span>
                  )}
                </div>
              </div>
              <p className="text-xs text-theme-text-muted line-clamp-2">{tmpl.description}</p>
              <div className="flex items-center gap-3 mt-2 text-[10px] text-theme-text-muted">
                <span>{tmpl.services?.length || 0} services</span>
                {tmpl.estimated_disk_gb && (
                  <span className="flex items-center gap-1">
                    <HardDrive size={10} aria-hidden="true" />
                    ~{tmpl.estimated_disk_gb}GB
                  </span>
                )}
              </div>
            </button>
          )
        })}
      </div>

      {preview && (
        <TemplatePreview
          template={preview}
          onClose={() => setPreview(null)}
          onApplied={onApplied}
        />
      )}
    </>
  )
}

/**
 * TemplatePreview — modal showing what will be enabled/already enabled/incompatible.
 */
export function TemplatePreview({ template, onClose, onApplied }) {
  const [previewData, setPreviewData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState(null)
  const [applied, setApplied] = useState(false)
  const [applyResult, setApplyResult] = useState(null)

  const Icon = ICON_MAP[template.icon] || Package

  const loadPreview = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetchJson(`/api/templates/${template.id}/preview`, { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setPreviewData(await res.json())
    } catch (err) {
      setError(err.name === 'AbortError' ? 'Request timed out' : 'Failed to load preview')
    } finally {
      setLoading(false)
    }
  }

  const handleApply = async () => {
    setApplying(true)
    setError(null)
    try {
      const res = await fetchJson(`/api/templates/${template.id}/apply`, {
        method: 'POST',
        timeout: TEMPLATE_APPLY_TIMEOUT_MS,
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setApplyResult(data)
      if (data.failed_services?.length > 0 || data.skipped_services?.length > 0) {
        setApplied('partial')
      } else if (data.enabled_count > 0) {
        setApplied(data.restart_required ? 'restart_required' : 'enabled')
      } else {
        setApplied('already_active')
      }
      onApplied?.()
    } catch (err) {
      setError(err.name === 'AbortError' ? 'Request timed out' : 'Failed to apply template')
    } finally {
      setApplying(false)
    }
  }

  // Load preview on mount
  if (!previewData && !loading && !error) {
    loadPreview()
  }

  const changes = previewData?.changes || {}

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-theme-card border border-theme-border rounded-xl p-6 max-w-lg mx-4 w-full"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`${template.name} template preview`}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-theme-accent/10">
              <Icon size={20} className="text-theme-accent-light" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-theme-text">{template.name}</h3>
              <p className="text-xs text-theme-text-muted">{template.description}</p>
            </div>
          </div>
          <button onClick={onClose} className="text-theme-text-muted hover:text-theme-text transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="animate-spin text-theme-accent" size={24} />
          </div>
        )}

        {/* Preview content */}
        {previewData && !applied && (
          <div className="space-y-3">
            {changes.to_enable?.length > 0 && (
              <div>
                <h4 className="text-xs font-medium text-theme-text-muted uppercase tracking-wider mb-1.5">Will be enabled</h4>
                <div className="flex flex-wrap gap-1.5">
                  {changes.to_enable.map(svc => (
                    <span key={svc} className="text-xs px-2 py-1 rounded bg-theme-accent/10 text-theme-accent-light border border-theme-accent/20">
                      {svc}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {changes.already_enabled?.length > 0 && (
              <div>
                <h4 className="text-xs font-medium text-theme-text-muted uppercase tracking-wider mb-1.5">Already running</h4>
                <div className="flex flex-wrap gap-1.5">
                  {changes.already_enabled.map(svc => (
                    <span key={svc} className="text-xs px-2 py-1 rounded bg-green-500/10 text-green-400 border border-green-500/20">
                      <Check size={10} className="inline mr-1" />{svc}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {changes.incompatible?.length > 0 && (
              <div>
                <h4 className="text-xs font-medium text-theme-text-muted uppercase tracking-wider mb-1.5">Incompatible</h4>
                <div className="flex flex-wrap gap-1.5">
                  {changes.incompatible.map(svc => (
                    <span key={svc} className="text-xs px-2 py-1 rounded bg-orange-500/10 text-orange-400 border border-orange-500/20">
                      <AlertTriangle size={10} className="inline mr-1" />{svc}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {previewData.warnings?.length > 0 && (
              <div className="p-2.5 rounded-lg bg-orange-500/10 border border-orange-500/20">
                {previewData.warnings.map((w, i) => (
                  <p key={i} className="text-xs text-orange-300">{w}</p>
                ))}
              </div>
            )}

            {template.service_notes && Object.keys(template.service_notes).length > 0 && (
              <div className="text-xs text-theme-text-muted space-y-1 pt-1">
                {Object.entries(template.service_notes).map(([svc, note]) => (
                  <p key={svc}><span className="text-theme-text">{svc}:</span> {note}</p>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Applied success */}
        {applied && (
          <div className="py-6 text-center">
            {applied === 'partial'
              ? <AlertTriangle size={32} className="text-orange-400 mx-auto mb-2" />
              : <Check size={32} className="text-green-400 mx-auto mb-2" />}
            {applied === 'enabled' && (
              <p className="text-sm text-green-400">Template applied — check extension cards for installation progress</p>
            )}
            {applied === 'already_active' && (
              <p className="text-sm text-green-400">All services in this template are already active</p>
            )}
            {applied === 'restart_required' && (
              <>
                <p className="text-sm text-green-400 mb-3">Template applied successfully</p>
                <div className="p-3 rounded-lg bg-orange-500/10 border border-orange-500/20 text-left">
                  <p className="text-sm text-orange-300 font-medium mb-1">Restart required</p>
                  <p className="text-xs text-orange-200/80">Run <code className="px-1.5 py-0.5 rounded bg-theme-card text-orange-100">ods restart</code> in your terminal to start the newly enabled services.</p>
                </div>
              </>
            )}
            {applied === 'partial' && (
              <div className="p-3 rounded-lg bg-orange-500/10 border border-orange-500/20 text-left">
                <p className="text-sm text-orange-300 font-medium mb-1">Template applied with exceptions</p>
                {applyResult?.failed_services?.length > 0 && (
                  <p className="text-xs text-orange-200/80">
                    Failed to start: {applyResult.failed_services.join(', ')}.
                    {applyResult.restart_required ? ' Run ods restart to retry.' : ''}
                  </p>
                )}
                {applyResult?.skipped_services?.length > 0 && (
                  <p className="text-xs text-orange-200/80">
                    Skipped: {applyResult.skipped_services.join(', ')}.
                  </p>
                )}
                {applyResult?.warnings?.map((warning, index) => (
                  <p key={index} className="text-xs text-orange-200/80 mt-1">{warning}</p>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-sm text-red-300 mb-3">
            {error}
          </div>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-3 mt-4">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-theme-text-muted hover:text-theme-text transition-colors"
          >
            {applied ? 'Close' : 'Cancel'}
          </button>
          {!applied && previewData && (
            <button
              onClick={handleApply}
              disabled={applying || (changes.to_enable?.length === 0)}
              className="px-4 py-2 text-sm rounded-lg bg-theme-accent/20 text-theme-accent-light hover:bg-theme-accent/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {applying ? <Loader2 size={14} className="animate-spin" /> : null}
              Apply Template
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
