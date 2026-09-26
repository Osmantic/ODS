import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions, { readyToast } from './Extensions' // eslint-disable-line no-unused-vars
import { APPLICATIONS_CHANGED, isPinned, setPinned } from '../lib/applicationPins'

/**
 * After an install the owner can find and use the extension: the card offers
 * Open (for a page) and How to use, a toast names the next steps, and the
 * extension is pinned to Applications in the sidebar.
 */

const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })
const base = {
  source: 'user', installable: true, update_status: 'current', update_available: false, locally_modified: false,
  rollback_available: false, has_data: false, features: [{ category: 'productivity', icon: 'Box' }],
}
const uptimeKuma = {
  ...base, id: 'uptime-kuma', name: 'Uptime Kuma', usage_kind: 'web', port: 3001, external_port: 11027,
  external_port_default: 11027, ui_path: '/', description: 'Monitor local services.',
}
const kroki = {
  ...base, id: 'kroki', name: 'Kroki', usage_kind: 'api', port: 8000, external_port: 11072,
  external_port_default: 11072, description: 'Render diagrams through a local HTTP API.',
}
const detail = ext => ({
  id: ext.id, name: ext.name, description: ext.description, status: ext.status, public_url: null, dependents: [],
  integration: null,
  guide: { schemaVersion: 1, kind: ext.usage_kind, uiPath: '/', hostPort: ext.external_port,
    internalUrl: `http://${ext.id}:${ext.port}`, docsUrl: null, settings: [] },
})

