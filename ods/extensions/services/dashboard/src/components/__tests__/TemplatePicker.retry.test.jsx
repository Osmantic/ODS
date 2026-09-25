import { act, fireEvent, screen, within } from '@testing-library/react'
import { render } from '../../test/test-utils'
import { TemplatePicker } from '../TemplatePicker'

const template = {
  id: 'developer-homelab', name: 'Developer Homelab',
  description: 'Git and local development tools', services: ['gitea', 'n8n'],
  _status: 'has_errors',
}
const response = body => ({ ok: true, status: 200, json: async () => body })
const preview = changes => response({ changes: {
  to_enable: [], already_enabled: ['n8n'], incompatible: [],
  has_errors: ['gitea'], in_progress: [], ...changes,
}, warnings: [] })

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

test.each(['cards', 'library'])('reviews failed %s templates before one explicit retry', async variant => {
  let finish
  const onApplied = vi.fn()
  const fetchMock = vi.fn().mockResolvedValueOnce(preview({}))
    .mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  vi.stubGlobal('fetch', fetchMock)
  render(<TemplatePicker templates={[template]} variant={variant} onApplied={onApplied} />)

  const card = screen.getByRole('button', { name: /Developer Homelab/i })
  expect(card).toBeEnabled()
  fireEvent.click(card)
  const retry = await screen.findByRole('button', { name: 'Retry Template' })
  expect(within(screen.getByRole('region', { name: 'Failed template services' })).getByText('gitea')).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(retry).toBeEnabled()
  fireEvent.click(retry)
  fireEvent.click(retry)
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(fetchMock.mock.calls[1][0]).toBe('/api/templates/developer-homelab/apply')
  expect(fetchMock.mock.calls[1][1].method).toBe('POST')
  expect(retry).toBeDisabled()

  await act(async () => finish(response({ enabled_count: 1, started_count: 1,
    failed_services: [], skipped_services: [], restart_required: false })))
  expect(await screen.findByText(/Template applied — check extension cards/)).toBeInTheDocument()
  expect(onApplied).toHaveBeenCalledTimes(1)
})

test('a fresh preview with an active installation blocks retry', async () => {
  let refresh
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(preview({ in_progress: ['n8n'], already_enabled: [] }))
    .mockImplementationOnce(() => new Promise(resolve => { refresh = resolve }))
  vi.stubGlobal('fetch', fetchMock)
  render(<TemplatePicker templates={[{ ...template, _status: 'available' }]} />)
  fireEvent.click(screen.getByRole('button', { name: /Developer Homelab/i }))

  const retry = await screen.findByRole('button', { name: 'Retry Template' })
  expect(within(screen.getByRole('region', { name: 'Template installations in progress' })).getByText('n8n')).toBeInTheDocument()
  expect(retry).toBeDisabled()
  fireEvent.click(retry)
  expect(fetchMock).toHaveBeenCalledTimes(1)

  fireEvent.click(screen.getByRole('button', { name: 'Refresh preview' }))
  expect(retry).toBeDisabled()
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(fetchMock.mock.calls[1][0]).toBe('/api/templates/developer-homelab/preview')
  await act(async () => refresh(preview({})))
  expect(screen.queryByRole('region', { name: 'Template installations in progress' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Retry Template' })).toBeEnabled()
  expect(fetchMock).toHaveBeenCalledTimes(2)
})
