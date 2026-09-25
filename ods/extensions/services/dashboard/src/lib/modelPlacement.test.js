import { describePlacement, isCpuSpill, normalizeRuntimePlacement } from './modelPlacement'

const laptopPartial = {
  layersOnGpu: 29,
  layersTotal: 33,
  cpuWeightMiB: 1078.96,
  fullyResident: false,
  intentionalOffload: false,
  state: 'partial',
  detail: '29/33 layers on the GPU, 136 MiB of KV cache in system memory',
}
const resident = { ...laptopPartial, layersOnGpu: 33, fullyResident: true, state: 'resident', detail: null }

test('keeps a valid placement from /api/models', () => {
  expect(normalizeRuntimePlacement(laptopPartial)).toEqual(laptopPartial)
  expect(normalizeRuntimePlacement(resident)).toEqual(resident)
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
    fullyResident: true,
    intentionalOffload: false,
    state: 'resident',
    detail: null,
  })
})

test('describes a partial offload as a slowdown warning', () => {
  const view = describePlacement(laptopPartial)
  expect(isCpuSpill(view)).toBe(true)
  expect(view.label).toBe('Partly on CPU')
  expect(view.warning).toBe('Model partly on CPU (29/33 layers on GPU) — expect slower responses')
  expect(view.detail).toContain('1.1 GB of the model is in system memory.')
  expect(view.hostDetail).toBe(laptopPartial.detail)
})

test('describes a CPU-only load as the stronger warning', () => {
  const view = describePlacement({ ...laptopPartial, layersOnGpu: 0, state: 'cpu_only' })
  expect(isCpuSpill(view)).toBe(true)
  expect(view.label).toBe('CPU only')
  expect(view.warning).toBe('Model running on the CPU only (0/33 layers on GPU) — expect much slower responses')
})

test('describes full and configured placements without a warning', () => {
  expect(describePlacement(resident)).toEqual({ state: 'resident', tone: 'green', label: 'GPU: 33/33 layers' })
  const intentional = describePlacement({ ...resident, intentionalOffload: true, state: 'intentional' })
  expect(intentional).toEqual({ state: 'intentional', tone: 'neutral', label: 'GPU: 33/33 layers · CPU offload configured' })
  expect(isCpuSpill(intentional)).toBe(false)
  expect(describePlacement(null)).toBeNull()
})
