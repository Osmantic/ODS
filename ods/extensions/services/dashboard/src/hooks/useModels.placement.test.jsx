import { act, renderHook, waitFor } from '@testing-library/react'
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
    cpuKvMiB: null,
    overflowingLayers: null,
    fullyResident: false,
    intentionalOffload: false,
    state: 'partial',
    remedy: null,
    fitTargetMiB: null,
    detail: null,
  })
})

test('a reload asks the server to reload and waits for it, not for the first matching poll', async () => {
  vi.useFakeTimers()
  let answerPost
  const post = new Promise(resolve => { answerPost = resolve })
  const running = modelsPayload({
    models: [{ id: 'qwen3.5-9b-q4', status: 'loaded', contextLength: 65536 }],
    activationReadyModel: 'qwen3.5-9b-q4',
  })
  const fetchMock = vi.fn(async (_url, options) => {
    if (options?.method === 'POST') return post
    return { ok: true, json: async () => running }
  })
  vi.stubGlobal('fetch', fetchMock)
  try {
    const { result } = renderHook(() => useModels())
    await act(async () => {})

    let settled = false
    let loadPromise
    act(() => {
      loadPromise = result.current
        .loadModel('qwen3.5-9b-q4', { contextLength: 65536, reload: true })
        .then(() => { settled = true })
    })
    const postCall = fetchMock.mock.calls.find(call => call[1]?.method === 'POST')
    expect(JSON.parse(postCall[1].body)).toEqual({ context_length: 65536, reload: true })

    // The running model already matches every poll; without waiting for the
    // server the reload would report done before it started.
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(settled).toBe(false)

    answerPost({ ok: true })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
      await loadPromise
    })
    expect(settled).toBe(true)
    expect(result.current.error).toBeNull()
  } finally {
    vi.useRealTimers()
  }
})

test('reads an unverified placement as a state of its own', async () => {
  serve(modelsPayload({
    runtime: { placement: { state: 'unverified', layersOnGpu: null, layersTotal: null, detail: 'no placement line' } },
  }))
  const { result } = renderHook(() => useModels())

  await waitFor(() => expect(result.current.loading).toBe(false))
  expect(result.current.runtimePlacement).toMatchObject({ state: 'unverified', layersTotal: null, detail: 'no placement line' })
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
