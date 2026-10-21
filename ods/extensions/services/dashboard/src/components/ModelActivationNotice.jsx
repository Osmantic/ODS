import {AlertCircle, Loader2} from 'lucide-react'
import {Link} from 'react-router-dom'
import {modelActivationStatus} from '../lib/modelActivationStatus'

const phases = {
  preparing: ['Preparing model switch', 'ODS is preparing the selected model.'],
  loading: ['Loading selected model', 'ODS is starting the selected model. Chat is paused while it loads.'],
  profiling: ['Checking what this model can do (first time only)', 'ODS is trying the new model’s answers, tool calls and thinking on this machine. It takes up to two minutes, once per model.'],
  verifying: ['Checking selected model', 'ODS is checking that the selected model can respond before resuming chat.'],
  rolling_back: ['Model activation failed; restoring previous model', 'ODS is restoring the previous model. Wait for recovery to finish before starting another model operation.'],
  rollback_verifying: ['Checking the previous model', 'The selected model could not be activated. ODS is checking the restored model before resuming chat.'],
}
const failures = {
  runtime_load_failed: 'The selected model could not be loaded by the runtime. Its GGUF or model export may be unsupported. Try a different GGUF or model, or update ODS before trying this file again.',
  runtime_readiness_failed: 'The selected model did not become ready.',
  consumer_verification_failed: 'ODS could not verify the selected model across its applications.',
  rollback_unconfirmed: 'Restoration of the previous model has not been confirmed.',
}

/** Advisory only: admission and recovery authorization remain backend-owned. */
export default function ModelActivationNotice({value, pending=false, portal=false, available=false, onRefresh}) {
  const state = modelActivationStatus(value)
  if (!pending && (!state || state.outcome === 'activated')) return null
  const active = state?.active === true
  const failed = state?.outcome === 'rolled_back' || state?.outcome === 'rollback_unconfirmed'
    || ['rolling_back', 'rollback_verifying'].includes(state?.phase)
  const [title, detail] = active ? phases[state.phase]
    : state?.outcome === 'rolled_back'
      ? ['Model switch failed; previous model restored', 'ODS verified the previous model after rolling back. Review the selected model’s compatibility before trying it again.']
      : state?.outcome === 'rollback_unconfirmed'
        ? ['Model recovery needs attention', 'ODS could not confirm restoration of the previous model. Review model status and recover the existing switch before trying another model.']
        : ['Model change has not finished', 'ODS has not confirmed that model activation or recovery is complete. Review model status for the next step.']
  const Icon = active ? Loader2 : AlertCircle
  return <section role={failed ? 'alert' : 'status'} aria-label="Model activation" className="mx-auto my-3 w-full max-w-5xl rounded-xl border border-theme-border bg-theme-card p-4 text-sm text-theme-text-secondary">
    <div className="flex items-start gap-3"><Icon size={18} className={active ? 'mt-0.5 shrink-0 animate-spin' : 'mt-0.5 shrink-0'} aria-hidden="true"/><div>
      <p className="font-medium text-theme-text">{title}</p>
      <p className="mt-1">{detail}</p>
      {state?.failureCode && <p className="mt-1">{failures[state.failureCode]}</p>}
      {portal && available && state?.outcome === 'rolled_back' && <p className="mt-2">Portal is available again.</p>}
      {portal && <p className="mt-2">Your draft stays in this chat. Sending is available only when Portal confirms it is ready.</p>}
      <div className="mt-3 flex gap-4">
        {portal && <Link className="text-theme-accent-light underline" to="/models">View model status and recovery</Link>}
        {onRefresh && <button type="button" className="text-theme-accent-light underline" onClick={onRefresh}>Refresh model status</button>}
      </div>
    </div></div>
  </section>
}
