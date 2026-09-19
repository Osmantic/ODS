import { act, renderHook } from '@testing-library/react'
import { useFirstRun } from '../useFirstRun'

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

afterEach(() => vi.unstubAllGlobals())

test.each(['success', 'failure'])('ignores stale setup %s after the completion refresh', async (outcome) => {
  const older = deferred()
  const newer = deferred()
  vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise))
  const { result } = renderHook(() => useFirstRun())
  let refresh
  act(() => { refresh = result.current.refresh() })
  await act(async () => {
    newer.resolve({ ok: true, json: async () => ({ first_run: false }) })
    await refresh
  })
  expect(result.current.firstRun).toBe(false)
  await act(async () => {
    if (outcome === 'success') older.resolve({ ok: true, json: async () => ({ first_run: true }) })
    else older.reject(new Error('old request failed'))
  })
  expect(result.current.firstRun).toBe(false)
  expect(result.current.error).toBeNull()
  expect(result.current.loading).toBe(false)
})

test('an older completion cannot clear loading while the latest refresh is pending', async () => {
  const older = deferred()
  const newer = deferred()
  vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise))
  const { result } = renderHook(() => useFirstRun())
  let refresh
  act(() => { refresh = result.current.refresh() })
  await act(async () => { older.resolve({ ok: true, json: async () => ({ first_run: true }) }) })
  expect(result.current.loading).toBe(true)
  await act(async () => {
    newer.resolve({ ok: true, json: async () => ({ first_run: false }) })
    await refresh
  })
  expect(result.current.loading).toBe(false)
})
