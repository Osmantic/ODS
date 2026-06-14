import {
  AlertTriangle,
  CheckCircle2,
  CircleMinus,
  CircleHelp,
  RefreshCw,
  ServerCog,
  XCircle,
} from 'lucide-react'

const STATUS_STYLE = {
  ok: {
    icon: CheckCircle2,
    label: 'Ready',
    className: 'border-emerald-500/20 bg-emerald-500/[0.06] text-emerald-300',
  },
  failed: {
    icon: XCircle,
    label: 'Failed',
    className: 'border-red-500/20 bg-red-500/[0.06] text-red-300',
  },
  unsupported: {
    icon: AlertTriangle,
    label: 'Unsupported',
    className: 'border-amber-500/20 bg-amber-500/[0.06] text-amber-300',
  },
  skipped: {
    icon: CircleMinus,
    label: 'Skipped',
    className: 'border-zinc-700 bg-zinc-800/40 text-zinc-400',
  },
  unverified: {
    icon: CircleHelp,
    label: 'Unverified',
    className: 'border-sky-500/20 bg-sky-500/[0.06] text-sky-300',
  },
}

const PROVIDER_STYLE = {
  ready: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  degraded: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  blocked: 'border-red-500/30 bg-red-500/10 text-red-300',
  unverified: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
}

const WARNING_LABEL = {
  chat_model_legacy_llm_model: 'Using legacy LLM_MODEL. Copy this model id to LEMONADE_MODEL.',
  chat_model_legacy_llm_model_ignored: 'Ignored legacy LLM_MODEL because it is not a chat-capable Lemonade catalog id. Set LEMONADE_MODEL explicitly.',
}

function warningLabel(warning) {
  return WARNING_LABEL[warning] || warning.replaceAll('_', ' ')
}

function CapabilityRow({ capability }) {
  const style = STATUS_STYLE[capability.status] || STATUS_STYLE.failed
  const Icon = style.icon

  return (
    <div className={`min-w-0 border rounded-lg px-3 py-2.5 ${style.className}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Icon size={14} className="shrink-0" />
            <span className="truncate text-xs font-semibold uppercase">{capability.name.replaceAll('_', ' ')}</span>
          </div>
          {capability.detail && (
            <p className="mt-1 truncate font-mono text-[10px] opacity-75" title={capability.detail}>
              {capability.detail.replaceAll('_', ' ')}
            </p>
          )}
        </div>
        <span className="shrink-0 text-[10px] font-medium uppercase">{style.label}</span>
      </div>
      {capability.recoveryHint && (
        <p className="mt-2 border-t border-current/15 pt-2 text-[11px] leading-4 opacity-90">
          {capability.recoveryHint}
        </p>
      )}
    </div>
  )
}

export function LemonadeProviderContract({ runtime, onRunActiveProbe, activeProbeRunning = false, activeProbeError = null }) {
  const capabilities = runtime?.providerCapabilities || []
  if (!capabilities.length) return null

  const status = runtime.providerStatus || 'blocked'
  const loadedModels = runtime.loadedModels || []
  const warnings = runtime.warnings || []
  const loadedSummary = loadedModels.length
    ? `${loadedModels.length} loaded model${loadedModels.length === 1 ? '' : 's'}`
    : runtime.loadedModel || 'No loaded model'

  return (
    <section className="mb-6 border border-zinc-800 bg-zinc-900/50 rounded-xl p-4" aria-label="Lemonade provider contract">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-white">
            <ServerCog size={16} className="text-amber-300" />
            Lemonade Provider Contract
          </h2>
          <p className="mt-1 text-xs text-zinc-500">
            {loadedSummary} · {runtime.runtimeMode || 'unknown mode'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-medium uppercase text-zinc-500">
            {runtime.providerProbeMode || 'passive'} probe
          </span>
          <span className={`rounded-md border px-2.5 py-1 text-[10px] font-semibold uppercase ${PROVIDER_STYLE[status] || PROVIDER_STYLE.blocked}`}>
            {status}
          </span>
          {onRunActiveProbe && (
            <button
              type="button"
              onClick={onRunActiveProbe}
              disabled={activeProbeRunning}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-zinc-700 text-zinc-300 transition-colors hover:border-zinc-600 hover:bg-zinc-800 disabled:cursor-wait disabled:opacity-50"
              title="Run active Lemonade capability probe; this may load or switch models"
              aria-label="Run active Lemonade capability probe"
            >
              <RefreshCw size={13} className={activeProbeRunning ? 'animate-spin' : ''} />
            </button>
          )}
        </div>
      </div>

      {activeProbeError && (
        <p className="mt-3 text-xs text-red-300" role="alert">{activeProbeError}</p>
      )}

      {warnings.length > 0 && (
        <div className="mt-3 border border-amber-500/20 bg-amber-500/[0.06] px-3 py-2 text-amber-200">
          {warnings.map(warning => (
            <p key={warning} className="flex items-start gap-2 text-[11px] leading-4">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span>{warningLabel(warning)}</span>
            </p>
          ))}
        </div>
      )}

      <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {capabilities.map(capability => (
          <CapabilityRow key={capability.name} capability={capability} />
        ))}
      </div>
    </section>
  )
}
