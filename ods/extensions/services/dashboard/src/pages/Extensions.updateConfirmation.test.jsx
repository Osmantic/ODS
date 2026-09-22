import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions'

const extension = {
  id: 'gitea', name: 'Gitea', source: 'user', status: 'enabled', installable: true,
  update_status: 'available', update_available: true, locally_modified: false,
  features: [{ category: 'tools', icon: 'Box' }],
}
const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })
function mockCatalog(overrides, update) {
  const fetchMock = vi.fn(async (url, options) => {
    if (url === '/api/extensions/catalog') return json({
      extensions: [{ ...extension, ...overrides }], agent_available: true,
    })
    if (url === '/api/templates') return json({ templates: [] })
    if (url.startsWith('/api/extensions/gitea/update')) return update(url, options)
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test.each([
  ['unknown', false, /could not inspect the installed files/i],
  ['modified', true, /Local definition changes will be replaced/],
])('can review and cancel a %s library refresh without an available release', async (update_status, locally_modified, warning) => {
  const update = vi.fn().mockResolvedValue(json({ message: 'Gitea refreshed successfully.' }))
  mockCatalog({ update_status, locally_modified, update_available: false }, update)
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button', { name: 'Refresh' }))
  const dialog = screen.getByRole('dialog', { name: 'Confirm action' })
  expect(within(dialog).getByText(warning)).toBeInTheDocument()
  expect(within(dialog).getByText(/rollback backup/i)).toBeInTheDocument()
  expect(update).not.toHaveBeenCalled()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(update).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Update' }))
  expect(update).toHaveBeenCalledTimes(1)
  expect(update.mock.calls[0][0]).toBe('/api/extensions/gitea/update?force=true')
  expect(await screen.findByText('Gitea refreshed successfully.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Dismiss notification' })).toBeInTheDocument()
})

test.each([
  ['update_state_unknown', /could not inspect the installed files/i],
  ['locally_modified', /Local definition changes will be replaced/],
  ['untracked_install', /legacy Gitea install/i],
])('requires new consent for the server %s overwrite guard', async (code, warning) => {
  let finish
  const update = vi.fn().mockResolvedValueOnce(json({ detail: {
    code, force_available: true, message: 'Confirm replacement',
  } }, 409)).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  mockCatalog({}, update)
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button', { name: 'Update' }))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Update' }))

  expect(await screen.findByText(warning)).toBeInTheDocument()
  expect(update).toHaveBeenCalledTimes(1)
  expect(update.mock.calls[0][0]).toBe('/api/extensions/gitea/update')
  const confirm = within(screen.getByRole('dialog')).getByRole('button', { name: 'Update' })
  fireEvent.click(confirm)
  fireEvent.click(confirm)
  expect(update).toHaveBeenCalledTimes(2)
  expect(update.mock.calls[1][0]).toBe('/api/extensions/gitea/update?force=true')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Disable Gitea' })).toBeDisabled()
  await act(async () => finish(json({ message: 'Gitea refreshed successfully.' })))
  expect(await screen.findByText('Gitea refreshed successfully.')).toBeInTheDocument()
})

test.each([
  ['operation_in_progress', true, 409],
  ['locally_modified', false, 409],
  ['update_state_unknown', true, 403],
])('does not escalate %s without an explicit overwrite-conflict receipt', async (code, force_available, status) => {
  const update = vi.fn().mockResolvedValue(json({ detail: {
    code, force_available, message: 'Update is blocked',
  } }, status))
  mockCatalog({}, update)
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button', { name: 'Update' }))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Update' }))
  await waitFor(() => expect(screen.getByText('Update is blocked')).toBeInTheDocument())
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(update).toHaveBeenCalledTimes(1)
})
