import { act, renderHook, waitFor } from '@testing-library/react'
import { useFirstRun } from '../useFirstRun'

const response = firstRun => ({ ok: true, json: async () => ({ first_run: firstRun }) })

afterEach(() => vi.restoreAllMocks())

it('commits only the newest overlapping setup-status response', async () => {
  let resolveFirst
  let resolveSecond
  vi.stubGlobal('fetch', vi.fn()
    .mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve }))
    .mockImplementationOnce(() => new Promise(resolve => { resolveSecond = resolve })))

  const { result } = renderHook(() => useFirstRun())
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce())
  act(() => { result.current.refresh() })
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))

  await act(async () => { resolveSecond(response(false)); await Promise.resolve() })
  await act(async () => { resolveFirst(response(true)); await Promise.resolve() })

  expect(result.current.firstRun).toBe(false)
  expect(result.current.error).toBeNull()
})
