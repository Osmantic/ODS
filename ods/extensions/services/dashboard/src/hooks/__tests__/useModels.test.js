import { renderHook, waitFor, act } from '@testing-library/react'
import { getMockModels, MOCK_MODES, useModels } from '../useModels'

// Shadow jsdom's Document.prototype.hidden getter on the instance; deleting
// the own property in afterEach restores the prototype behavior.
const setDocumentHidden = (hidden) => {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
}

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const modelsResponse = (models, overrides = {}) => ({
  ok: true,
  json: () => Promise.resolve({
    models,
    gpu: null,
    currentModel: null,
    activationReadyModel: Object.prototype.hasOwnProperty.call(overrides, 'activationReadyModel')
      ? overrides.activationReadyModel
      : (overrides.currentModel ?? null),
    odsMode: 'local',
    configuredMode: 'local',
    ...overrides,
  })
})

describe('useModels', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.restoreAllMocks()
    delete document.hidden
  })

  test('development fixtures use unique model IDs and runnable local modes', () => {
    const mockModels = getMockModels()
    const modelIds = mockModels.map(model => model.id)

    expect(new Set(modelIds).size).toBe(modelIds.length)
    expect(modelIds).toContain('Qwen/Qwen2.5-32B-Instruct-AWQ')
    expect(modelIds).toContain('Qwen/Qwen2.5-Coder-32B-Instruct-AWQ')
    expect(MOCK_MODES).toEqual({ odsMode: 'local', configuredMode: 'local' })
  })

  test('fetches models on mount', async () => {
    const mockData = {
      models: [{ id: 'qwen-32b', name: 'Qwen2.5 32B' }],
      gpu: { vramTotal: 16 },
      currentModel: 'qwen-32b',
      configuredModel: 'qwen-32b',
      odsMode: 'cloud',
      configuredMode: 'cloud',
      recommendationAlternatives: [{ id: 'qwen-32b', name: 'Qwen2.5 32B' }]
    }
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockData)
    })

    const { result } = renderHook(() => useModels())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.models).toHaveLength(1)
    expect(result.current.models[0].id).toBe('qwen-32b')
    expect(result.current.gpu.vramTotal).toBe(16)
    expect(result.current.currentModel).toBe('qwen-32b')
    expect(result.current.configuredModel).toBe('qwen-32b')
    expect(result.current.odsMode).toBe('cloud')
    expect(result.current.configuredMode).toBe('cloud')
    expect(result.current.canActivateModels).toBe(false)
    expect(result.current.recommendationAlternatives[0].id).toBe('qwen-32b')
    expect(result.current.error).toBeNull()
  })

  test('surfaces backend-owned activation as a pending model action', async () => {
    const target = 'slow-model'
    fetch.mockResolvedValue(modelsResponse(
      [{
        id: target,
        status: 'downloaded',
        modelOperation: {
          active: true,
          operation: 'model_activation',
          modelId: target,
        },
      }],
      {
        modelLifecycle: {
          active: true,
          operation: 'model_activation',
          target,
          modelId: target,
        },
      }
    ))

    const { result } = renderHook(() => useModels())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.modelLifecycle).toEqual({
      active: true,
      operation: 'model_activation',
      target,
      modelId: target,
    })
    expect(result.current.activationLoading).toBe(target)
    expect(result.current.actionLoadingModels).toEqual([target])
  })

  test('treats a missing runtime mode as unknown and blocks activation', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ models: [], gpu: null, currentModel: null }),
    })

    const { result } = renderHook(() => useModels())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.odsMode).toBe('unknown')
    expect(result.current.configuredMode).toBe('unknown')
    expect(result.current.canActivateModels).toBe(false)
  })

  test('does not send a model activation POST in cloud mode', async () => {
    const target = 'downloaded-model'
    fetch.mockResolvedValue(modelsResponse(
      [{ id: target, status: 'downloaded' }],
      { odsMode: 'cloud', configuredMode: 'cloud' }
    ))

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.odsMode).toBe('cloud'))

    await act(async () => {
      await result.current.loadModel(target)
    })

    const activationPosts = fetch.mock.calls.filter(([, options]) => options?.method === 'POST')
    expect(activationPosts).toHaveLength(0)
    expect(result.current.actionLoading).toBeNull()
    expect(result.current.error).toBe('ODS is running in cloud mode. A local-mode installation is required to run downloaded models.')
  })

  test('does not activate when effective and configured modes differ', async () => {
    const target = 'downloaded-model'
    fetch.mockResolvedValue(modelsResponse(
      [{ id: target, status: 'downloaded' }],
      { odsMode: 'local', configuredMode: 'cloud' }
    ))

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.configuredMode).toBe('cloud'))

    await act(async () => {
      await result.current.loadModel(target)
    })

    const activationPosts = fetch.mock.calls.filter(([, options]) => options?.method === 'POST')
    expect(activationPosts).toHaveLength(0)
    expect(result.current.canActivateModels).toBe(false)
    expect(result.current.error).toContain('running in local mode but configured for cloud mode')
  })

  test('sets error on fetch failure', async () => {
    fetch.mockResolvedValue({ ok: false })

    const { result } = renderHook(() => useModels())

    await waitFor(() => {
      expect(result.current.error).toBeTruthy()
    })
    expect(result.current.loading).toBe(false)
  })

  test('downloadModel calls POST and refreshes', async () => {
    fetch.mockImplementation((url, opts) => {
      if (opts?.method === 'POST') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ models: [{ id: 'new-model' }], gpu: null, currentModel: null })
      })
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.downloadModel('new-model')
    })

    const postCall = fetch.mock.calls.find(c => c[1]?.method === 'POST')
    expect(postCall[0]).toBe('/api/models/new-model/download')
    expect(Object.keys(postCall[1]).sort()).toEqual(['method', 'signal'])
    expect(postCall[1]).toMatchObject({ method: 'POST' })
    expect(postCall[1].signal).toBeInstanceOf(globalThis.AbortSignal)
  })

  test('loadModel sends the exact encoded activation request', async () => {
    vi.useFakeTimers()
    const target = 'org/model q4'
    let currentModel = null
    fetch.mockImplementation((_url, options) => {
      if (options?.method === 'POST') {
        currentModel = target
        return Promise.resolve({ ok: true })
      }
      return Promise.resolve(modelsResponse(
        [{ id: target, status: currentModel ? 'loaded' : 'downloaded' }],
        { currentModel }
      ))
    })

    try {
      const { result } = renderHook(() => useModels())
      await act(async () => {})

      let loadPromise
      act(() => {
        loadPromise = result.current.loadModel(target)
      })

      const postCall = fetch.mock.calls.find(c => c[1]?.method === 'POST')
      expect(postCall[0]).toBe('/api/models/org%2Fmodel%20q4/load')
      expect(Object.keys(postCall[1]).sort()).toEqual(['method', 'signal'])
      expect(postCall[1]).toMatchObject({ method: 'POST' })
      expect(postCall[1].signal).toBeInstanceOf(globalThis.AbortSignal)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
        await loadPromise
      })
    } finally {
      vi.useRealTimers()
    }
  })

  test('keeps activation pending until downstream synchronization is receipted', async () => {
    vi.useFakeTimers()
    const target = 'qwen-model'
    let currentModel = null
    let activationReadyModel = null
    fetch.mockImplementation((_url, options) => {
      if (options?.method === 'POST') {
        currentModel = target
        return Promise.resolve({ ok: true })
      }
      return Promise.resolve(modelsResponse(
        [{ id: target, status: currentModel ? 'loaded' : 'downloaded' }],
        { currentModel, activationReadyModel }
      ))
    })

    try {
      const { result } = renderHook(() => useModels())
      await act(async () => {})

      let loadPromise
      act(() => {
        loadPromise = result.current.loadModel(target)
      })

      await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
      expect(result.current.currentModel).toBe(target)
      expect(result.current.activationReadyModel).toBeNull()
      expect(result.current.actionLoading).toBe(target)

      activationReadyModel = target
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
        await loadPromise
      })
      expect(result.current.activationReadyModel).toBe(target)
      expect(result.current.actionLoading).toBeNull()
      expect(result.current.error).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  test('aborts a hung download start and releases its pending action for retry', async () => {
    vi.useFakeTimers()
    let postCount = 0
    fetch.mockImplementation((_url, options) => {
      if (options?.method === 'POST') {
        postCount += 1
        return new Promise((_resolve, reject) => {
          options.signal.addEventListener('abort', () => {
            const error = new Error('The operation was aborted.')
            error.name = 'AbortError'
            reject(error)
          }, { once: true })
        })
      }
      return Promise.resolve(modelsResponse([{ id: 'new-model', status: 'available' }]))
    })

    try {
      const { result } = renderHook(() => useModels())
      await act(async () => {})

      let firstError
      let firstDownload
      act(() => {
        firstDownload = result.current.downloadModel('new-model').catch((error) => {
          firstError = error
        })
      })
      expect(result.current.actionLoading).toBe('new-model')
      expect(postCount).toBe(1)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(15000)
        await firstDownload
      })

      expect(firstError?.message).toMatch(/did not start within 15 seconds/i)
      expect(result.current.actionLoading).toBeNull()
      expect(result.current.error).toBeNull()

      let retryDownload
      act(() => {
        retryDownload = result.current.downloadModel('new-model').catch(() => {})
      })
      expect(postCount).toBe(2)
      expect(result.current.actionLoading).toBe('new-model')

      await act(async () => {
        await vi.advanceTimersByTimeAsync(15000)
        await retryDownload
      })
      expect(result.current.actionLoading).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  test('keeps unrelated mutation errors separate from download start failures', async () => {
    fetch.mockImplementation((_url, options) => {
      if (options?.method === 'DELETE') {
        return Promise.resolve({
          ok: false,
          json: () => Promise.resolve({ detail: 'Delete is blocked by the active runtime.' }),
        })
      }
      if (options?.method === 'POST') {
        return Promise.resolve({
          ok: false,
          json: () => Promise.resolve({ detail: 'Download service is unavailable.' }),
        })
      }
      return Promise.resolve(modelsResponse([{ id: 'new-model', status: 'available' }]))
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.deleteModel('old-model')
    })
    expect(result.current.error).toBe('Delete is blocked by the active runtime.')

    let downloadError
    await act(async () => {
      try {
        await result.current.downloadModel('new-model')
      } catch (error) {
        downloadError = error
      }
    })

    expect(downloadError?.message).toBe('Download service is unavailable.')
    expect(result.current.error).toBe('Delete is blocked by the active runtime.')
  })

  test('clears a pending download when an independent refresh confirms completion', async () => {
    const downloadRequest = deferred()
    let snapshot = [{ id: 'new-model', status: 'available' }]
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'POST') return downloadRequest.promise
      return Promise.resolve(modelsResponse(snapshot))
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    let downloadPromise
    act(() => {
      downloadPromise = result.current.downloadModel('new-model')
    })
    await waitFor(() => expect(result.current.actionLoading).toBe('new-model'))

    snapshot = [{ id: 'new-model', status: 'downloaded' }]
    await act(async () => {
      await result.current.refresh()
    })
    expect(result.current.actionLoading).toBeNull()

    await act(async () => {
      downloadRequest.resolve({ ok: true })
      await downloadPromise
    })
  })

  test('deleteModel calls DELETE and refreshes', async () => {
    vi.stubGlobal('confirm', vi.fn(() => { throw new Error('Native delete confirmation should stay in the page UI.') }))
    const target = 'org/model q4'

    fetch.mockImplementation((url, opts) => {
      if (opts?.method === 'DELETE') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ models: [], gpu: null, currentModel: null })
      })
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.deleteModel(target)
    })

    expect(confirm).not.toHaveBeenCalled()
    const deleteCall = fetch.mock.calls.find(c => c[1]?.method === 'DELETE')
    expect(deleteCall).toEqual(['/api/models/org%2Fmodel%20q4', { method: 'DELETE' }])
  })

  test('clears a pending delete when an independent refresh confirms removal', async () => {
    const deleteRequest = deferred()
    let snapshot = [{ id: 'to-delete', status: 'downloaded' }]
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'DELETE') return deleteRequest.promise
      return Promise.resolve(modelsResponse(snapshot))
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    let deletePromise
    act(() => {
      deletePromise = result.current.deleteModel('to-delete')
    })
    await waitFor(() => expect(result.current.actionLoading).toBe('to-delete'))

    snapshot = []
    await act(async () => {
      await result.current.refresh()
    })
    expect(result.current.actionLoading).toBeNull()

    await act(async () => {
      deleteRequest.resolve({ ok: true })
      await deletePromise
    })
  })

  test('shows the backend delete explanation instead of a generic failure', async () => {
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'DELETE') {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: () => Promise.resolve({ detail: 'The active model must be stopped before deleting its file.' })
        })
      }
      return Promise.resolve(modelsResponse([{ id: 'to-delete', status: 'downloaded' }]))
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.deleteModel('to-delete')
    })

    expect(result.current.error).toBe('The active model must be stopped before deleting its file.')
    expect(result.current.actionLoading).toBeNull()
  })

  test('does not erase a mutation error when background list polling succeeds', async () => {
    vi.useFakeTimers()
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'DELETE') {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: () => Promise.resolve({ detail: 'Delete blocked by the active runtime.' })
        })
      }
      return Promise.resolve(modelsResponse([{ id: 'to-delete', status: 'downloaded' }]))
    })

    try {
      const { result } = renderHook(() => useModels())
      await act(async () => {})

      await act(async () => {
        await result.current.deleteModel('to-delete')
      })
      expect(result.current.error).toBe('Delete blocked by the active runtime.')

      await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
      expect(result.current.error).toBe('Delete blocked by the active runtime.')
    } finally {
      vi.useRealTimers()
    }
  })

  test('keeps activation working while an overlapping model mutation settles', async () => {
    vi.useFakeTimers()
    const target = 'slow-model'
    const otherModel = 'other-model'
    const deleteRequest = deferred()
    let currentModel = null
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'POST') return Promise.resolve({ ok: true })
      if (opts?.method === 'DELETE') return deleteRequest.promise
      return Promise.resolve(modelsResponse(
        [
          { id: target, status: currentModel ? 'loaded' : 'downloaded' },
          { id: otherModel, status: 'downloaded' },
        ],
        { currentModel }
      ))
    })

    try {
      const { result } = renderHook(() => useModels())
      await act(async () => {})

      let loadPromise
      act(() => {
        loadPromise = result.current.loadModel(target)
      })
      expect(result.current.actionLoading).toBe(target)
      expect(result.current.actionLoadingModels).toEqual([target])

      let deletePromise
      act(() => {
        deletePromise = result.current.deleteModel(otherModel)
      })
      expect(result.current.actionLoading).toBe(target)
      expect(result.current.actionLoadingModels).toEqual([target, otherModel])

      await act(async () => {
        deleteRequest.resolve({ ok: true })
        await deletePromise
      })
      expect(result.current.actionLoading).toBe(target)
      expect(result.current.actionLoadingModels).toEqual([target])

      currentModel = target
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
        await loadPromise
      })
      expect(result.current.actionLoading).toBeNull()
      expect(result.current.actionLoadingModels).toEqual([])
    } finally {
      vi.useRealTimers()
    }
  })

  test('keeps activation pending beyond 150 seconds until the requested model is loaded', async () => {
    vi.useFakeTimers()
    const target = 'slow-model'
    let currentModel = null
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'POST') return Promise.resolve({ ok: true })
      return Promise.resolve(modelsResponse(
        [{ id: target, status: currentModel ? 'loaded' : 'downloaded' }],
        { currentModel }
      ))
    })

    try {
      const { result } = renderHook(() => useModels())
      await act(async () => {})

      let loadPromise
      act(() => {
        loadPromise = result.current.loadModel(target)
      })

      await act(async () => { await vi.advanceTimersByTimeAsync(155000) })
      expect(result.current.actionLoading).toBe(target)
      expect(result.current.error).toBeNull()

      currentModel = target
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
        await loadPromise
      })
      expect(result.current.currentModel).toBe(target)
      expect(result.current.actionLoading).toBeNull()
      expect(result.current.error).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  test('keeps activation pending while backend activation lifecycle is still active', async () => {
    vi.useFakeTimers()
    const target = 'warmup-model'
    let currentModel = null
    let activationActive = false
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'POST') return Promise.resolve({ ok: true })
      return Promise.resolve(modelsResponse(
        [{ id: target, status: currentModel ? 'loaded' : 'downloaded' }],
        {
          currentModel,
          modelLifecycle: activationActive
            ? {
                active: true,
                operation: 'model_activation',
                target,
                modelId: target,
              }
            : null,
        }
      ))
    })

    try {
      const { result } = renderHook(() => useModels())
      await act(async () => {})

      let loadPromise
      act(() => {
        loadPromise = result.current.loadModel(target)
      })

      currentModel = target
      activationActive = true
      await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
      expect(result.current.currentModel).toBe(target)
      expect(result.current.actionLoading).toBe(target)
      expect(result.current.error).toBeNull()

      activationActive = false
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
        await loadPromise
      })
      expect(result.current.actionLoading).toBeNull()
      expect(result.current.error).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  test('holds the activation lock through 600 seconds and then reports a terminal timeout', async () => {
    vi.useFakeTimers()
    const target = 'never-loads'
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'POST') return Promise.resolve({ ok: true })
      return Promise.resolve(modelsResponse([{ id: target, status: 'downloaded' }]))
    })

    try {
      const { result } = renderHook(() => useModels())
      await act(async () => {})

      let loadPromise
      act(() => {
        loadPromise = result.current.loadModel(target)
      })

      await act(async () => { await vi.advanceTimersByTimeAsync(600000) })
      expect(result.current.actionLoading).toBe(target)
      expect(result.current.error).toBeNull()

      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000)
        await loadPromise
      })
      expect(result.current.actionLoading).toBeNull()
      expect(result.current.error).toMatch(/timed out after 10 minutes/i)
      expect(result.current.error).toContain(target)
    } finally {
      vi.useRealTimers()
    }
  })

  test.each([
    ['root payload', { detail: 'Activation already in progress', activeModelId: 'same-target' }],
    ['nested detail payload', { detail: { message: 'Activation already in progress', activeModelId: 'same-target' } }],
  ])('continues waiting for a same-target 409 from a %s', async (_label, conflictBody) => {
    vi.useFakeTimers()
    const target = 'same-target'
    let currentModel = null
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'POST') {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: () => Promise.resolve(conflictBody),
        })
      }
      return Promise.resolve(modelsResponse(
        [{ id: target, status: currentModel ? 'loaded' : 'downloaded' }],
        { currentModel }
      ))
    })

    try {
      const { result } = renderHook(() => useModels())
      await act(async () => {})

      let loadPromise
      act(() => {
        loadPromise = result.current.loadModel(target)
      })

      await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
      expect(result.current.actionLoading).toBe(target)
      expect(result.current.error).toBeNull()

      currentModel = target
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
        await loadPromise
      })
      expect(result.current.actionLoading).toBeNull()
      expect(result.current.error).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  test.each([
    [
      'different target',
      { detail: { error: 'Activation already in progress', activeModelId: 'other-model' } },
      /active target: other-model; requested target: requested-model/i,
    ],
    [
      'unknown target',
      { detail: 'Activation already in progress' },
      /did not identify the active target/i,
    ],
  ])('rejects a 409 with a %s instead of joining it', async (_label, conflictBody, expectedError) => {
    vi.useFakeTimers()
    const baseline = 'baseline-model'
    const target = 'requested-model'
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'POST') {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: () => Promise.resolve(conflictBody),
        })
      }
      return Promise.resolve(modelsResponse(
        [
          { id: baseline, status: 'loaded' },
          { id: target, status: 'downloaded' },
        ],
        { currentModel: baseline }
      ))
    })

    try {
      const { result } = renderHook(() => useModels())
      await act(async () => {})

      let loadPromise
      act(() => {
        loadPromise = result.current.loadModel(target)
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
        await loadPromise
      })

      expect(result.current.currentModel).toBe(baseline)
      expect(result.current.actionLoading).toBeNull()
      expect(result.current.error).toMatch(expectedError)
    } finally {
      vi.useRealTimers()
    }
  })

  test('late mutation settlement does not clear a newer action', async () => {
    const oldDownload = deferred()
    const newDownload = deferred()
    fetch.mockImplementation((url, opts) => {
      if (opts?.method === 'POST') {
        return String(url).includes('old-model') ? oldDownload.promise : newDownload.promise
      }
      return Promise.resolve(modelsResponse([
        { id: 'old-model', status: 'available' },
        { id: 'new-model', status: 'available' }
      ]))
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    let oldPromise
    act(() => {
      oldPromise = result.current.downloadModel('old-model')
    })
    await waitFor(() => expect(result.current.actionLoading).toBe('old-model'))

    let newPromise
    act(() => {
      newPromise = result.current.downloadModel('new-model')
    })
    await waitFor(() => expect(result.current.actionLoading).toBe('new-model'))

    await act(async () => {
      oldDownload.resolve({ ok: true })
      await oldPromise
    })
    expect(result.current.actionLoading).toBe('new-model')

    await act(async () => {
      newDownload.resolve({ ok: true })
      await newPromise
    })
    expect(result.current.actionLoading).toBeNull()
  })

  test('keeps only one prompt poll in flight while a model action is pending', async () => {
    vi.useFakeTimers()
    const downloadRequest = deferred()
    const pendingPoll = deferred()
    let getCount = 0
    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'POST') return downloadRequest.promise
      getCount += 1
      if (getCount === 1) return Promise.resolve(modelsResponse([{ id: 'new-model', status: 'available' }]))
      return pendingPoll.promise
    })

    try {
      const { result } = renderHook(() => useModels())
      await act(async () => {})
      expect(getCount).toBe(1)

      let downloadPromise
      act(() => {
        downloadPromise = result.current.downloadModel('new-model')
      })
      expect(result.current.actionLoading).toBe('new-model')

      await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
      expect(getCount).toBe(2)
      await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
      expect(getCount).toBe(2)

      await act(async () => {
        pendingPoll.resolve(modelsResponse([{ id: 'new-model', status: 'downloaded' }]))
        await pendingPoll.promise
      })
      expect(result.current.actionLoading).toBeNull()

      await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
      expect(getCount).toBe(2)

      downloadRequest.resolve({ ok: true })
      await act(async () => { await downloadPromise })
    } finally {
      vi.useRealTimers()
    }
  })

  test('recovers polling after a models request exceeds its deadline', async () => {
    vi.useFakeTimers()
    let getCount = 0
    fetch.mockImplementation((_url, options) => {
      getCount += 1
      if (getCount > 1) {
        return Promise.resolve(modelsResponse([{ id: 'recovered', status: 'loaded' }]))
      }
      return new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => {
          const error = new Error('The operation was aborted.')
          error.name = 'AbortError'
          reject(error)
        }, { once: true })
      })
    })

    try {
      const { result } = renderHook(() => useModels())
      expect(getCount).toBe(1)

      await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
      expect(result.current.loading).toBe(false)
      expect(getCount).toBe(2)
      expect(result.current.models[0].id).toBe('recovered')
      expect(result.current.error).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  test('does not let an older models response overwrite a newer snapshot', async () => {
    const firstRequest = deferred()
    const secondRequest = deferred()
    let getCount = 0
    fetch.mockImplementation(() => {
      getCount += 1
      return getCount === 1 ? firstRequest.promise : secondRequest.promise
    })

    const { result } = renderHook(() => useModels())
    expect(getCount).toBe(1)

    let refreshPromise
    act(() => {
      refreshPromise = result.current.refresh()
    })
    expect(getCount).toBe(2)

    await act(async () => {
      secondRequest.resolve(modelsResponse([{ id: 'new-snapshot', status: 'loaded' }]))
      await refreshPromise
    })
    expect(result.current.models[0].id).toBe('new-snapshot')

    await act(async () => {
      firstRequest.resolve(modelsResponse([{ id: 'stale-snapshot', status: 'available' }]))
      await firstRequest.promise
      await Promise.resolve()
    })
    expect(result.current.models[0].id).toBe('new-snapshot')
  })

  test('benchmarkModel calls POST and refreshes', async () => {
    fetch.mockImplementation((url, opts) => {
      if (opts?.method === 'POST' && String(url).includes('/benchmark')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ tokensPerSecond: 42 }) })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ models: [{ id: 'qwen3.5-9b-q4' }], gpu: null, currentModel: 'qwen3.5-9b-q4' })
      })
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.benchmarkModel('qwen3.5-9b-q4')
    })

    const benchmarkCall = fetch.mock.calls.find(c => c[1]?.method === 'POST' && String(c[0]).includes('/benchmark'))
    expect(benchmarkCall).toBeTruthy()
    expect(benchmarkCall[0]).toContain('qwen3.5-9b-q4')
    expect(benchmarkCall[1].body).toContain('max_tokens')
  })

  test('pauses 30s polling while the tab is hidden and refreshes on visibilitychange', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ models: [], gpu: null, currentModel: null })
    })

    vi.useFakeTimers()
    try {
      renderHook(() => useModels())
      await act(async () => {})
      expect(fetch).toHaveBeenCalledTimes(1)

      setDocumentHidden(true)
      await act(async () => { await vi.advanceTimersByTimeAsync(90000) })
      expect(fetch).toHaveBeenCalledTimes(1)

      setDocumentHidden(false)
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
      })
      expect(fetch).toHaveBeenCalledTimes(2)

      await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
      expect(fetch).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })

  test('deleteModel does not depend on native confirm cancellation', async () => {
    vi.stubGlobal('confirm', vi.fn(() => false))

    fetch.mockImplementation((_url, opts) => {
      if (opts?.method === 'DELETE') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }
      return Promise.resolve(modelsResponse([{ id: 'keep-me', status: 'downloaded' }]))
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.deleteModel('keep-me')
    })

    expect(confirm).not.toHaveBeenCalled()
    expect(fetch.mock.calls.some(call => call[1]?.method === 'DELETE')).toBe(true)
  })
})