function mockCatalog(extensions, routes = {}) {
  const fetchMock = vi.fn(async (url, options = {}) => {
    const target = String(url)
    const route = routes[`${options.method || 'GET'} ${target}`]
    if (route) return route(options)
    if (target === '/api/extensions/catalog') return json({ agent_available: true, extensions: extensions() })
    if (target === '/api/templates') return json({ templates: [] })
    const found = extensions().find(ext => target === `/api/extensions/${ext.id}`)
    if (found) return json(detail(found))
    if (target.endsWith('/progress')) return json({ status: 'idle' })
    throw new Error(`Unexpected request: ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('a running extension with a page offers Open and How to use on its card', async () => {
  mockCatalog(() => [{ ...uptimeKuma, status: 'enabled' }])
  render(<Extensions compact />)
  await screen.findByText('Uptime Kuma')

  const open = screen.getByRole('link', { name: 'Open' })
  expect(open).toHaveAttribute('href', 'http://localhost:11027')
  expect(open).toHaveAttribute('target', '_blank')
  expect(open).toHaveAttribute('title', 'Open Uptime Kuma (port 11027)')

  fireEvent.click(screen.getByRole('button', { name: 'How to use Uptime Kuma' }))
  const guide = screen.getByRole('dialog', { name: 'How to use Uptime Kuma' })
  expect(await within(guide).findByText('Open Uptime Kuma in a new tab:')).toBeInTheDocument()
  fireEvent.click(within(guide).getByRole('button', { name: 'Close guide' }))
  expect(screen.queryByRole('dialog')).toBeNull()
})

test('an API extension says so instead of offering a page', async () => {
  mockCatalog(() => [{ ...kroki, status: 'enabled' }])
  render(<Extensions compact />)
  await screen.findByText('Kroki')
  expect(screen.queryByRole('link')).toBeNull()
  expect(screen.getByText('API service')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'How to use Kroki' })).toBeInTheDocument()
})

test('an extension that is not installed has no How to use yet', async () => {
  mockCatalog(() => [{ ...uptimeKuma, source: 'library', status: 'not_installed' }])
  render(<Extensions compact />)
  await screen.findByText('Uptime Kuma')
  expect(screen.getByRole('button', { name: 'Install' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'How to use Uptime Kuma' })).toBeNull()
  expect(screen.queryByRole('link')).toBeNull()
})

test('a finished install pins the extension, and the toast offers Open and How to use', async () => {
  // The owner had unpinned an earlier installation.
  setPinned('uptime-kuma', false)
  let state = 'not_installed'
  const install = vi.fn(async () => { state = 'installing'; return json({ id: 'uptime-kuma', message: 'Extension installed and starting.' }) })
  mockCatalog(() => [{ ...uptimeKuma, status: state, source: state === 'not_installed' ? 'library' : 'user' }], {
    'GET /api/extensions/uptime-kuma/install-plan': async () => json({ schemaVersion: 1, extensionId: 'uptime-kuma', steps: [
      { extensionId: 'uptime-kuma', configuration: [], missingConfiguration: [], setupHook: false }] }),
    'POST /api/extensions/uptime-kuma/install': install,
    'GET /api/extensions/uptime-kuma/progress': async () => json({ status: 'started', phase_label: 'Starting' }),
  })
  const changed = vi.fn()
  window.addEventListener(APPLICATIONS_CHANGED, changed)
  render(<Extensions compact />)

  fireEvent.click(await screen.findByRole('button', { name: 'Install' }))
  const confirm = screen.getByRole('dialog', { name: 'Confirm action' })
  await waitFor(() => expect(within(confirm).getByRole('button', { name: 'Install' })).toBeEnabled())
  vi.useFakeTimers({ shouldAdvanceTime: true })
  fireEvent.click(within(confirm).getByRole('button', { name: 'Install' }))
  await waitFor(() => expect(install).toHaveBeenCalledOnce())
  state = 'enabled'
  await act(async () => vi.advanceTimersByTimeAsync(3100))

  await waitFor(() => expect(isPinned('uptime-kuma')).toBe(true))
  expect(changed).toHaveBeenCalled()
  expect(await screen.findByText('Uptime Kuma is installed and running. Open it here or from Applications in the sidebar.')).toBeInTheDocument()
  const links = screen.getAllByRole('link', { name: /Open/ })
  expect(links.map(link => link.getAttribute('href'))).toEqual(['http://localhost:11027', 'http://localhost:11027'])

  fireEvent.click(screen.getByRole('button', { name: 'How to use' }))
  expect(screen.getByRole('dialog', { name: 'How to use Uptime Kuma' })).toBeInTheDocument()
  window.removeEventListener(APPLICATIONS_CHANGED, changed)
})

test('removing or disabling an extension refreshes Applications', async () => {
  const changed = vi.fn()
  window.addEventListener(APPLICATIONS_CHANGED, changed)
  let state = 'enabled'
  mockCatalog(() => [{ ...uptimeKuma, status: state }], {
    'POST /api/extensions/uptime-kuma/disable': async () => { state = 'disabled'; return json({ message: 'Extension disabled and stopped.' }) },
  })
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button', { name: 'Disable Uptime Kuma' }))
  fireEvent.click(within(screen.getByRole('dialog', { name: 'Confirm action' })).getByRole('button', { name: 'Disable' }))
  await waitFor(() => expect(changed).toHaveBeenCalled())
  expect(await screen.findByText('Extension disabled and stopped.')).toBeInTheDocument()
  window.removeEventListener(APPLICATIONS_CHANGED, changed)
})

test('ready toasts match what the extension offers', () => {
  expect(readyToast({ ...uptimeKuma, status: 'enabled' }).actions.map(item => item.label)).toEqual(['Open', 'How to use'])
  expect(readyToast({ ...kroki, status: 'enabled' })).toMatchObject({
    text: 'Kroki is installed and running. It has no page to open; How to use shows how other apps reach it.',
    actions: [{ label: 'How to use' }],
  })
  expect(readyToast({ id: 'aider', name: 'Aider', status: 'cli_installed', usage_kind: 'none' }).text)
    .toBe('Aider installed — run via `docker compose run --rm aider`.')
  // A page that is not published on a host port cannot be opened.
  expect(readyToast({ ...uptimeKuma, status: 'enabled', external_port: 0 }).actions.map(item => item.label)).toEqual(['How to use'])
})
