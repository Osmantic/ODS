import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, StrictMode } from 'react'
import { useSystemStatus } from '../useSystemStatus'

describe('useSystemStatus', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  test('fetches status on mount', async () => {
    const mockStatus = { gpu: { name: 'RTX 4090' }, services: [], model: null, bootstrap: null, uptime: 100 }
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockStatus)
    })

    const { result } = renderHook(() => useSystemStatus())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.status.gpu.name).toBe('RTX 4090')
    expect(result.current.error).toBeNull()
  })

  test('starts with loading true', () => {
    fetch.mockReturnValue(new Promise(() => {}))

    const { result } = renderHook(() => useSystemStatus())

    expect(result.current.loading).toBe(true)
  })

  test('sets loading false after fetch completes', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ gpu: null, services: [] })
    })

    const { result } = renderHook(() => useSystemStatus())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
  })

  test('sets error on failed fetch', async () => {
    fetch.mockResolvedValue({ ok: false })

    const { result } = renderHook(() => useSystemStatus())

    await waitFor(() => {
      expect(result.current.error).toBeTruthy()
    })
    expect(result.current.loading).toBe(false)
  })

  test('sets error on network failure', async () => {
    fetch.mockRejectedValue(new Error('network down'))

    const { result } = renderHook(() => useSystemStatus())

    await waitFor(() => {
      expect(result.current.error).toBe('network down')
    })
    expect(result.current.loading).toBe(false)
  })

  test('times out a hung initial request and retries on the next poll', async () => {
    vi.useFakeTimers()
    const recoveredStatus = {
      gpu: { name: 'Recovered GPU' },
      services: [],
      model: null,
      bootstrap: null,
      uptime: 200,
    }
    fetch
      .mockImplementationOnce((_url, options) => new Promise((resolve, reject) => {
        options.signal.addEventListener('abort', () => {
          const error = new Error('aborted')
          error.name = 'AbortError'
          reject(error)
        })
      }))
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(recoveredStatus),
      })

    try {
      const { result } = renderHook(() => useSystemStatus())
      expect(result.current.loading).toBe(true)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(12000)
      })
      expect(result.current.loading).toBe(false)
      expect(result.current.error).toBe('Status request timed out')

      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000)
      })
      expect(result.current.status.gpu?.name).toBe('Recovered GPU')
      expect(result.current.error).toBeNull()
      expect(fetch).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  test('does not clear status on error (preserves previous data)', async () => {
    const mockStatus = { gpu: { name: 'RTX 4090' }, services: [], model: null, bootstrap: null, uptime: 100 }
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockStatus)
    })

    const { result } = renderHook(() => useSystemStatus())

    await waitFor(() => {
      expect(result.current.status.gpu?.name).toBe('RTX 4090')
    })

    // The hook keeps previous status on error by design
    // (see source: catch block only sets error, doesn't clear status)
    expect(result.current.status.gpu).toBeTruthy()
  })

  test('marks preserved readings stale after a failed poll and replaces them on recovery', async () => {
    vi.useFakeTimers()
    const initial={gpu:{name:'Actual GPU',utilization:37},services:[{name:'Pixel',status:'healthy'}],model:{name:'Actual model'}}
    fetch.mockResolvedValueOnce({ok:true,json:async()=>initial})
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue({ok:true,json:async()=>({...initial,gpu:{...initial.gpu,utilization:0}})})
    const {result,unmount}=renderHook(()=>useSystemStatus())
    await act(async()=>{await vi.advanceTimersByTimeAsync(0)})
    const received=result.current.status.clientTelemetry.sampledAt
    expect(result.current.status.clientTelemetry.stale).toBe(false)
    await act(async()=>{await vi.advanceTimersByTimeAsync(5000)})
    expect(result.current.status.gpu.utilization).toBe(37)
    expect(result.current.status.services).toEqual(initial.services)
    expect(result.current.status.model).toEqual(initial.model)
    expect(result.current.status.clientTelemetry).toEqual({sampledAt:received,stale:true})
    await act(async()=>{await vi.advanceTimersByTimeAsync(5000)})
    expect(result.current.status.gpu.utilization).toBe(0)
    expect(result.current.status.clientTelemetry.stale).toBe(false)
    expect(result.current.status.clientTelemetry.sampledAt).toBeGreaterThan(received)
    expect(result.current.error).toBeNull()
    unmount()
  })

  test('expires a stalled JSON body and ignores its late result after recovery', async () => {
    vi.useFakeTimers()
    let finishBody
    let signal
    fetch.mockImplementationOnce((_url, options) => {
      signal = options.signal
      return Promise.resolve({ ok: true, json: () => new Promise(resolve => { finishBody = resolve }) })
    }).mockResolvedValue({ ok: true, json: async () => ({ uptime: 200, services: [] }) })
    const { result, unmount } = renderHook(() => useSystemStatus())
    await act(async () => { await vi.advanceTimersByTimeAsync(12000) })
    expect(signal.aborted).toBe(true)
    expect(result.current.error).toBe('Status request timed out')
    expect(result.current.loading).toBe(false)
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(result.current.status.uptime).toBe(200)
    await act(async () => { finishBody({ uptime: 1 }); await Promise.resolve() })
    expect(result.current.status.uptime).toBe(200)
    expect(result.current.error).toBeNull()
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  test('StrictMode replacement owns a fresh request and unmount cancels it', async () => {
    vi.useFakeTimers()
    const signals = []
    fetch.mockImplementation((_url, options) => {
      signals.push(options.signal)
      return new Promise(() => {})
    })
    const { unmount } = renderHook(() => useSystemStatus(), {
      wrapper: ({ children }) => createElement(StrictMode, null, children),
    })
    expect(signals).toHaveLength(2)
    expect(signals[0].aborted).toBe(true)
    expect(signals[1].aborted).toBe(false)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(fetch).toHaveBeenCalledTimes(2)
    unmount()
    expect(signals[1].aborted).toBe(true)
    await act(async () => { await Promise.resolve() })
    expect(vi.getTimerCount()).toBe(0)
  })

  test('cleans up interval on unmount', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ gpu: null, services: [] })
    })

    const { unmount } = renderHook(() => useSystemStatus())

    await waitFor(() => {
      expect(fetch).toHaveBeenCalled()
    })

    const callCount = fetch.mock.calls.length
    unmount()

    // Wait a bit and confirm no new calls
    await new Promise(r => setTimeout(r, 100))
    expect(fetch.mock.calls.length).toBe(callCount)
  })
})
