import { renderHook, waitFor } from '@testing-library/react'

import { useSessionBootstrap } from '../useSessionBootstrap'

describe('useSessionBootstrap', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('mints a session after verify explicitly rejects the cookie', async () => {
    globalThis.fetch
      .mockResolvedValueOnce({ ok: false, status: 401 })
      .mockResolvedValueOnce({ ok: true, status: 200 })

    renderHook(() => useSessionBootstrap())

    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(2))
    expect(globalThis.fetch).toHaveBeenNthCalledWith(
      2,
      '/api/auth/admin-session',
      { method: 'POST', credentials: 'same-origin' },
    )
  })

  test('does not mint a session when verify is temporarily unavailable', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    globalThis.fetch.mockResolvedValueOnce({ ok: false, status: 503 })

    renderHook(() => useSessionBootstrap())

    await waitFor(() => expect(warn).toHaveBeenCalledWith(
      '[ods-session] verify-session returned',
      503,
    ))
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })
})
