import { act, screen, waitFor, within } from '@testing-library/react'
import { render } from '../../test/test-utils'
import Sidebar from '../Sidebar' // eslint-disable-line no-unused-vars
import { getSidebarExternalLinks } from '../../plugins/registry'
import { setPinned } from '../../lib/applicationPins'

/**
 * Installed extensions with a page appear under Applications like the
 * built-in apps. The API reports their health with the link, an install or
 * pin change reloads the list at once, and the owner can unpin one.
 */

const status = { services: [{ id: 'open-webui', name: 'Open WebUI', status: 'healthy' }], version: '3.0.0' }
const openWebUi = { id: 'open-webui', label: 'Open WebUI', port: 3000, ui_path: '/', public_url: '', icon: 'MessageSquare', healthNeedles: ['open-webui', 'open webui'] }
const uptimeKuma = { id: 'uptime-kuma', label: 'Uptime Kuma', port: 11027, ui_path: '/', public_url: '', icon: 'ExternalLink', healthNeedles: [], source: 'extension', status: 'healthy' }
const cyberchef = { id: 'cyberchef', label: 'CyberChef', port: 11028, ui_path: '/', public_url: '', icon: 'ExternalLink', healthNeedles: [], source: 'extension', status: 'down' }

function mockLinks(initial) {
  let links = initial
  const fetchMock = vi.fn(async url => ({
    ok: true,
    json: async () => (String(url) === '/api/external-links' ? links : {}),
  }))
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, set: next => { links = next } }
}

const linkCalls = fetchMock => fetchMock.mock.calls.filter(([url]) => url === '/api/external-links').length

beforeEach(() => { localStorage.clear() })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists installed extensions with a page under Applications, offline ones marked', async () => {
  mockLinks([openWebUi, uptimeKuma, cyberchef])
  render(<Sidebar status={status} collapsed={false} onToggle={() => {}} />)
  const applications = screen.getByLabelText('Applications').closest('details')

  const kuma = await within(applications).findByRole('link', { name: 'Uptime Kuma' })
  expect(kuma).toHaveAttribute('href', 'http://localhost:11027')
  expect(kuma).toHaveAttribute('target', '_blank')
  expect(within(applications).getByRole('link', { name: 'Open WebUI' })).toHaveAttribute('href', 'http://localhost:3000')
  // A pinned extension stays listed while it is down, without a live link.
  const chef = within(applications).getByText('CyberChef').closest('a')
  expect(chef).not.toHaveAttribute('href')
  expect(within(chef).getByText('Offline')).toBeInTheDocument()
})

test('an install or pin change reloads Applications, and unpinned extensions are hidden', async () => {
  const api = mockLinks([openWebUi])
  render(<Sidebar status={status} collapsed={false} onToggle={() => {}} />)
  const applications = screen.getByLabelText('Applications').closest('details')
  await within(applications).findByRole('link', { name: 'Open WebUI' })
  expect(within(applications).queryByText('Uptime Kuma')).toBeNull()

  api.set([openWebUi, uptimeKuma])
  const before = linkCalls(api.fetchMock)
  await act(async () => setPinned('uptime-kuma', true))
  expect(await within(applications).findByRole('link', { name: 'Uptime Kuma' })).toBeInTheDocument()
  expect(linkCalls(api.fetchMock)).toBe(before + 1)

  await act(async () => setPinned('uptime-kuma', false))
  await waitFor(() => expect(within(applications).queryByText('Uptime Kuma')).toBeNull())
  // Built-in applications are not affected by extension pins.
  expect(within(applications).getByRole('link', { name: 'Open WebUI' })).toBeInTheDocument()
})

test('extension health comes from the link, not the status poll', () => {
  const links = getSidebarExternalLinks({
    status: { services: [{ name: 'uptime kuma', status: 'healthy' }] },
    getExternalUrl: port => `http://localhost:${port}`,
    apiLinks: [{ ...uptimeKuma, status: 'down', healthNeedles: ['uptime kuma'] }, cyberchef, { ...openWebUi }],
  })
  const byKey = Object.fromEntries(links.map(link => [link.key, link]))
  expect(byKey['uptime-kuma']).toMatchObject({ healthy: false, extension: true, alwaysVisible: true })
  expect(byKey.cyberchef).toMatchObject({ healthy: false, extension: true, alwaysVisible: true })
  expect(byKey['open-webui']).toMatchObject({ healthy: false, extension: false, alwaysVisible: false })
})
