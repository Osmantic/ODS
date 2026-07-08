import { renderHook, waitFor, act } from '@testing-library/react'
import { useModels } from '../useModels'

// Shadow jsdom's Document.prototype.hidden getter on the instance; deleting
// the own property in afterEach restores the prototype behavior.
const setDocumentHidden = (hidden) => {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
}

describe('useModels', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.restoreAllMocks()
    delete document.hidden
  })

  test('fetches models on mount', async () => {
    const mockData = {
      models: [{ id: 'qwen-32b', name: 'Qwen2.5 32B' }],
      gpu: { vramTotal: 16 },
      currentModel: 'qwen-32b',
      configuredModel: 'qwen-32b',
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
    expect(result.current.recommendationAlternatives[0].id).toBe('qwen-32b')
    expect(result.current.error).toBeNull()
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
    expect(postCall).toBeTruthy()
    expect(postCall[0]).toContain('new-model')
    expect(postCall[0]).toContain('/download')
  })

  test('deleteModel calls DELETE and refreshes', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))

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
      await result.current.deleteModel('to-delete')
    })

    expect(confirm).toHaveBeenCalled()
    const deleteCall = fetch.mock.calls.find(c => c[1]?.method === 'DELETE')
    expect(deleteCall).toBeTruthy()
    expect(deleteCall[0]).toContain('to-delete')
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

  test('deleteModel aborts when user cancels confirm', async () => {
    vi.stubGlobal('confirm', vi.fn(() => false))

    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ models: [{ id: 'keep-me' }], gpu: null, currentModel: null })
    })

    const { result } = renderHook(() => useModels())
    await waitFor(() => expect(result.current.loading).toBe(false))

    const callCountBefore = fetch.mock.calls.length

    await act(async () => {
      await result.current.deleteModel('keep-me')
    })

    expect(fetch.mock.calls.length).toBe(callCountBefore)
  })
})
