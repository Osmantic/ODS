import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

/**
 * Remove flow on the Extensions page.
 *
 * A failed install leaves an extension in the `error` state with its
 * definition still enabled. The card offers Remove there, and one confirmed
 * click must remove it: the dashboard API performs the stop itself, so the UI
 * sends a single DELETE and never chains a separate disable request.
 */

const extension = {
  id: 'swagger-ui', name: 'Swagger UI', source: 'user', installable: true,
  update_status: 'current', update_available: false, locally_modified: false,
  rollback_available: false, has_data: false,
  features: [{ category: 'tools', icon: 'Box' }],
}
const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })

function mockApi(status, remove) {
  const fetchMock = vi.fn(async (url, options) => {
    const target = String(url)
    if (target === '/api/extensions/catalog') {
      return json({ extensions: [{ ...extension, status }], agent_available: true })
    }
    if (target === '/api/templates') return json({ templates: [] })
    if (target === '/api/extensions/swagger-ui/progress') {
      return json({ status: 'error', error: 'Container did not reach running state within 15s' })
    }
    if (target.split('?')[0] === '/api/extensions/swagger-ui' && options?.method === 'DELETE') return remove()
    throw new Error(`Unexpected request: ${options?.method || 'GET'} ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const mutationCalls = fetchMock => fetchMock.mock.calls
  .filter(([, options]) => options?.method && options.method !== 'GET')
  .map(([url, options]) => `${options.method} ${url}`)

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('removes an extension in the error state with one confirmed request', async () => {
  const remove = vi.fn().mockResolvedValue(json({
    id: 'swagger-ui', action: 'uninstalled', stopped_before_removal: true, data_info: null,
    message: 'Failed extension stopped and uninstalled.',
  }))
  const fetchMock = mockApi('error', remove)
  render(<Extensions />)

  fireEvent.click(await screen.findByRole('button', { name: /Remove/ }))
  const dialog = screen.getByRole('dialog', { name: 'Confirm action' })
  expect(within(dialog).getByText(/stop anything its failed setup left running/)).toBeInTheDocument()
  expect(within(dialog).getByText(/Service data is kept/)).toBeInTheDocument()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }))

  expect(await screen.findByText('Failed extension stopped and uninstalled.')).toBeInTheDocument()
  expect(remove).toHaveBeenCalledTimes(1)
  expect(mutationCalls(fetchMock)).toEqual(['DELETE /api/extensions/swagger-ui'])
})

test('shows the API reason when removing a failed extension is refused', async () => {
  const remove = vi.fn().mockResolvedValue(json({
    detail: 'Cannot remove swagger-ui: enabled extensions depend on it (api-client). Disable them first, then remove swagger-ui.',
  }, 409))
  const fetchMock = mockApi('error', remove)
  render(<Extensions />)

  fireEvent.click(await screen.findByRole('button', { name: /Remove/ }))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove' }))

  expect(await screen.findByText(/enabled extensions depend on it \(api-client\)/)).toBeInTheDocument()
  // The page never escalates a refused removal into a disable on its own.
  expect(mutationCalls(fetchMock)).toEqual(['DELETE /api/extensions/swagger-ui'])
})

test('removes a disabled extension with the standard confirmation', async () => {
  const remove = vi.fn().mockResolvedValue(json({
    id: 'swagger-ui', action: 'uninstalled', stopped_before_removal: false, data_info: null,
    message: 'Extension uninstalled.',
  }))
  const fetchMock = mockApi('disabled', remove)
  render(<Extensions />)

  fireEvent.click(await screen.findByRole('button', { name: /Remove/ }))
  const dialog = screen.getByRole('dialog', { name: 'Confirm action' })
  expect(within(dialog).getByText('Remove Swagger UI? ODS deletes its container and installed definition. You can reinstall it from the library.')).toBeInTheDocument()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }))

  expect(await screen.findByText('Extension uninstalled.')).toBeInTheDocument()
  expect(mutationCalls(fetchMock)).toEqual(['DELETE /api/extensions/swagger-ui'])
})

/**
 * Removing Excalidraw left its stopped container behind (RTX Spark laptop,
 * 2026-10-25). Remove now deletes the container too. The dialog says that
 * the service's data is kept, and deletes it only when the owner asks.
 */
test('keeps service data on Remove and shows what was kept', async () => {
  const remove = vi.fn().mockResolvedValue(json({
    id: 'swagger-ui', action: 'uninstalled', stopped_before_removal: false,
    containers_removed: ['ods-swagger-ui'], data_deleted: false,
    data_info: { path: 'data/swagger-ui', size_gb: 0.2, preserved: true },
    message: 'Swagger UI was removed: its container and installed definition were deleted. Its data was kept (data/swagger-ui, 0.2 GB), so reinstalling it picks it up again.',
  }))
  const fetchMock = mockApi('disabled', remove)
  render(<Extensions />)

  fireEvent.click(await screen.findByRole('button', { name: /Remove/ }))
  const dialog = screen.getByRole('dialog', { name: 'Confirm action' })
  expect(within(dialog).getByText(/Service data is kept: the data\/swagger-ui folder and its Docker volumes, if it has any, stay/)).toBeVisible()
  expect(within(dialog).getByRole('checkbox', { name: "Also delete Swagger UI's data" })).not.toBeChecked()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }))

  // The API's own message names the kept data; nothing points at a Purge
  // button that a removed extension no longer has.
  expect(await screen.findByText(/Its data was kept \(data\/swagger-ui, 0.2 GB\)/)).toBeVisible()
  expect(screen.queryByText(/purge to remove/)).toBeNull()
  expect(mutationCalls(fetchMock)).toEqual(['DELETE /api/extensions/swagger-ui'])
})

test('deletes service data on Remove only when the owner ticks it', async () => {
  const remove = vi.fn().mockResolvedValue(json({
    id: 'swagger-ui', action: 'uninstalled', stopped_before_removal: false,
    containers_removed: ['ods-swagger-ui'], data_deleted: true, data_info: null,
    message: 'Swagger UI was removed with its data: its container, installed definition and data were deleted.',
  }))
  const fetchMock = mockApi('disabled', remove)
  render(<Extensions />)

  fireEvent.click(await screen.findByRole('button', { name: /Remove/ }))
  const dialog = screen.getByRole('dialog', { name: 'Confirm action' })
  fireEvent.click(within(dialog).getByRole('checkbox', { name: "Also delete Swagger UI's data" }))
  expect(within(dialog).getByText(/Its data will be deleted permanently/)).toBeVisible()
  expect(within(dialog).queryByRole('button', { name: 'Remove' })).toBeNull()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Remove and delete data' }))

  expect(await screen.findByText(/removed with its data/)).toBeVisible()
  expect(mutationCalls(fetchMock)).toEqual(['DELETE /api/extensions/swagger-ui?delete_data=true'])
})

test('a reopened Remove dialog keeps data again', async () => {
  const fetchMock = mockApi('disabled', vi.fn())
  render(<Extensions />)

  fireEvent.click(await screen.findByRole('button', { name: /Remove/ }))
  fireEvent.click(screen.getByRole('checkbox', { name: "Also delete Swagger UI's data" }))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
  fireEvent.click(screen.getByRole('button', { name: /Remove/ }))

  expect(screen.getByRole('checkbox', { name: "Also delete Swagger UI's data" })).not.toBeChecked()
  expect(mutationCalls(fetchMock)).toEqual([])
})

test('does not offer Remove for a running extension', async () => {
  const remove = vi.fn()
  const fetchMock = mockApi('enabled', remove)
  render(<Extensions />)

  expect(await screen.findByText('Disable to remove')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Remove/ })).toBeNull()
  expect(screen.getByRole('button', { name: 'Disable Swagger UI' })).toBeInTheDocument()
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/extensions/catalog', expect.anything()))
  expect(remove).not.toHaveBeenCalled()
  expect(mutationCalls(fetchMock)).toEqual([])
})
