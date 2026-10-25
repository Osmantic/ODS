import { StrictMode } from 'react' // eslint-disable-line no-unused-vars
import { act, fireEvent, render, screen } from '@testing-library/react'
import { TemplatePreview } from '../TemplatePicker' // eslint-disable-line no-unused-vars

const template = { id: 'developer-homelab', name: 'Developer Homelab', services: ['gitea'] }
const payload = (changes = {}) => ({ changes: { to_enable: ['gitea'], in_progress: [], ...changes }, warnings: [] })
const response = data => ({ ok: true, status: 200, json: async () => data })
const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

test('StrictMode discards abandoned previews and close aborts the live request', async () => {
  const requests = []
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const body = deferred()
    requests.push({ url, options, body })
    return Promise.resolve({ ok: true, json: () => body.promise })
  }))
  const view = render(<StrictMode><TemplatePreview template={template} onClose={vi.fn()} /></StrictMode>)
  expect(requests.length).toBeGreaterThan(0)
  expect(requests.slice(0, -1).every(request => request.options.signal?.aborted)).toBe(true)
  const live = requests.at(-1)
  expect(live.options.signal.aborted).toBe(false)
  await act(async () => {
    for (const request of requests.slice(0, -1)) request.body.resolve(payload({ to_enable: ['abandoned-service'] }))
  })
  expect(screen.queryByText('abandoned-service')).not.toBeInTheDocument()
  view.unmount()
  expect(live.options.signal.aborted).toBe(true)
  await act(async () => live.body.resolve(payload()))
})

test('a failed refresh invalidates the previous plan and offers a preview retry before apply', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(response(payload({ in_progress: ['n8n'] })))
    .mockRejectedValueOnce(new TypeError('network unavailable'))
    .mockResolvedValueOnce(response(payload({ to_enable: ['miniflux'] }))))
  render(<TemplatePreview template={template} onClose={vi.fn()} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Refresh preview' }))
  expect(await screen.findByText('Failed to load preview')).toBeInTheDocument()
  expect(screen.queryByText('gitea')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Apply Template' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Retry preview' }))
  expect(await screen.findByText('miniflux')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Apply Template' })).toBeEnabled()
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(Array(3).fill('/api/templates/developer-homelab/preview'))
})
