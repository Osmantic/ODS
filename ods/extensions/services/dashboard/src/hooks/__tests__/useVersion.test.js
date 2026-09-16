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

  test('suppresses update banner for a previously dismissed version', async () => {
    globalThis.localStorage.setItem('dismissed-update', '2.0.0')
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ current: '1.0.0', latest: '2.0.0', update_available: true })
    })

    const { result } = renderHook(() => useVersion())

    await waitFor(() => {
      expect(result.current.version).toBeTruthy()
    })
    // Dismissed 2.0.0 → banner stays hidden even though the API reports it.
    expect(result.current.version.update_available).toBe(false)
  })

  test('re-surfaces the banner when a newer version supersedes the dismissed one', async () => {
    globalThis.localStorage.setItem('dismissed-update', '2.0.0')
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ current: '1.0.0', latest: '3.0.0', update_available: true })
    })

    const { result } = renderHook(() => useVersion())

    await waitFor(() => {
      expect(result.current.version).toBeTruthy()
    })
    // 3.0.0 != dismissed 2.0.0 → the new release still surfaces.
    expect(result.current.version.update_available).toBe(true)
  })

  test('still exposes version data when reading localStorage throws', async () => {
    vi.spyOn(globalThis.Storage.prototype, 'getItem').mockImplementation(() => {
      throw new globalThis.DOMException('Storage is blocked', 'SecurityError')
    })
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ current: '1.0.0', latest: '2.0.0', update_available: true })
    })

    const { result } = renderHook(() => useVersion())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.version.latest).toBe('2.0.0')
    expect(result.current.version.update_available).toBe(true)
    expect(result.current.error).toBeNull()
  })

  test('dismisses the current banner when writing localStorage throws', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ current: '1.0.0', latest: '2.0.0', update_available: true })
    })
    const { result } = renderHook(() => useVersion())
    await waitFor(() => expect(result.current.version).toBeTruthy())
    vi.spyOn(globalThis.Storage.prototype, 'setItem').mockImplementation(() => {
      throw new globalThis.DOMException('Storage is blocked', 'SecurityError')
    })

    act(() => result.current.dismissUpdate())

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

  test('loads and dismisses the banner when the localStorage getter itself is denied', async () => {
    vi.spyOn(globalThis, 'localStorage', 'get').mockImplementation(() => {
      throw new globalThis.DOMException('Storage denied', 'SecurityError')
    })
    fetch.mockResolvedValue({ ok: true, json: async () => ({ current: '1', latest: '2', update_available: true }) })
    const { result } = renderHook(() => useVersion())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.version.latest).toBe('2')
    expect(result.current.error).toBeNull()
    act(() => result.current.dismissUpdate())
    expect(result.current.version.update_available).toBe(false)
  })
})
