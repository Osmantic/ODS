import { renderHook, waitFor } from '@testing-library/react'
import { useFirstRun } from './useFirstRun'

describe('useFirstRun', () => {
  it('does not report a normal workspace before setup status resolves', async () => {
    let resolveResponse
    vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { resolveResponse = resolve })))
    const { result } = renderHook(() => useFirstRun())

    expect(result.current.loading).toBe(true)
    expect(result.current.firstRun).toBe(null)

    resolveResponse({ ok: true, json: async () => ({ first_run: false }) })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.firstRun).toBe(false)
  })
})
