import { act, fireEvent, render, screen } from '@testing-library/react'
import HuggingFaceModelBrowser from './HuggingFaceModelBrowser'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

test.each(['resolve', 'abort'])('keeps the current Hub search when an obsolete response body completes: %s', async outcome => {
  vi.useFakeTimers()
  let resolveOld, rejectOld
  const oldBody = new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject })
  const model = { id: 'current/new-model', author: 'current', name: 'new-model' }
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: true, json: () => oldBody })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ models: [model], authenticated: true, stale: false }) })
  vi.stubGlobal('fetch', fetchMock)
  render(<HuggingFaceModelBrowser />)
  await act(async () => vi.advanceTimersByTimeAsync(350))
  fireEvent.change(screen.getByPlaceholderText('Search repositories, authors, or model families...'), { target: { value: 'new-model' } })
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
  await act(async () => vi.advanceTimersByTimeAsync(350))
  expect(screen.getByText('Authenticated')).toBeVisible()
  await act(async () => {
    if (outcome === 'abort') rejectOld(new globalThis.DOMException('Aborted body', 'AbortError'))
    else resolveOld({ models: [{ id: 'old/wrong-model', author: 'old' }], authenticated: false, stale: true })
  })
  expect(screen.getByText('Authenticated')).toBeVisible()
  expect(screen.getByText('1 repositories')).toBeVisible()
  expect(screen.queryByText('Cached snapshot')).toBeNull()
  expect(screen.queryByText('old/wrong-model')).toBeNull()
})
