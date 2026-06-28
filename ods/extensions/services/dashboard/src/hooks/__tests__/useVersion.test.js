import { renderHook, waitFor, act } from '@testing-library/react'
import { useVersion } from '../useVersion'

describe('useVersion', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    globalThis.localStorage.removeItem('dismissed-update')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('fetches version on mount', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ current: '1.0.0', latest: '1.1.0', update_available: true })
    })

    const { result } = renderHook(() => useVersion())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.version.current).toBe('1.0.0')
    expect(result.current.version.latest).toBe('1.1.0')
    expect(result.current.version.update_available).toBe(true)
    expect(result.current.error).toBeNull()
  })

  test('identifies update available', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ current: '1.0.0', latest: '2.0.0', update_available: true })
    })

    const { result } = renderHook(() => useVersion())

    await waitFor(() => {
      expect(result.current.version?.update_available).toBe(true)
    })
  })

  test('dismissUpdate stores in localStorage and clears flag', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ current: '1.0.0', latest: '2.0.0', update_available: true })
    })

    const { result } = renderHook(() => useVersion())

    await waitFor(() => {
      expect(result.current.version).toBeTruthy()
    })

    act(() => {
      result.current.dismissUpdate()
    })

    expect(globalThis.localStorage.getItem('dismissed-update')).toBe('2.0.0')
    expect(result.current.version.update_available).toBe(false)
  })

  test('handles fetch error gracefully', async () => {
    fetch.mockRejectedValue(new Error('network error'))

    const { result } = renderHook(() => useVersion())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.error).toBeTruthy()
    expect(result.current.version).toBeNull()
  })
})
