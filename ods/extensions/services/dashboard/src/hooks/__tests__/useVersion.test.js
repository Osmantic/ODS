import { renderHook, waitFor, act } from '@testing-library/react'
import { useVersion } from '../useVersion'

describe('useVersion', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    globalThis.localStorage.removeItem('dismissed-update')
  })

  afterEach(() => {
    vi.useRealTimers()
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

  test('dismissUpdate hides the notice while retaining release metadata', async () => {
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
    expect(result.current.version.update_available).toBe(true)
    expect(result.current.showUpdate).toBe(false)
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
    expect(result.current.showUpdate).toBe(false)
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

  test('handles fetch error gracefully', async () => {
    fetch.mockRejectedValue(new Error('network error'))

    const { result } = renderHook(() => useVersion())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.error).toBeTruthy()
    expect(result.current.version).toBeNull()
  })

  test('retains the last good snapshot across a failed poll and clears the error on recovery', async () => {
    vi.useFakeTimers()
    fetch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ current: '1.0.0', latest: '2.0.0', update_available: true })
      })
      .mockRejectedValueOnce(new Error('dashboard-api restarting'))
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ current: '1.0.0', latest: '2.1.0', update_available: true })
      })

    const { result } = renderHook(() => useVersion())
    await act(async () => { await Promise.resolve() })

    expect(result.current.version.latest).toBe('2.0.0')

    await act(async () => { await vi.advanceTimersByTimeAsync(30 * 60 * 1000) })
    expect(result.current.error).toBe('dashboard-api restarting')
    expect(result.current.version.latest).toBe('2.0.0')

    await act(async () => { await vi.advanceTimersByTimeAsync(30 * 60 * 1000) })
    expect(result.current.error).toBeNull()
    expect(result.current.version.latest).toBe('2.1.0')
  })

  test.each(['HTTP', 'JSON'])('retains a dismissed snapshot after a %s failure', async (failure) => {
    vi.useFakeTimers()
    fetch.mockResolvedValueOnce({ ok: true, json: async () => ({ current: '1', latest: '2', update_available: true }) })
      .mockResolvedValueOnce(failure === 'HTTP'
        ? { ok: false }
        : { ok: true, json: async () => { throw new SyntaxError('invalid version JSON') } })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ current: '1', latest: '3', update_available: true }) })
    const { result, unmount } = renderHook(() => useVersion())
    await act(async () => { await Promise.resolve() })
    act(() => result.current.dismissUpdate())
    await act(async () => { await vi.advanceTimersByTimeAsync(30 * 60 * 1000) })
    expect(result.current.version).toMatchObject({ latest: '2' })
    expect(result.current.showUpdate).toBe(false)
    expect(result.current.error).toBeTruthy()
    await act(async () => { await vi.advanceTimersByTimeAsync(30 * 60 * 1000) })
    expect(result.current.version).toMatchObject({ latest: '3', update_available: true })
    expect(result.current.error).toBeNull()
    unmount()
  })

  test('does not announce a stale release and retries a pending first check', async () => {
    vi.useFakeTimers()
    fetch.mockResolvedValueOnce({ ok:true, json:async () => ({ current:'2.6.0', latest:null, check_status:'checking' }) })
      .mockResolvedValueOnce({ ok:true, json:async () => ({ current:'2.6.0', latest:'2.7.0', update_available:true, check_status:'checked' }) })
    const { result, unmount } = renderHook(() => useVersion())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current.showUpdate).toBe(false)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(result.current.showUpdate).toBe(true)
    unmount()
    vi.useRealTimers()
  })

  test('storage failure does not hide a confirmed update or prevent dismissal', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('disabled') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('disabled') })
    fetch.mockResolvedValue({ ok:true, json:async () => ({ current:'2.6.0', latest:'2.7.0', update_available:true, check_status:'checked' }) })
    const { result } = renderHook(() => useVersion())
    await waitFor(() => expect(result.current.showUpdate).toBe(true))
    act(() => result.current.dismissUpdate())
    expect(result.current.showUpdate).toBe(false)
    expect(result.current.version.latest).toBe('2.7.0')
  })
})
