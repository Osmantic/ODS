import { renderHook, waitFor } from '@testing-library/react'
import { useModels } from './useModels'

function modelsPayload(extra = {}) {
  return {
    models: [],
    gpu: null,
    currentModel: 'qwen3.5-9b-q4',
    loadedModel: 'Qwen3.5-9B-Q4_K_M.gguf',
    odsMode: 'local',
    configuredMode: 'local',
    llmBackend: 'llama-server',
    ...extra,
  }
}

function serve(payload) {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => payload })))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

test('reads runtime.placement from /api/models', async () => {
  serve(modelsPayload({
    runtime: {
      placement: {
        layersOnGpu: 29,
        layersTotal: 33,
        cpuWeightMiB: 1078.96,
        fullyResident: false,
        intentionalOffload: false,
        state: 'partial',
        detail: null,
      },
    },
  }))
  const { result } = renderHook(() => useModels())

  await waitFor(() => expect(result.current.loading).toBe(false))
  expect(result.current.runtimePlacement).toEqual({
    layersOnGpu: 29,
    layersTotal: 33,
    cpuWeightMiB: 1078.96,
    fullyResident: false,
    intentionalOffload: false,
    state: 'partial',
    detail: null,
  })
})

test.each([
  ['an older API without runtime', modelsPayload()],
  ['an unknown placement', modelsPayload({ runtime: { placement: null } })],
  ['a malformed placement', modelsPayload({ runtime: { placement: { layersOnGpu: '29', layersTotal: 33 } } })],
])('reports no placement for %s', async (_label, payload) => {
  serve(payload)
  const { result } = renderHook(() => useModels())

  await waitFor(() => expect(result.current.loading).toBe(false))
  expect(result.current.runtimePlacement).toBeNull()
})
