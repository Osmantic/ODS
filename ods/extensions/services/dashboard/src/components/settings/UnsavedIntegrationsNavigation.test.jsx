import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createBrowserRouter, createMemoryRouter, Link, Route, RouterProvider, Routes, useLocation, useNavigate } from 'react-router-dom'
import PortalIntegrationsSettings from './PortalIntegrationsSettings'

const SETTINGS = '/settings?section=portal-integrations'
const routers = []
const dialogMethods = Object.fromEntries(['showModal', 'close'].map(name => [name, Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, name)]))
const response = body => ({ ok: true, json: async () => globalThis.structuredClone(body) })

function service() {
  let settings = { schemaVersion: 1, revision: 0,
    notifications: { enabled: false, url: 'http://fixture.invalid', topic: 'fixture', username: '', notifyApprovals: true, approvalLink: '', hasPassword: false },
    notes: { enabled: false, vaultPath: '', write: 'ask' }, mcp: { servers: [] } }
  let release, hold = false
  const view = () => ({ settings, approvals: [], activity: [] })
  const fetchMock = vi.fn(async (_url, options) => {
    if (options.method === 'POST') {
      const payload = JSON.parse(options.body)
      if (hold) await new Promise(resolve => { release = resolve })
      settings = { ...settings, ...payload.settings, revision: settings.revision + 1,
        notifications: { ...payload.settings.notifications, hasPassword: Boolean(payload.secrets.ntfyPassword) } }
    }
    return response(view())
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, holdSave: () => { hold = true }, releaseSave: () => release() }
}

function SettingsPage() {
  const location = useLocation()
  const visible = new URLSearchParams(location.search).get('section') === 'portal-integrations'
  return <><p>Settings panel</p><div hidden={!visible}><PortalIntegrationsSettings /></div>
    {!visible && <p>Other Settings section</p>}</>
}

function Shell() {
  const navigate = useNavigate()
  return <><nav aria-label="Fixture navigation">
    <Link to={SETTINGS}>Integration settings</Link>
    <Link to="/models">Models</Link>
    <button onClick={() => navigate('/')}>Close panel</button>
  </nav><Routes>
    <Route path="/settings" element={<SettingsPage />} />
    <Route path="/models" element={<p>Models page</p>} />
    <Route path="*" element={<p>Portal chat</p>} />
  </Routes></>
}

function mount({ entries = [SETTINGS], index = entries.length - 1, browser = false } = {}) {
  // Same catch-all data-router seam as main.jsx; descendant App-style Routes
  // remain declarative. No history interception or synthetic popstate events.
  const router = browser ? createBrowserRouter([{ path: '*', element: <Shell /> }])
    : createMemoryRouter([{ path: '*', element: <Shell /> }], { initialEntries: entries, initialIndex: index })
  routers.push(router)
  render(<RouterProvider router={router} />)
  return router
}

beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value() { this.open = true } })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value() { this.open = false } })
})
afterEach(() => {
  cleanup()
  for (const router of routers.splice(0)) router.dispose()
  vi.restoreAllMocks(); vi.unstubAllGlobals()
  for (const [name, descriptor] of Object.entries(dialogMethods)) {
    if (descriptor) Object.defineProperty(HTMLDialogElement.prototype, name, descriptor)
    else delete HTMLDialogElement.prototype[name]
  }
})

async function edit() {
  fireEvent.change(await screen.findByLabelText('User (optional)'), { target: { value: 'Unsaved edit' } })
}

it('blocks Link navigation until Stay or Leave is chosen and never stores the typed credential', async () => {
  service()
  const storage = vi.spyOn(Storage.prototype, 'setItem')
  const router = mount()
  await edit()
  fireEvent.change(screen.getByLabelText('Password or access token'), { target: { value: 'invented-unsaved-secret' } })
  fireEvent.click(screen.getByRole('link', { name: 'Models' }))
  const dialog = await screen.findByRole('dialog', { name: 'Leave Portal integrations?' })
  expect(router.state.location.pathname).toBe('/settings')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Stay here' }))
  await waitFor(() => expect(dialog).not.toBeVisible())
  expect(screen.getByLabelText('User (optional)')).toHaveValue('Unsaved edit')
  expect(screen.getByLabelText('Password or access token')).toHaveValue('invented-unsaved-secret')
  fireEvent.click(screen.getByRole('link', { name: 'Models' }))
  await waitFor(() => expect(dialog).toBeVisible())
  fireEvent.click(within(dialog).getByRole('button', { name: 'Leave without saving' }))
  await screen.findByText('Models page')
  expect(router.state.location.pathname).toBe('/models')
  expect(storage.mock.calls.some(call => call.some(value => String(value).includes('invented-unsaved-secret')))).toBe(false)
})

