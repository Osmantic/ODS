import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { TemplatePicker } from '../TemplatePicker'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

test.each([true, false])('retains the apply dialog until the request settles (success=%s)', async success => {
  let finish
  const fetchMock = vi.fn(async url => url.endsWith('/preview')
    ? { ok: true, json: async () => ({ changes: { to_enable: ['n8n'] } }) }
    : new Promise(resolve => { finish = resolve }))
  vi.stubGlobal('fetch', fetchMock)
  render(<TemplatePicker templates={[{ id: 'workflows', name: 'Workflows', services: ['n8n'] }]} />)
  fireEvent.click(screen.getByRole('button', { name: /Workflows/ }))
  fireEvent.click(await screen.findByRole('button', { name: 'Apply Template' }))
  const dialog = screen.getByRole('dialog')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  fireEvent.click(within(dialog).getAllByRole('button')[0])
  fireEvent.click(dialog.parentElement)
  expect(screen.getByRole('dialog')).toBe(dialog)
  fireEvent.click(screen.getByRole('button', { name: 'Apply Template' }))
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/apply'))).toHaveLength(1)
  await act(async () => finish({ ok: success, status: success ? 200 : 503, json: async () => ({ enabled_count: 1 }) }))
  fireEvent.click(screen.getByRole('button', { name: success ? 'Close' : 'Cancel' }))
  expect(screen.queryByRole('dialog')).toBeNull()
})

test('unlocks dismissal when the apply response body reaches its deadline', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url, { signal }) => url.endsWith('/preview')
    ? { ok: true, json: async () => ({ changes: { to_enable: ['n8n'] } }) }
    : { ok: true, json: () => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new globalThis.DOMException('Timed out', 'AbortError')))) }))
  render(<TemplatePicker templates={[{ id: 'workflows', name: 'Workflows', services: ['n8n'] }]} />)
  fireEvent.click(screen.getByRole('button', { name: /Workflows/ }))
  const apply = await screen.findByRole('button', { name: 'Apply Template' })
  vi.useFakeTimers()
  await act(async () => fireEvent.click(apply))
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
  await act(async () => vi.advanceTimersByTimeAsync(30 * 60 * 1000))
  expect(screen.getByText('Request timed out')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(screen.queryByRole('dialog')).toBeNull()
})
