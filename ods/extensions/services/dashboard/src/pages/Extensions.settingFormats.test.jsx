import { readFileSync } from 'node:fs'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars
import {
  generateSettingValue, installPlanSettings, settingProblem,
} from '../components/ExtensionInstallSettings'

/**
 * Declared setting formats in the Extensions page install dialog.
 *
 * Shlink on tower1 (2026-09-25): values that were not 64 hexadecimal
 * characters were accepted here, Shlink's start script rejected them, and the
 * install ended after 90 seconds with only "state=restarting". The dialog now
 * shows the expected format, keeps Save disabled until every value matches,
 * and can fill crypto-random values in that format.
 */

const SECRET = 'correct horse battery staple'
const HEX64 = /^[0-9a-f]{64}$/
const extension = {
  id: 'shlink', name: 'Shlink', installable: true, update_status: 'current', update_available: false,
  locally_modified: false, rollback_available: false, has_data: false,
  features: [{ category: 'productivity', icon: 'Box' }],
}
const hex64 = (distinctFrom = []) => ({
  name: 'hex64', patterns: ['^[0-9a-fA-F]{64}$'], minLength: null, maxLength: null, generate: 'hex64', distinctFrom,
  hint: '64 hexadecimal characters (0-9, a-f)',
})
const email = {
  name: 'email', patterns: ['^[^@]+@[^@]+$'], minLength: null, maxLength: null, generate: null, distinctFrom: [],
  hint: 'a plain email address such as name@example.com',
}
const setting = (key, format, secret = true) => ({
  key, required: true, secret, configured: false, description: `${key} description`, format,
})
const DB = setting('SHLINK_DB_PASSWORD', hex64())
const API = setting('SHLINK_API_KEY', hex64(['SHLINK_DB_PASSWORD']))
const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })

