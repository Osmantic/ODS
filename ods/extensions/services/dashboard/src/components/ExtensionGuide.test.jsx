import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import ExtensionGuide, { credentialsCommand, extensionKind } from './ExtensionGuide' // eslint-disable-line no-unused-vars
import { APPLICATIONS_CHANGED, isPinned } from '../lib/applicationPins'

/**
 * "How to use" for an installed extension. Everything shown comes from the
 * extension's own definition through GET /api/extensions/{id}; settings are
 * reported by presence only and their values are read on the host.
 */

const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })

const uptimeKuma = {
  id: 'uptime-kuma', name: 'Uptime Kuma', status: 'enabled', source: 'user', usage_kind: 'web',
  port: 3001, external_port: 11027, external_port_default: 11027, ui_path: '/',
  description: 'Monitor local services and websites.',
}
const uptimeDetail = {
  id: 'uptime-kuma', name: 'Uptime Kuma', description: 'Monitor local services and websites.',
  status: 'enabled', public_url: null, dependents: [],
  integration: {
    documentation: [
      '# Uptime Kuma for ODS',
      '',
      'Create the first administrator account when the page first opens.',
      '',
      'See the [upstream wiki](https://github.com/louislam/uptime-kuma/wiki) or `http://localhost:11027`.',
      '',
      '![diagram](https://tracker.example.test/pixel.png)',
    ].join('\n'),
    documentationTruncated: false,
  },
  guide: {
    schemaVersion: 1, kind: 'web', uiPath: '/', hostPort: 11027, internalUrl: 'http://uptime-kuma:3001',
    docsUrl: 'https://github.com/louislam/uptime-kuma', settings: [],
  },
}
const gotify = {
  id: 'gotify', name: 'Gotify', status: 'enabled', source: 'user', usage_kind: 'web',
  port: 80, external_port: 11040, ui_path: '/',
}
const gotifyDetail = {
  ...uptimeDetail, id: 'gotify', name: 'Gotify', description: 'Push notifications.', integration: null,
  guide: {
    schemaVersion: 1, kind: 'web', uiPath: '/', hostPort: 11040, internalUrl: 'http://gotify:80', docsUrl: null,
    settings: [
      { key: 'GOTIFY_DB_PASSWORD', description: 'Database password.', secret: true, required: true, configured: true, role: 'internal' },
      { key: 'GOTIFY_ADMIN_PASSWORD', description: 'Initial administrator password.', secret: true, required: true, configured: true, role: 'sign_in' },
      { key: 'GOTIFY_TIMEZONE', description: 'Time zone.', secret: false, required: false, configured: false, role: 'setting' },
    ],
  },
}
const kroki = {
  id: 'kroki', name: 'Kroki', status: 'enabled', source: 'user', usage_kind: 'api',
  port: 8000, external_port: 11072, external_port_default: 11072,
}
const krokiDetail = {
  ...uptimeDetail, id: 'kroki', name: 'Kroki', description: 'Render diagrams through a local HTTP API.',
  dependents: ['docs-site'], integration: null,
  guide: {
    schemaVersion: 1, kind: 'api', uiPath: '/', hostPort: 11072, internalUrl: 'http://kroki:8000',
    docsUrl: 'https://github.com/yuzutech/kroki', settings: [],
  },
}