it('keeps Settings drafts across sections and shows a portaled blocker when closing a hidden editor', async () => {
  service(); mount(); await edit()
  fireEvent.click(screen.getByRole('link', { name: 'Portal access' }))
  await screen.findByText('Other Settings section')
  expect(screen.getByLabelText('User (optional)')).not.toBeVisible()
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Close panel' }))
  const dialog = await screen.findByRole('dialog', { name: 'Leave Portal integrations?' })
  expect(dialog.parentElement).toBe(document.body)
  expect(dialog).toBeVisible()
  fireEvent(dialog, new Event('cancel', { cancelable: true }))
  await waitFor(() => expect(dialog).not.toBeVisible())
  fireEvent.click(screen.getByRole('link', { name: 'Integration settings' }))
  expect(await screen.findByLabelText('User (optional)')).toHaveValue('Unsaved edit')
})

for (const direction of [-1, 1]) it(`blocks router POP ${direction < 0 ? 'Back' : 'Forward'} using the official blocker`, async () => {
  service()
  const entries = direction < 0 ? ['/models', SETTINGS] : [SETTINGS, '/models']
  const router = mount({ entries, index: direction < 0 ? 1 : 0 })
  await edit()
  await act(async () => { await router.navigate(direction) })
  const dialog = await screen.findByRole('dialog', { name: 'Leave Portal integrations?' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Stay here' }))
  await waitFor(() => expect(dialog).not.toBeVisible())
  expect(router.state.location.pathname).toBe('/settings')
  await act(async () => { await router.navigate(direction) })
  await waitFor(() => expect(dialog).toBeVisible())
  fireEvent.click(within(dialog).getByRole('button', { name: 'Leave without saving' }))
  await screen.findByText('Models page')
})

it('a confirmed save clears a pending navigation block without silently navigating away', async () => {
  const backend = service(); mount(); await edit()
  backend.holdSave()
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  fireEvent.click(screen.getByRole('button', { name: 'Close panel' }))
  const dialog = await screen.findByRole('dialog', { name: 'Leave Portal integrations?' })
  await act(async () => { backend.releaseSave() })
  await screen.findByText('Saved.')
  await waitFor(() => expect(dialog).not.toBeVisible())
  expect(screen.getByText('Settings panel')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Close panel' }))
  await screen.findByText('Portal chat')
})

it('hard reload uses beforeunload separately, while a saved form no longer blocks it', async () => {
  service(); mount(); await edit()
  const dirty = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(dirty)
  expect(dirty.defaultPrevented).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await screen.findByText('Saved.')
  const saved = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(saved)
  expect(saved.defaultPrevented).toBe(false)
})

it('browser-router Back and Forward preserve normal navigation and block a dirty POP without manual history hooks', async () => {
  service()
  const original = window.location.pathname + window.location.search
  const router = mount({ browser: true })
  try {
    await act(async () => { await router.navigate(SETTINGS) })
    await edit()
    window.history.back()
    const dialog = await screen.findByRole('dialog', { name: 'Leave Portal integrations?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Stay here' }))
    await waitFor(() => expect(router.state.blockers.size === 0 || [...router.state.blockers.values()].every(value => value.state === 'unblocked')).toBe(true))
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe(SETTINGS))
    window.history.back()
    await waitFor(() => expect(dialog).toBeVisible())
    fireEvent.click(within(dialog).getByRole('button', { name: 'Leave without saving' }))
    await waitFor(() => expect(router.state.location.pathname + router.state.location.search).toBe(original))
    window.history.forward()
    await screen.findByText('Settings panel')
    expect(await screen.findByLabelText('User (optional)')).toHaveValue('')
  } finally {
    // Restore only this disposable jsdom page's current URL after disposal.
    router.dispose(); window.history.replaceState(null, '', original)
  }
})