function mockApi(configuration, routes = {}) {
  const fetchMock = vi.fn(async (url, options = {}) => {
    const target = String(url)
    const method = options.method || 'GET'
    const route = routes[`${method} ${target}`]
    if (route) return route(options)
    if (target === '/api/extensions/catalog') {
      return json({ extensions: [{ ...extension, status: 'not_installed', source: 'library' }], agent_available: true })
    }
    if (target === '/api/templates') return json({ templates: [] })
    if (target === '/api/extensions/shlink/progress') return json({ status: 'idle' })
    if (target === '/api/extensions/shlink/install-plan') {
      return json({
        schemaVersion: 1, extensionId: 'shlink', requiresConfiguration: true, blocked: false, pending: false,
        executionStarted: false,
        steps: [{ extensionId: 'shlink', status: 'not_installed', action: 'install', dependsOn: [], reason: null,
          configuration, missingConfiguration: configuration.map(item => item.key), setupHook: false }],
      })
    }
    throw new Error(`Unexpected request: ${method} ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const mutationCalls = fetchMock => fetchMock.mock.calls
  .filter(([, options]) => options?.method && options.method !== 'GET')
  .map(([url, options]) => `${options.method} ${url}`)

async function openDialog() {
  render(<Extensions />)
  fireEvent.click(await screen.findByRole('button', { name: 'Install' }))
  const dialog = screen.getByRole('dialog', { name: 'Confirm action' })
  await within(dialog).findByLabelText(/SHLINK_DB_PASSWORD/, { selector: 'input' })
  return dialog
}

const input = (dialog, key) => within(dialog).getByLabelText(new RegExp(key), { selector: 'input' })

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('shows the expected format and keeps Save disabled until the value matches it', async () => {
  const fetchMock = mockApi([DB])
  const dialog = await openDialog()
  const save = within(dialog).getByRole('button', { name: 'Save and install' })

  expect(within(dialog).getByText('Format: 64 hexadecimal characters (0-9, a-f)')).toBeInTheDocument()
  expect(save).toBeDisabled()

  fireEvent.change(input(dialog, 'SHLINK_DB_PASSWORD'), { target: { value: 'not-hex-at-all' } })
  expect(within(dialog).getByText('Expected 64 hexadecimal characters (0-9, a-f).')).toBeInTheDocument()
  expect(input(dialog, 'SHLINK_DB_PASSWORD')).toHaveAttribute('aria-invalid', 'true')
  expect(save).toBeDisabled()

  fireEvent.change(input(dialog, 'SHLINK_DB_PASSWORD'), { target: { value: 'a'.repeat(63) } })
  expect(save).toBeDisabled()

  // Shlink's own check accepts either case, and so does the declared format.
  fireEvent.change(input(dialog, 'SHLINK_DB_PASSWORD'), { target: { value: 'A'.repeat(64) } })
  expect(save).toBeEnabled()

  fireEvent.change(input(dialog, 'SHLINK_DB_PASSWORD'), { target: { value: 'a'.repeat(64) } })
  expect(within(dialog).queryByText(/^Expected /)).toBeNull()
  expect(input(dialog, 'SHLINK_DB_PASSWORD')).not.toHaveAttribute('aria-invalid')
  expect(save).toBeEnabled()
  expect(mutationCalls(fetchMock)).toEqual([])
})

test('Generate fills a crypto-random value in the declared format; Show reveals it to record', async () => {
  const getRandomValues = vi.spyOn(globalThis.crypto, 'getRandomValues')
  const configure = vi.fn(async () => json({ service_id: 'shlink', status: 'saved', saved_keys: ['SHLINK_DB_PASSWORD'] }))
  const install = vi.fn(async () => json({ id: 'shlink', action: 'installed', message: 'Extension installed and starting.' }))
  mockApi([DB], {
    'POST /api/extensions/shlink/configure': configure,
    'POST /api/extensions/shlink/install': install,
  })
  const dialog = await openDialog()
  const field = input(dialog, 'SHLINK_DB_PASSWORD')

  expect(within(dialog).getByText(/Generate fills a random value in the required format/)).toBeInTheDocument()
  expect(within(dialog).getByText(/Secret values are not shown again; record any\s+password/)).toBeInTheDocument()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Generate SHLINK_DB_PASSWORD' }))
  expect(getRandomValues).toHaveBeenCalled()
  const generated = field.value
  expect(generated).toMatch(HEX64)
  expect(field).toHaveAttribute('type', 'password')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Show SHLINK_DB_PASSWORD' }))
  expect(field).toHaveAttribute('type', 'text')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Hide SHLINK_DB_PASSWORD' }))
  expect(field).toHaveAttribute('type', 'password')

  fireEvent.click(within(dialog).getByRole('button', { name: 'Save and install' }))
  await waitFor(() => expect(install).toHaveBeenCalledOnce())
  expect(JSON.parse(configure.mock.calls[0][0].body)).toEqual({ values: { SHLINK_DB_PASSWORD: generated } })
  expect(screen.queryByDisplayValue(generated)).toBeNull()
  expect(document.body.textContent).not.toContain(generated)
})

test('Generate all fills every setting when all of them can be generated', async () => {
  const configure = vi.fn(async options => json({
    service_id: 'shlink', status: 'saved', saved_keys: Object.keys(JSON.parse(options.body).values) }))
  const install = vi.fn(async () => json({ id: 'shlink', action: 'installed', message: 'Extension installed and starting.' }))
  mockApi([DB, API], {
    'POST /api/extensions/shlink/configure': configure,
    'POST /api/extensions/shlink/install': install,
  })
  const dialog = await openDialog()

  fireEvent.click(within(dialog).getByRole('button', { name: 'Generate all' }))
  const db = input(dialog, 'SHLINK_DB_PASSWORD').value
  const api = input(dialog, 'SHLINK_API_KEY').value
  expect(db).toMatch(HEX64)
  expect(api).toMatch(HEX64)
  expect(api).not.toBe(db)

  // One more click installs.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save and install' }))
  await waitFor(() => expect(install).toHaveBeenCalledOnce())
  expect(JSON.parse(configure.mock.calls[0][0].body)).toEqual({ values: { SHLINK_DB_PASSWORD: db, SHLINK_API_KEY: api } })
})

test('Generate all is not offered when a setting has no generator', async () => {
  mockApi([DB, setting('SHLINK_ADMIN_EMAIL', email, false)])
  const dialog = await openDialog()

  expect(within(dialog).queryByRole('button', { name: 'Generate all' })).toBeNull()
  expect(within(dialog).getByRole('button', { name: 'Generate SHLINK_DB_PASSWORD' })).toBeInTheDocument()
  expect(within(dialog).queryByRole('button', { name: 'Generate SHLINK_ADMIN_EMAIL' })).toBeNull()
  expect(within(dialog).queryByRole('button', { name: 'Show SHLINK_ADMIN_EMAIL' })).toBeNull()
  expect(within(dialog).getByText('Format: a plain email address such as name@example.com')).toBeInTheDocument()
})

test('settings that must differ cannot be saved with the same value', async () => {
  mockApi([DB, API])
  const dialog = await openDialog()
  const same = 'b'.repeat(64)

  fireEvent.change(input(dialog, 'SHLINK_DB_PASSWORD'), { target: { value: same } })
  fireEvent.change(input(dialog, 'SHLINK_API_KEY'), { target: { value: same } })
  expect(within(dialog).getByText('Must differ from SHLINK_DB_PASSWORD.')).toBeInTheDocument()
  expect(within(dialog).getByRole('button', { name: 'Save and install' })).toBeDisabled()

  fireEvent.change(input(dialog, 'SHLINK_API_KEY'), { target: { value: 'c'.repeat(64) } })
  expect(within(dialog).getByRole('button', { name: 'Save and install' })).toBeEnabled()
})

test('a saved value outside its format is a warning, never a block, and names no value', async () => {
  // Reinstall after uninstall: .env still holds the old values, and the
  // environment editor cannot clear a library secret.
  const install = vi.fn(async () => json({ id: 'shlink', action: 'installed', message: 'Extension installed and starting.' }))
  mockApi([], {
    'GET /api/extensions/shlink/install-plan': async () => json({
      schemaVersion: 1, extensionId: 'shlink', requiresConfiguration: false, blocked: false, pending: false,
      executionStarted: false,
      steps: [{ extensionId: 'shlink', status: 'not_installed', action: 'install', dependsOn: [], reason: null,
        configuration: [{ ...DB, configured: true }, { ...API, configured: true }], missingConfiguration: [],
        setupHook: false, savedConfigurationWarnings: ['SHLINK_DB_PASSWORD', 'SHLINK_API_KEY', 'not a key'] }],
    }),
    'POST /api/extensions/shlink/install': install,
  })
  render(<Extensions />)
  fireEvent.click(await screen.findByRole('button', { name: 'Install' }))
  const dialog = screen.getByRole('dialog', { name: 'Confirm action' })

  const note = await within(dialog).findByRole('note')
  expect(note).toHaveTextContent(
    'The saved settings SHLINK_DB_PASSWORD, SHLINK_API_KEY do not have the format Shlink requires.')
  expect(note).toHaveTextContent(/data volumes are kept too/)
  expect(note).not.toHaveTextContent(/remove|not a key/i)
  const confirm = within(dialog).getByRole('button', { name: 'Install' })
  expect(confirm).toBeEnabled()
  fireEvent.click(confirm)
  await waitFor(() => expect(install).toHaveBeenCalledOnce())
})

test('an API format refusal is shown without the value, and nothing is installed', async () => {
  const message = 'SHLINK_DB_PASSWORD must be 64 hexadecimal characters (0-9, a-f). Nothing was saved.'
  const fetchMock = mockApi([setting('SHLINK_DB_PASSWORD', null)], {
    'POST /api/extensions/shlink/configure': async () => json({ detail: {
      code: 'invalid_configuration', service_id: 'shlink', message,
      invalid_configuration: [{ key: 'SHLINK_DB_PASSWORD', expected: '64 hexadecimal characters (0-9, a-f)' }],
    } }, 422),
  })
  const dialog = await openDialog()

  // Without a format in the plan the dialog cannot check; the API still does.
  fireEvent.change(input(dialog, 'SHLINK_DB_PASSWORD'), { target: { value: SECRET } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save and install' }))

  expect(await within(dialog).findByRole('alert')).toHaveTextContent(`${message} Nothing was installed or started.`)
  expect(mutationCalls(fetchMock)).toEqual(['POST /api/extensions/shlink/configure'])
  expect(document.body.textContent).not.toContain(SECRET)
})

test('unusable formats are ignored and generation redraws until the value conforms', () => {
  const plan = format => ({ schemaVersion: 1, extensionId: 'shlink', steps: [{ extensionId: 'shlink',
    configuration: [setting('SHLINK_DB_PASSWORD', format)], missingConfiguration: ['SHLINK_DB_PASSWORD'] }] })
  const parsed = format => installPlanSettings(plan(format), 'shlink')[0]
  expect(parsed(hex64()).format.generate).toBe('hex64')
  expect(parsed({ ...hex64(), patterns: ['[0-9a-f]+'] }).format).toBeNull()
  expect(parsed({ ...hex64(), patterns: ['^[$'] }).format).toBeNull()
  expect(parsed({ ...hex64(), minLength: 0 }).format).toBeNull()
  expect(parsed({ ...hex64(), generate: 'uuid' }).format.generate).toBeNull()

  // Kestra-style "an uppercase letter and a number": candidates without one
  // are redrawn rather than offered.
  const field = parsed({ name: null, patterns: ['^(?=.*[A-Z])(?=.*[0-9]).*$'], minLength: 8, maxLength: 72,
    generate: 'password', distinctFrom: [], hint: 'an uppercase letter and a number, at least 8 characters' })
  for (let i = 0; i < 50; i += 1) {
    const value = generateSettingValue(field)
    expect(value).toMatch(/^[A-Za-z0-9]{24}$/)
    expect(settingProblem(field, value)).toBe('')
  }
  expect(settingProblem(field, 'lowercase1')).toBe('Expected an uppercase letter and a number, at least 8 characters.')
  // 72 bytes is the limit, whatever the character count.
  expect(settingProblem(field, `A1${'é'.repeat(36)}`)).toMatch(/^Expected /)
  expect(settingProblem(field, '')).toBe('Enter a value.')
  expect(settingProblem(field, 'Abcdefg1\n')).toBe('Use a single line without control characters.')
  expect(generateSettingValue({ ...field, format: { ...field.format, generate: null } })).toBeNull()
})

test('every pattern the extension library declares runs as a JavaScript RegExp', () => {
  // The API checks the same patterns with Python's re.fullmatch; manifests
  // may only use constructs both engines treat alike (see the schema).
  const catalog = JSON.parse(readFileSync('../../../config/extensions-catalog.json', 'utf8'))
  const declared = catalog.extensions.flatMap(entry => entry.env_vars || []).filter(item => item.pattern)
  expect(declared.length).toBeGreaterThan(20)
  for (const item of declared) {
    expect(() => new RegExp(item.pattern), item.key).not.toThrow()
    expect(item.pattern.startsWith('^') && item.pattern.endsWith('$')).toBe(true)
  }
})