function mockDetail(detail, status = 200) {
  const fetchMock = vi.fn(async url => {
    if (String(url) === `/api/extensions/${detail.id}`) return json(detail, status)
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('explains how to open a web extension, where its documentation is and that ODS made no login', async () => {
  mockDetail(uptimeDetail)
  render(<ExtensionGuide ext={uptimeKuma} onClose={() => {}} />)
  const dialog = screen.getByRole('dialog', { name: 'How to use Uptime Kuma' })

  expect(within(dialog).getByText('Monitor local services and websites.')).toBeInTheDocument()
  expect(within(dialog).getByRole('link', { name: 'Open Uptime Kuma' })).toHaveAttribute('href', 'http://localhost:11027')
  expect(await within(dialog).findByText('Open Uptime Kuma in a new tab:')).toBeInTheDocument()
  expect(within(dialog).getByRole('link', { name: 'Documentation' })).toHaveAttribute('href', 'https://github.com/louislam/uptime-kuma')
  expect(within(dialog).getByText('It is also listed under Applications in the sidebar.')).toBeInTheDocument()
  expect(within(dialog).getByText(/ODS did not create a login for Uptime Kuma/)).toBeInTheDocument()

  // The recipe guide renders; only https links leave the dashboard and a
  // README image never loads remote content.
  expect(within(dialog).getByRole('heading', { name: 'Uptime Kuma for ODS' })).toBeInTheDocument()
  expect(within(dialog).getByRole('link', { name: 'upstream wiki' })).toHaveAttribute('href', 'https://github.com/louislam/uptime-kuma/wiki')
  expect(within(dialog).getByRole('link', { name: 'upstream wiki' })).toHaveAttribute('rel', 'noopener noreferrer')
  expect(dialog.querySelector('img')).toBeNull()
  expect(within(dialog).getByText('[Image: diagram]')).toBeInTheDocument()
})

test('lists sign-in settings by name and presence and shows how to read them on the host', async () => {
  const fetchMock = mockDetail(gotifyDetail)
  render(<ExtensionGuide ext={gotify} onClose={() => {}} />)
  const dialog = screen.getByRole('dialog', { name: 'How to use Gotify' })

  const items = await within(dialog).findAllByRole('listitem')
  // Sign-in first; a non-secret ordinary setting is not a credential.
  expect(items.map(item => item.querySelector('code').textContent)).toEqual(['GOTIFY_ADMIN_PASSWORD', 'GOTIFY_DB_PASSWORD'])
  expect(within(items[0]).getByText('Sign-in')).toBeInTheDocument()
  expect(within(items[0]).getByText('Saved')).toBeInTheDocument()
  expect(within(items[0]).getByText('Initial administrator password.')).toBeInTheDocument()
  expect(within(items[1]).getByText('Used by the service')).toBeInTheDocument()
  expect(within(dialog).getByText(/are not shown here/)).toBeInTheDocument()
  expect(within(dialog).getByText(credentialsCommand(['GOTIFY_ADMIN_PASSWORD', 'GOTIFY_DB_PASSWORD']))).toBeInTheDocument()
  // Only the presence-only detail endpoint is read.
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/api/extensions/gotify'])
  expect(dialog.querySelector('input')).toBeNull()
})

test('says an API extension has no page and shows how other apps reach it', async () => {
  mockDetail(krokiDetail)
  render(<ExtensionGuide ext={kroki} onClose={() => {}} />)
  const dialog = screen.getByRole('dialog', { name: 'How to use Kroki' })

  expect(await within(dialog).findByText('Kroki has no page to open. It is a service that other apps call.')).toBeInTheDocument()
  expect(within(dialog).getByText('http://kroki:8000')).toBeInTheDocument()
  expect(within(dialog).getByText('http://localhost:11072')).toBeInTheDocument()
  expect(within(dialog).getByText('/extensions @kroki')).toBeInTheDocument()
  expect(within(dialog).getByText('docs-site')).toBeInTheDocument()
  expect(within(dialog).queryByRole('link', { name: /Open/ })).toBeNull()
  expect(within(dialog).queryByRole('button', { name: /Applications/ })).toBeNull()
  expect(within(dialog).getByText('Credentials')).toBeInTheDocument()
})

test('asks to start a stopped web extension before it can be opened', async () => {
  mockDetail(uptimeDetail)
  render(<ExtensionGuide ext={{ ...uptimeKuma, status: 'stopped' }} onClose={() => {}} />)
  const dialog = screen.getByRole('dialog', { name: 'How to use Uptime Kuma' })
  expect(await within(dialog).findByText('Start Uptime Kuma first. When it is running it opens at:')).toBeInTheDocument()
  expect(within(dialog).queryByRole('link', { name: 'Open Uptime Kuma' })).toBeNull()
})

test('unpins and pins the extension in Applications', async () => {
  mockDetail(uptimeDetail)
  const changed = vi.fn()
  window.addEventListener(APPLICATIONS_CHANGED, changed)
  render(<ExtensionGuide ext={uptimeKuma} onClose={() => {}} />)
  const dialog = screen.getByRole('dialog', { name: 'How to use Uptime Kuma' })

  fireEvent.click(within(dialog).getByRole('button', { name: 'Unpin from Applications' }))
  expect(isPinned('uptime-kuma')).toBe(false)
  expect(changed).toHaveBeenCalledTimes(1)
  expect(await within(dialog).findByRole('button', { name: 'Pin to Applications' })).toHaveAttribute('aria-pressed', 'false')
  expect(within(dialog).queryByText('It is also listed under Applications in the sidebar.')).toBeNull()

  fireEvent.click(within(dialog).getByRole('button', { name: 'Pin to Applications' }))
  expect(isPinned('uptime-kuma')).toBe(true)
  expect(changed).toHaveBeenCalledTimes(2)
  window.removeEventListener(APPLICATIONS_CHANGED, changed)
})

test('reports a guide that cannot be loaded and closes on Escape', async () => {
  mockDetail(uptimeDetail, 500)
  const onClose = vi.fn()
  render(<ExtensionGuide ext={uptimeKuma} onClose={onClose} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('The guide could not be loaded.')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(onClose).toHaveBeenCalledOnce()
})

test('classifies extensions from the catalog, keeping built-in API services', () => {
  expect(extensionKind({ usage_kind: 'api' }, false)).toBe('api')
  expect(extensionKind({ usage_kind: 'web' }, true)).toBe('api')
  expect(extensionKind({ usage_kind: 'none' }, false)).toBe('none')
  // An older API without the field keeps the previous behavior: a quick link.
  expect(extensionKind({}, false)).toBe('web')
  expect(credentialsCommand([])).toBe('')
})

test('uses a configured public URL for the Open link', async () => {
  mockDetail({ ...uptimeDetail, public_url: 'https://status.example.test' })
  render(<ExtensionGuide ext={uptimeKuma} onClose={() => {}} />)
  await waitFor(() => expect(screen.getByRole('link', { name: 'Open Uptime Kuma' }))
    .toHaveAttribute('href', 'https://status.example.test'))
})

test('copies an address where the clipboard is available and hides the button where it is not', async () => {
  mockDetail(krokiDetail)
  const writeText = vi.fn(async () => {})
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  const { unmount } = render(<ExtensionGuide ext={kroki} onClose={() => {}} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Copy address for ODS services' }))
  expect(writeText).toHaveBeenCalledWith('http://kroki:8000')
  unmount()

  delete navigator.clipboard
  mockDetail(krokiDetail)
  render(<ExtensionGuide ext={kroki} onClose={() => {}} />)
  expect(await screen.findByText('http://kroki:8000')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^Copy/ })).toBeNull()
})
