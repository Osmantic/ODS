import { renderHook, waitFor } from '@testing-library/react'
import { useFirstRun } from '../useFirstRun'

describe('useFirstRun', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('does not coerce a string first_run flag into first-run mode', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ first_run: 'false' }),
    })

    const { result } = renderHook(() => useFirstRun())

    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.firstRun).toBe(false)
    expect(result.current.error).toBe('setup-status returned an invalid first_run value')
  })

  test.each([true, false])('accepts the boolean first_run contract (%s)', async (firstRun) => {
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ first_run: firstRun }),
    })

    const { result } = renderHook(() => useFirstRun())

    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.firstRun).toBe(firstRun)
    expect(result.current.error).toBeNull()
  })
})
