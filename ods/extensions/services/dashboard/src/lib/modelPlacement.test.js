import { describePlacement, isCpuSpill, isPlacementProblem, normalizeRuntimePlacement } from './modelPlacement'

const laptopPartial = {
  layersOnGpu: 29,
  layersTotal: 33,
  cpuWeightMiB: 1078.96,
  cpuKvMiB: 136,
  overflowingLayers: 0,
  fullyResident: false,
  intentionalOffload: false,
  state: 'partial',
  remedy: null,
  fitTargetMiB: null,
  detail: '29/33 layers on the GPU, 136 MiB of KV cache in system memory',
}
const resident = {
  ...laptopPartial,
  layersOnGpu: 33,
  cpuKvMiB: 0,
  fullyResident: true,
  state: 'resident',
  detail: null,
}
const unverified = {
  layersOnGpu: null,
  layersTotal: null,
  cpuWeightMiB: null,
  cpuKvMiB: null,
  overflowingLayers: null,
  fullyResident: null,
  intentionalOffload: false,
  state: 'unverified',
  remedy: null,
  fitTargetMiB: null,
  detail: 'The runtime log does not report layer placement for this model.',
}

test('keeps a valid placement from /api/models', () => {
  expect(normalizeRuntimePlacement(laptopPartial)).toEqual(laptopPartial)
  expect(normalizeRuntimePlacement(resident)).toEqual(resident)
  expect(normalizeRuntimePlacement(unverified)).toEqual(unverified)
})

test.each([
  undefined,
  null,
  '29/33',
  { ...laptopPartial, layersOnGpu: '29' },
  { ...laptopPartial, layersOnGpu: 34 },
  { ...laptopPartial, layersTotal: 0 },
  { ...laptopPartial, fullyResident: 'false' },
  { ...laptopPartial, fullyResident: true },
  { ...laptopPartial, state: undefined },
  { ...laptopPartial, state: 'mostly_fine' },
  { ...resident, state: 'partial' },
])('treats a missing or malformed placement as unknown: %j', value => {
  expect(normalizeRuntimePlacement(value)).toBeNull()
})

test('defaults optional fields instead of guessing', () => {
  expect(normalizeRuntimePlacement({ layersOnGpu: 33, layersTotal: 33, fullyResident: true, state: 'resident' })).toEqual({
    layersOnGpu: 33,
    layersTotal: 33,
    cpuWeightMiB: null,
    cpuKvMiB: null,
    overflowingLayers: null,
    fullyResident: true,
    intentionalOffload: false,
    state: 'resident',
    remedy: null,
    fitTargetMiB: null,
    detail: null,
  })
  expect(normalizeRuntimePlacement({ ...laptopPartial, remedy: 'reboot', fitTargetMiB: -1 })).toMatchObject({
    remedy: null,
    fitTargetMiB: null,
  })
})

test('describes a partial offload as a slowdown warning', () => {
  const view = describePlacement(laptopPartial)
  expect(isCpuSpill(view)).toBe(true)
  expect(isPlacementProblem(view)).toBe(true)
  expect(view.label).toBe('Partly on CPU')
  expect(view.warning).toBe('Model partly on CPU (29/33 layers on GPU) — expect slower responses')
  expect(view.detail).toContain('1.1 GB of the model is in system memory.')
  expect(view.detail).toContain('Reload it on the GPU (Configure context, then Reload on GPU).')
  expect(view.hostDetail).toBe(laptopPartial.detail)
  expect(view.canReload).toBe(true)
})

test('an idle GPU with room for the model is told to reload, not to close apps', () => {
  // The 8 GB laptop: 6492 MiB needed, 6860 MiB free, llama.cpp kept 1024 MiB.
  const view = describePlacement({ ...laptopPartial, remedy: 'refit', fitTargetMiB: 1024 })
  expect(view.detail).toContain('The GPU has room for this model, but llama.cpp kept a 1024 MB safety margin free.')
  expect(view.detail).toContain('closing other apps will not help')
  expect(view.detail).not.toContain('Close other apps')
})

test('a GPU that is really full is told to free memory or shrink', () => {
  const view = describePlacement({ ...laptopPartial, remedy: 'free_or_shrink' })
  expect(view.detail).toContain('Close other apps that use the GPU and reload the model, or choose a smaller context or model.')
  expect(view.detail).not.toContain('safety margin')
})

test('a layer cap is fixed in .env, not by reloading', () => {
  const view = describePlacement({ ...laptopPartial, remedy: 'set_auto_layers' })
  expect(view.detail).toContain('N_GPU_LAYERS')
  expect(view.canReload).toBe(false)
})

test('describes a CPU-only load as the stronger warning', () => {
  const view = describePlacement({ ...laptopPartial, layersOnGpu: 0, state: 'cpu_only' })
  expect(isCpuSpill(view)).toBe(true)
  expect(view.label).toBe('CPU only')
  expect(view.warning).toBe('Model running on the CPU only (0/33 layers on GPU) — expect much slower responses')
})

test('MoE expert overflow with every layer on the GPU does not claim layers on the CPU', () => {
  const view = describePlacement({
    ...laptopPartial,
    layersOnGpu: 49,
    layersTotal: 49,
    cpuKvMiB: 0,
    overflowingLayers: 12,
  })
  expect(isCpuSpill(view)).toBe(true)
  expect(view.label).toBe('Part in system memory')
  expect(view.warning).toBe(
    'All 49/49 layers are on the GPU, but llama.cpp moved the MoE expert weights of 12 layers to system memory — expect slower responses',
  )
  expect(view.warning).not.toMatch(/partly on CPU/i)
  expect(view.detail).not.toContain('of the model is in system memory.')
})

test('KV cache in system memory with every layer on the GPU is named', () => {
  const view = describePlacement({ ...laptopPartial, layersOnGpu: 33, cpuKvMiB: 1088, overflowingLayers: 0 })
  expect(view.warning).toBe(
    'All 33/33 layers are on the GPU, but 1.1 GB of KV cache is in system memory — expect slower responses',
  )
})

test('an unverified placement is a problem, not a fit', () => {
  const view = describePlacement(normalizeRuntimePlacement(unverified))
  expect(isPlacementProblem(view)).toBe(true)
  expect(isCpuSpill(view)).toBe(false)
  expect(view.label).toBe('GPU placement unverified')
  expect(view.warning).toBe(
    'ODS cannot confirm this model is on the GPU — llama.cpp did not log where it placed the layers',
  )
  expect(view.hostDetail).toBe(unverified.detail)
})

test('describes full and configured placements without a warning', () => {
  expect(describePlacement(resident)).toEqual({ state: 'resident', tone: 'green', label: 'GPU: 33/33 layers' })
  const intentional = describePlacement({ ...resident, intentionalOffload: true, state: 'intentional' })
  expect(intentional).toEqual({ state: 'intentional', tone: 'neutral', label: 'GPU: 33/33 layers · CPU offload configured' })
  expect(isCpuSpill(intentional)).toBe(false)
  expect(isPlacementProblem(intentional)).toBe(false)
  expect(describePlacement(null)).toBeNull()
})
