import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars
import { installPlanSettings, missingSettingsRefusal } from '../components/ExtensionInstallSettings'

/**
 * Required settings in the Extensions page install and retry dialogs.
 *
 * About half of the library Compose files use `${NAME:?}`. Before this
 * dialog asked for them, Install was acknowledged and then failed on the
 * host with an opaque error. Settings are saved through the same
 * /configure endpoint as the Portal's extension setup, then the original
 * request is sent.
 */

const SECRET = 'correct horse battery staple'
const extension = {
  id: 'gotify', name: 'Gotify', installable: true, update_status: 'current', update_available: false,
  locally_modified: false, rollback_available: false, has_data: false,
  features: [{ category: 'productivity', icon: 'Box' }],
}
const field = {
  key: 'GOTIFY_ADMIN_PASSWORD', required: true, secret: true, configured: false,
  description: 'Initial administrator password; existing accounts are not reset by changing this value.',
}
const plan = (step = {}) => ({
  schemaVersion: 1, extensionId: 'gotify', requiresConfiguration: true, blocked: false, pending: false,
  executionStarted: false,
  steps: [{ extensionId: 'gotify', status: 'not_installed', action: 'install', dependsOn: [], reason: null,
    configuration: [field], missingConfiguration: ['GOTIFY_ADMIN_PASSWORD'], setupHook: false, ...step }],
})
const refusal = {
  code: 'missing_configuration', service_id: 'gotify',
  message: 'Gotify needs required settings before it can be started: GOTIFY_ADMIN_PASSWORD. Nothing was changed.',
  missing_configuration: ['GOTIFY_ADMIN_PASSWORD'],
  configuration: [{ key: 'GOTIFY_ADMIN_PASSWORD', secret: true, description: field.description }],
}
const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })

