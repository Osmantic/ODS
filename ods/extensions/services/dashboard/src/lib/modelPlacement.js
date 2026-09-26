// Where the running model's layers are, from /api/models runtime.placement.
// Older hosts omit it; anything missing or malformed is "unknown" (null) and
// must never be shown as "fits the GPU". "unverified" is different: the model
// loaded but llama.cpp did not log where its layers went, so the page must
// not claim it fits either.

const RESIDENT_STATES = new Set(['resident', 'intentional'])
const SPILL_STATES = new Set(['partial', 'cpu_only'])
const STATES = new Set([...RESIDENT_STATES, ...SPILL_STATES])
const REMEDIES = new Set(['refit', 'free_or_shrink', 'set_auto_layers'])

function nonNegativeNumber(value) {
  return Number.isFinite(value) && value >= 0 ? value : null
}

function nonNegativeInteger(value) {
  return Number.isInteger(value) && value >= 0 ? value : null
}

function text(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

export function normalizeRuntimePlacement(value) {
  if (!value || typeof value !== 'object') return null
  const { layersOnGpu, layersTotal, fullyResident, state } = value
  if (state === 'unverified') {
    return {
      layersOnGpu: null,
      layersTotal: null,
      cpuWeightMiB: null,
      cpuKvMiB: null,
      overflowingLayers: null,
      fullyResident: null,
      intentionalOffload: value.intentionalOffload === true,
      state,
      remedy: null,
      fitTargetMiB: null,
      detail: text(value.detail),
    }
  }
  if (!Number.isInteger(layersOnGpu) || !Number.isInteger(layersTotal)) return null
  if (layersTotal <= 0 || layersOnGpu < 0 || layersOnGpu > layersTotal) return null
  if (typeof fullyResident !== 'boolean' || !STATES.has(state)) return null
  if (RESIDENT_STATES.has(state) !== fullyResident) return null
  if (fullyResident && layersOnGpu < layersTotal) return null
  return {
    layersOnGpu,
    layersTotal,
    cpuWeightMiB: nonNegativeNumber(value.cpuWeightMiB),
    cpuKvMiB: nonNegativeNumber(value.cpuKvMiB),
    overflowingLayers: nonNegativeInteger(value.overflowingLayers),
    fullyResident,
    intentionalOffload: value.intentionalOffload === true,
    state,
    remedy: REMEDIES.has(value.remedy) ? value.remedy : null,
    fitTargetMiB: nonNegativeInteger(value.fitTargetMiB),
    detail: text(value.detail),
  }
}

function formatMemory(mib) {
  if (mib === null) return null
  return mib >= 1024 ? `${(mib / 1024).toFixed(1)} GB` : `${Math.round(mib)} MB`
}

const RELOAD = 'Reload it on the GPU (Configure context, then Reload on GPU)'

// The fix follows llama.cpp's own fit numbers (host agent / ods doctor).
function remedyText(placement) {
  if (placement.remedy === 'refit') {
    const margin = placement.fitTargetMiB ? `a ${placement.fitTargetMiB} MB` : 'a large'
    return `The GPU has room for this model, but llama.cpp kept ${margin} safety margin free. `
      + `${RELOAD} so ODS fits it again with a smaller margin; closing other apps will not help.`
  }
  if (placement.remedy === 'free_or_shrink') {
    return 'Close other apps that use the GPU and reload the model, or choose a smaller context or model.'
  }
  if (placement.remedy === 'set_auto_layers') {
    return 'N_GPU_LAYERS limits how many layers go to the GPU. Set it to auto in .env and reload the model.'
  }
  return `${RELOAD}. If it spills again, close other apps that use the GPU or choose a smaller context or model.`
}

function spillView(placement) {
  const layers = `${placement.layersOnGpu}/${placement.layersTotal} layers`
  if (placement.state === 'cpu_only') {
    return {
      label: 'CPU only',
      warning: `Model running on the CPU only (${layers} on GPU) — expect much slower responses`,
      systemMemory: formatMemory(placement.cpuWeightMiB),
    }
  }
  if (placement.layersOnGpu < placement.layersTotal) {
    return {
      label: 'Partly on CPU',
      warning: `Model partly on CPU (${layers} on GPU) — expect slower responses`,
      systemMemory: formatMemory(placement.cpuWeightMiB),
    }
  }
  // Every layer is on the GPU, yet part of the model is in system memory:
  // llama.cpp's fit moved MoE expert weights there, or the KV cache sits there.
  const inRam = placement.overflowingLayers
    ? `llama.cpp moved the MoE expert weights of ${placement.overflowingLayers} layers to system memory`
    : placement.cpuKvMiB
      ? `${formatMemory(placement.cpuKvMiB)} of KV cache is in system memory`
      : 'part of the model is in system memory'
  return {
    label: 'Part in system memory',
    warning: `All ${layers} are on the GPU, but ${inRam} — expect slower responses`,
    systemMemory: null,
  }
}

export function describePlacement(placement) {
  if (!placement) return null
  if (placement.state === 'unverified') {
    return {
      state: 'unverified',
      tone: 'amber',
      label: 'GPU placement unverified',
      warning: 'ODS cannot confirm this model is on the GPU — llama.cpp did not log where it placed the layers',
      detail: 'A model partly on the CPU still answers, only slower. Run ods doctor on the host for the fix.',
      hostDetail: placement.detail,
    }
  }
  const layers = `${placement.layersOnGpu}/${placement.layersTotal} layers`
  if (placement.state === 'resident') {
    return { state: 'resident', tone: 'green', label: `GPU: ${layers}` }
  }
  if (placement.state === 'intentional') {
    return { state: 'intentional', tone: 'neutral', label: `GPU: ${layers} · CPU offload configured` }
  }
  const view = spillView(placement)
  return {
    state: placement.state,
    tone: 'amber',
    label: view.label,
    warning: view.warning,
    detail: [
      view.systemMemory && `${view.systemMemory} of the model is in system memory.`,
      remedyText(placement),
    ].filter(Boolean).join(' '),
    hostDetail: placement.detail,
    canReload: placement.remedy !== 'set_auto_layers',
  }
}

export function isCpuSpill(view) {
  return view?.state === 'partial' || view?.state === 'cpu_only'
}

// A spill, or a placement the log does not state: either way the page must
// not claim the model fits the GPU.
export function isPlacementProblem(view) {
  return isCpuSpill(view) || view?.state === 'unverified'
}
