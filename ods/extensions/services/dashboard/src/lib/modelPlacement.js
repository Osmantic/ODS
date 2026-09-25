// Where the running model's layers are, from /api/models runtime.placement.
// Older hosts omit it; anything missing or malformed is "unknown" (null) and
// must never be shown as "fits the GPU".

const RESIDENT_STATES = new Set(['resident', 'intentional'])
const STATES = new Set([...RESIDENT_STATES, 'partial', 'cpu_only'])

export function normalizeRuntimePlacement(value) {
  if (!value || typeof value !== 'object') return null
  const { layersOnGpu, layersTotal, fullyResident, state } = value
  if (!Number.isInteger(layersOnGpu) || !Number.isInteger(layersTotal)) return null
  if (layersTotal <= 0 || layersOnGpu < 0 || layersOnGpu > layersTotal) return null
  if (typeof fullyResident !== 'boolean' || !STATES.has(state)) return null
  if (RESIDENT_STATES.has(state) !== fullyResident) return null
  if (fullyResident && layersOnGpu < layersTotal) return null
  const cpuWeightMiB = Number.isFinite(value.cpuWeightMiB) && value.cpuWeightMiB >= 0
    ? value.cpuWeightMiB
    : null
  return {
    layersOnGpu,
    layersTotal,
    cpuWeightMiB,
    fullyResident,
    intentionalOffload: value.intentionalOffload === true,
    state,
    detail: typeof value.detail === 'string' && value.detail.trim() ? value.detail.trim() : null,
  }
}

function formatSystemMemory(mib) {
  if (mib === null) return null
  return mib >= 1024 ? `${(mib / 1024).toFixed(1)} GB` : `${Math.round(mib)} MB`
}

const REMEDY = 'Close other apps that use the GPU and reload the model, or choose a smaller context or model.'

export function describePlacement(placement) {
  if (!placement) return null
  const layers = `${placement.layersOnGpu}/${placement.layersTotal} layers`
  if (placement.state === 'resident') {
    return { state: 'resident', tone: 'green', label: `GPU: ${layers}` }
  }
  if (placement.state === 'intentional') {
    return { state: 'intentional', tone: 'neutral', label: `GPU: ${layers} · CPU offload configured` }
  }
  const systemMemory = formatSystemMemory(placement.cpuWeightMiB)
  const warning = placement.state === 'cpu_only'
    ? `Model running on the CPU only (${layers} on GPU) — expect much slower responses`
    : `Model partly on CPU (${layers} on GPU) — expect slower responses`
  return {
    state: placement.state,
    tone: 'amber',
    label: placement.state === 'cpu_only' ? 'CPU only' : 'Partly on CPU',
    warning,
    detail: [
      systemMemory && `${systemMemory} of the model is in system memory.`,
      REMEDY,
    ].filter(Boolean).join(' '),
    hostDetail: placement.detail,
  }
}

export function isCpuSpill(view) {
  return view?.state === 'partial' || view?.state === 'cpu_only'
}