function mockApi({ status = 'not_installed', source = 'library', routes = {} }) {
  const fetchMock = vi.fn(async (url, options = {}) => {
    const target = String(url)
    const method = options.method || 'GET'
    const route = routes[`${method} ${target}`]
    if (route) return route(options)
    if (target === '/api/extensions/catalog') {
      return json({ extensions: [{ ...extension, status, source }], agent_available: true })
    }
    if (target === '/api/templates') return json({ templates: [] })
    if (target === '/api/extensions/gotify/progress') return json({ status: 'idle' })
    throw new Error(`Unexpected request: ${method} ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const mutationCalls = fetchMock => fetchMock.mock.calls
  .filter(([, options]) => options?.method && options.method !== 'GET')
  .map(([url, options]) => `${options.method} ${url}`)

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('collects a required secret in the Install dialog, saves it, then installs', async () => {
  const configure = vi.fn(async () => json({ service_id: 'gotify', status: 'saved', saved_keys: ['GOTIFY_ADMIN_PASSWORD'] }))
  const install = vi.fn(async () => json({ id: 'gotify', action: 'installed', message: 'Extension installed and starting.' }))
  const fetchMock = mockApi({ routes: {
    'GET /api/extensions/gotify/install-plan': async () => json(plan()),
    'POST /api/extensions/gotify/configure': configure,
    'POST /api/extensions/gotify/install': install,
  } })
  render(<Extensions />)

  fireEvent.click(await screen.findByRole('button', { name: 'Install' }))
  const dialog = screen.getByRole('dialog', { name: 'Confirm action' })
  const input = await within(dialog).findByLabelText(/GOTIFY_ADMIN_PASSWORD/, { selector: 'input' })
  expect(input).toHaveAttribute('type', 'password')
  expect(within(dialog).getByText(field.description)).toBeInTheDocument()
  expect(within(dialog).getByText(/Details → Configured Credentials/)).toBeInTheDocument()

  // An empty required setting is never sent.
  expect(within(dialog).getByRole('button', { name: 'Save and install' })).toBeDisabled()
  expect(mutationCalls(fetchMock)).toEqual([])

  fireEvent.change(input, { target: { value: SECRET } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save and install' }))

  await waitFor(() => expect(install).toHaveBeenCalledOnce())
  expect(mutationCalls(fetchMock)).toEqual([
    'POST /api/extensions/gotify/configure', 'POST /api/extensions/gotify/install'])
  expect(JSON.parse(configure.mock.calls[0][0].body)).toEqual({ values: { GOTIFY_ADMIN_PASSWORD: SECRET } })
  // The secret is not echoed anywhere once submitted.
  expect(screen.queryByDisplayValue(SECRET)).toBeNull()
  expect(document.body.textContent).not.toContain(SECRET)
})

test('installs directly when nothing is missing or a setup hook provides the settings', async () => {
  const install = vi.fn(async () => json({ id: 'gotify', action: 'installed', message: 'Extension installed and starting.' }))
  const fetchMock = mockApi({ routes: {
    'GET /api/extensions/gotify/install-plan': async () => json(plan({ setupHook: true })),
    'POST /api/extensions/gotify/install': install,
  } })
  render(<Extensions />)

  fireEvent.click(await screen.findByRole('button', { name: 'Install' }))
  const dialog = screen.getByRole('dialog', { name: 'Confirm action' })
  const confirm = within(dialog).getByRole('button', { name: 'Install' })
  await waitFor(() => expect(confirm).toBeEnabled())
  expect(within(dialog).queryByLabelText(/GOTIFY_ADMIN_PASSWORD/, { selector: 'input' })).toBeNull()
  fireEvent.click(confirm)

  await waitFor(() => expect(install).toHaveBeenCalledOnce())
  expect(mutationCalls(fetchMock)).toEqual(['POST /api/extensions/gotify/install'])
})

test('answers an install refusal for missing settings in the same dialog', async () => {
  const install = vi.fn()
    .mockResolvedValueOnce(json({ detail: { ...refusal, message: refusal.message.replace('started', 'installed') } }, 400))
    .mockResolvedValueOnce(json({ id: 'gotify', action: 'installed', message: 'Extension installed and starting.' }))
  const fetchMock = mockApi({ routes: {
    // The plan is unavailable; the API itself still refuses up front.
    'GET /api/extensions/gotify/install-plan': async () => json({ detail: 'unavailable' }, 503),
    'POST /api/extensions/gotify/configure': async () => json({ service_id: 'gotify', status: 'saved', saved_keys: ['GOTIFY_ADMIN_PASSWORD'] }),
    'POST /api/extensions/gotify/install': install,
  } })
  render(<Extensions />)

  fireEvent.click(await screen.findByRole('button', { name: 'Install' }))
  let dialog = screen.getByRole('dialog', { name: 'Confirm action' })
  const confirm = within(dialog).getByRole('button', { name: 'Install' })
  await waitFor(() => expect(confirm).toBeEnabled())
  fireEvent.click(confirm)

  dialog = await screen.findByRole('dialog', { name: 'Confirm action' })
  expect(await within(dialog).findByText(/needs required settings before it can be installed/)).toBeInTheDocument()
  fireEvent.change(within(dialog).getByLabelText(/GOTIFY_ADMIN_PASSWORD/, { selector: 'input' }), { target: { value: SECRET } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save and install' }))

  await waitFor(() => expect(install).toHaveBeenCalledTimes(2))
  expect(mutationCalls(fetchMock)).toEqual([
    'POST /api/extensions/gotify/install', 'POST /api/extensions/gotify/configure', 'POST /api/extensions/gotify/install'])
})

test('Retry on a failed extension asks for the missing settings the API reports, then retries', async () => {
  const enable = vi.fn()
    .mockResolvedValueOnce(json({ detail: refusal }, 400))
    .mockResolvedValueOnce(json({ id: 'gotify', action: 'enabled', message: 'Extension enabled and started.' }))
  const fetchMock = mockApi({ status: 'error', source: 'user', routes: {
    'POST /api/extensions/gotify/configure': async () => json({ service_id: 'gotify', status: 'saved', saved_keys: ['GOTIFY_ADMIN_PASSWORD'] }),
    'POST /api/extensions/gotify/enable': enable,
    'GET /api/extensions/gotify/progress': async () => json({
      status: 'error', error: 'Could not resolve installation Compose configuration; containers were not started. Missing required setting: GOTIFY_ADMIN_PASSWORD.' }),
  } })
  render(<Extensions />)

  fireEvent.click(await screen.findByRole('button', { name: /Retry/ }))
  fireEvent.click(within(screen.getByRole('dialog', { name: 'Confirm action' })).getByRole('button', { name: 'Enable' }))

  const dialog = await screen.findByRole('dialog', { name: 'Confirm action' })
  expect(await within(dialog).findByText(refusal.message)).toBeInTheDocument()
  fireEvent.change(within(dialog).getByLabelText(/GOTIFY_ADMIN_PASSWORD/, { selector: 'input' }), { target: { value: SECRET } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save and enable' }))

  await waitFor(() => expect(enable).toHaveBeenCalledTimes(2))
  expect(mutationCalls(fetchMock)).toEqual([
    'POST /api/extensions/gotify/enable', 'POST /api/extensions/gotify/configure', 'POST /api/extensions/gotify/enable'])
})

test('a failed save stays in the dialog and never installs', async () => {
  const fetchMock = mockApi({ routes: {
    'GET /api/extensions/gotify/install-plan': async () => json(plan()),
    'POST /api/extensions/gotify/configure': async () => json({ detail: 'Configuration save could not be confirmed' }, 503),
  } })
  render(<Extensions />)

  fireEvent.click(await screen.findByRole('button', { name: 'Install' }))
  const dialog = screen.getByRole('dialog', { name: 'Confirm action' })
  fireEvent.change(await within(dialog).findByLabelText(/GOTIFY_ADMIN_PASSWORD/, { selector: 'input' }), { target: { value: SECRET } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save and install' }))

  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    'Configuration save could not be confirmed. Nothing was installed or started.')
  expect(mutationCalls(fetchMock)).toEqual(['POST /api/extensions/gotify/configure'])
  expect(within(dialog).getByLabelText(/GOTIFY_ADMIN_PASSWORD/, { selector: 'input' })).toHaveValue('')
})

test('cancelling discards typed settings without sending anything', async () => {
  const fetchMock = mockApi({ routes: {
    'GET /api/extensions/gotify/install-plan': async () => json(plan()),
  } })
  render(<Extensions />)

  fireEvent.click(await screen.findByRole('button', { name: 'Install' }))
  let dialog = screen.getByRole('dialog', { name: 'Confirm action' })
  fireEvent.change(await within(dialog).findByLabelText(/GOTIFY_ADMIN_PASSWORD/, { selector: 'input' }), { target: { value: SECRET } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  expect(screen.queryByRole('dialog', { name: 'Confirm action' })).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Install' }))
  dialog = screen.getByRole('dialog', { name: 'Confirm action' })
  expect(await within(dialog).findByLabelText(/GOTIFY_ADMIN_PASSWORD/, { selector: 'input' })).toHaveValue('')
  expect(mutationCalls(fetchMock)).toEqual([])
})

test('settings parsers reject anything but declared keys with typed flags', () => {
  expect(installPlanSettings(plan(), 'gotify')).toEqual([
    { key: 'GOTIFY_ADMIN_PASSWORD', secret: true, description: field.description, format: null }])
  expect(installPlanSettings(plan({ setupHook: true }), 'gotify')).toEqual([])
  expect(installPlanSettings(plan(), 'other')).toBeNull()
  expect(installPlanSettings(plan({ missingConfiguration: ['NOT_DECLARED'] }), 'gotify')).toBeNull()
  expect(installPlanSettings(plan({ configuration: [{ ...field, configured: true }] }), 'gotify')).toBeNull()
  expect(missingSettingsRefusal(refusal)).toEqual({
    serviceId: 'gotify', message: refusal.message,
    fields: refusal.configuration.map(item => ({ ...item, format: null })) })
  expect(missingSettingsRefusal({ ...refusal, code: 'other' })).toBeNull()
  expect(missingSettingsRefusal({ ...refusal, service_id: '../x' })).toBeNull()
  expect(missingSettingsRefusal({ ...refusal, configuration: [{ key: 'lower', secret: true }] })).toBeNull()
  expect(missingSettingsRefusal({ ...refusal, configuration: [{ key: 'A_KEY', secret: 'yes' }] })).toBeNull()
})
