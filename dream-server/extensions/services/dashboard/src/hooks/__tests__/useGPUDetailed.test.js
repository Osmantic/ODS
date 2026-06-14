import { renderHook, waitFor } from '@testing-library/react'
import { useGPUDetailed } from '../useGPUDetailed'


describe('useGPUDetailed', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('does not block GPU metrics while Lemonade runtime diagnostics are pending', async () => {
    let resolveRuntime
    const runtimeResponse = new Promise(resolve => {
      resolveRuntime = resolve
    })
    const payloads = {
      '/api/gpu/detailed': { gpu_count: 1, backend: 'amd', gpus: [] },
      '/api/gpu/history': { timestamps: [], gpus: {} },
      '/api/gpu/topology': null,
    }

    fetch.mockImplementation(url => {
      if (url === '/api/gpu/amd-runtime') return runtimeResponse
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(payloads[url]),
      })
    })

    const { result, unmount } = renderHook(() => useGPUDetailed())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.detailed.backend).toBe('amd')
    expect(result.current.runtime).toBeNull()

    resolveRuntime({
      ok: true,
      json: () => Promise.resolve({ runtime: 'lemonade', providerStatus: 'ready' }),
    })
    await waitFor(() => {
      expect(result.current.runtime?.providerStatus).toBe('ready')
    })

    unmount()
  })

  test('runs active Lemonade diagnostics only through the explicit action', async () => {
    const payloads = {
      '/api/gpu/detailed': { gpu_count: 1, backend: 'amd', gpus: [] },
      '/api/gpu/history': { timestamps: [], gpus: {} },
      '/api/gpu/topology': null,
      '/api/gpu/amd-runtime': { providerProbeMode: 'passive', providerStatus: 'unverified' },
      '/api/gpu/amd-runtime/probe': { providerProbeMode: 'active', providerStatus: 'ready' },
    }
    fetch.mockImplementation((url, options = {}) => Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(payloads[url]),
      method: options.method,
    }))

    const { result } = renderHook(() => useGPUDetailed())
    await waitFor(() => expect(result.current.runtime?.providerProbeMode).toBe('passive'))

    await result.current.runRuntimeProbe()
    await waitFor(() => expect(result.current.runtime?.providerProbeMode).toBe('active'))

    expect(fetch).toHaveBeenCalledWith('/api/gpu/amd-runtime/probe', { method: 'POST' })
  })

  test('does not let an older passive response overwrite a completed active probe', async () => {
    let resolvePassive
    const passiveResponse = new Promise(resolve => {
      resolvePassive = resolve
    })
    fetch.mockImplementation(url => {
      if (url === '/api/gpu/amd-runtime') return passiveResponse
      if (url === '/api/gpu/amd-runtime/probe') {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ providerProbeMode: 'active', providerStatus: 'ready' }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(url === '/api/gpu/detailed' ? { gpu_count: 1, backend: 'amd', gpus: [] } : {}),
      })
    })

    const { result } = renderHook(() => useGPUDetailed())
    await waitFor(() => expect(result.current.loading).toBe(false))
    await result.current.runRuntimeProbe()
    await waitFor(() => expect(result.current.runtime?.providerProbeMode).toBe('active'))

    resolvePassive({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ providerProbeMode: 'passive', providerStatus: 'unverified' }),
    })
    await Promise.resolve()

    expect(result.current.runtime.providerProbeMode).toBe('active')
  })
})
