import { act, renderHook, waitFor } from '@testing-library/react'
import { useFirstRun } from './useFirstRun'

afterEach(() => vi.unstubAllGlobals())

test('keeps browser onboarding distinct from saved installation settings', async () => {
  const installation = { selection_saved: true, tier: 'T4', gpu_backend: 'apple', mode: 'local' }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true, json: async () => ({ first_run: true, installation }),
  }))
  const { result } = renderHook(() => useFirstRun())
  await waitFor(() => expect(result.current.loading).toBe(false))
  expect(result.current.firstRun).toBe(true)
  expect(result.current.installation).toEqual(installation)
})

test('does not reuse saved selection after the setup status request fails', async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce({
    ok: true, json: async () => ({ first_run: true, installation: { selection_saved: true } }),
  }).mockResolvedValueOnce({ ok: false, status: 503 })
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useFirstRun())
  await waitFor(() => expect(result.current.loading).toBe(false))
  await act(async () => result.current.refresh())
  expect(result.current.firstRun).toBe(false)
  expect(result.current.installation).toBeNull()
  expect(result.current.error).toContain('503')
})
