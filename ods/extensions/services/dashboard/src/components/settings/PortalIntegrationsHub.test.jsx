import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router-dom'
import PortalIntegrationsSettings from './PortalIntegrationsSettings'
import { ICON_NAMES, LOGO_NAMES } from './portalIntegrationLogos'
import { HUB_TILES, editedTiles, tileStatus, viewFrom } from './portalIntegrationsHub'
import { NO_SECRETS, approvalLinkFor, draftFrom } from './portalIntegrationsForm'

const APP = { enabled: false, url: 'http://127.0.0.1:1', hasKey: false }
const settings = (patch = {}) => ({
  schemaVersion: 1, revision: 1,
  notifications: { enabled: false, url: 'http://127.0.0.1:8097', topic: 'portal', username: '', notifyApprovals: true, hasPassword: false, approvalLink: '', scheduledTasks: 'all' },
  notes: { enabled: false, vaultPath: '', write: 'ask' },
  mcp: { servers: [] },
  apps: { documents: { ...APP }, photos: { ...APP }, watch: { ...APP, changes: 'ask' },
    news: { ...APP, username: '', digest: { enabled: false, time: '07:00', timezone: 'UTC' } } },
  ...patch,
})
const TELEGRAM = { enabled: false, ownerId: '', apiRoot: 'https://api.telegram.org', hasToken: false, bot: '', lastError: '', lastMessageAt: 0, refusedCount: 0 }
const MEMORY = { enabled: true, askFirst: true, items: [], pending: [], quietChats: [] }
const view = (patch = {}) => ({ settings: settings(), approvals: [], activity: [], telegram: TELEGRAM, memory: MEMORY, ...patch })
const response = body => ({ ok: true, status: 200, json: async () => body })

function Where() {
  const location = useLocation()
  return <output aria-label="Address">{location.search}</output>
}

function show(body = view(), entry = '/settings?section=portal-integrations') {
  vi.stubGlobal('fetch', vi.fn(async () => response(body)))
  // The page asks before leaving with unsaved changes (useBlocker), which needs a data router.
  const router = createMemoryRouter([{ path: '*', element: <><PortalIntegrationsSettings /><Where /></> }],
    { initialEntries: [entry] })
  render(<RouterProvider router={router} />)
}

const tile = name => screen.findByRole('button', { name: new RegExp(`^${name},`) })
const address = () => screen.getByLabelText('Address').textContent

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('tile statuses', () => {
  const status = (id, body) => tileStatus(id, body)

  it('ntfy is on only with an address and a topic', () => {
    expect(status('notifications', view())).toEqual({ tone: 'off', label: 'Off' })
    const on = settings({ notifications: { ...settings().notifications, enabled: true } })
    expect(status('notifications', view({ settings: on }))).toEqual({ tone: 'on', label: 'On' })
    const noTopic = settings({ notifications: { ...on.notifications, topic: '' } })
    expect(status('notifications', view({ settings: noTopic }))).toEqual({ tone: 'setup', label: 'Needs setup' })
  })

  it('Telegram says off, needs setup, on, or a problem', () => {
    expect(status('telegram', view())).toMatchObject({ tone: 'off' })
    expect(status('telegram', view({ telegram: { ...TELEGRAM, enabled: true, hasToken: true } }))).toMatchObject({ tone: 'setup' })
    expect(status('telegram', view({ telegram: { ...TELEGRAM, enabled: true, hasToken: true, ownerId: '123456789' } }))).toMatchObject({ tone: 'on' })
    expect(status('telegram', view({ telegram: { ...TELEGRAM, enabled: true, hasToken: true, ownerId: '1', lastError: 'Telegram refused the bot token.' } })))
      .toEqual({ tone: 'problem', label: 'Problem' })
    expect(status('telegram', view({ telegram: undefined }))).toMatchObject({ tone: 'off' })
  })

  it('Memory counts what is remembered and what waits for review', () => {
    expect(status('memory', view())).toEqual({ tone: 'on', label: '0 memories' })
    expect(status('memory', view({ memory: { ...MEMORY, items: [{ id: 'a', text: 'x' }] } }))).toEqual({ tone: 'on', label: '1 memory' })
    expect(status('memory', view({ memory: { ...MEMORY, items: [], pending: [{ id: 'p' }, { id: 'q' }] } }))).toEqual({ tone: 'waiting', label: '2 to review' })
    expect(status('memory', view({ memory: { ...MEMORY, enabled: false } }))).toMatchObject({ tone: 'off' })
  })

  it('Obsidian needs its vault folder', () => {
    expect(status('notes', view({ settings: settings({ notes: { enabled: true, vaultPath: '', write: 'ask' } }) }))).toMatchObject({ tone: 'setup' })
    expect(status('notes', view({ settings: settings({ notes: { enabled: true, vaultPath: '/v', write: 'ask' } }) }))).toMatchObject({ tone: 'on' })
  })

  it('an app is on only with its saved key and address', () => {
    for (const id of ['documents', 'photos', 'watch', 'news']) {
      const apps = on => ({ ...settings().apps, [id]: { ...settings().apps[id], ...on } })
      expect(status(id, view())).toMatchObject({ tone: 'off' })
      expect(status(id, view({ settings: settings({ apps: apps({ enabled: true }) }) }))).toMatchObject({ tone: 'setup' })
      expect(status(id, view({ settings: settings({ apps: apps({ enabled: true, hasKey: true }) }) }))).toMatchObject({ tone: 'on' })
      expect(status(id, view({ settings: settings({ apps: apps({ enabled: true, hasKey: true, url: '' }) }) }))).toMatchObject({ tone: 'setup' })
    }
  })

  it('MCP counts the servers that are on', () => {
    expect(status('mcp', view())).toEqual({ tone: 'off', label: 'None yet' })
    const servers = [{ id: 'a', enabled: true }, { id: 'b', enabled: false }]
    expect(status('mcp', view({ settings: settings({ mcp: { servers } }) }))).toEqual({ tone: 'on', label: '1 on' })
    expect(status('mcp', view({ settings: settings({ mcp: { servers: [servers[1]] } }) }))).toEqual({ tone: 'off', label: 'Off' })
  })

  it('approvals and activity count their entries', () => {
    expect(status('approvals', view())).toEqual({ tone: 'info', label: 'Nothing waiting' })
    expect(status('approvals', view({ approvals: [{}, {}] }))).toEqual({ tone: 'waiting', label: '2 waiting' })
    expect(status('activity', view())).toEqual({ tone: 'info', label: 'None yet' })
    expect(status('activity', view({ activity: [{}] }))).toEqual({ tone: 'info', label: '1 entry' })
  })

  it('an unknown tile is a programming error, not a blank chip', () => {
    expect(() => status('nope', view())).toThrow('Unknown Portal integrations tile: nope')
  })
})

describe('unsaved tiles and links', () => {
  it('marks the tiles whose part of the draft changed or holds a typed secret', () => {
    const saved = draftFrom(settings())
    expect(editedTiles(saved, saved, NO_SECRETS)).toEqual(new Set())
    const draft = { ...saved, notes: { ...saved.notes, write: 'allow' }, apps: { ...saved.apps, photos: { ...saved.apps.photos, enabled: true } } }
    expect(editedTiles(draft, saved, NO_SECRETS)).toEqual(new Set(['notes', 'photos']))
    expect(editedTiles(saved, saved, { ...NO_SECRETS, ntfyPassword: 'pw', apps: { news: 'key' }, mcpEnv: { ha: { TOKEN: 't' } } }))
      .toEqual(new Set(['notifications', 'news', 'mcp']))
    expect(editedTiles(saved, saved, { ...NO_SECRETS, mcp: { ha: '' }, mcpEnv: { ha: { TOKEN: '' } } })).toEqual(new Set())
  })

  it('opens only views it knows', () => {
    expect(viewFrom('telegram')).toBe('telegram')
    expect(viewFrom('nope')).toBe('')
    expect(viewFrom(null)).toBe('')
    expect(viewFrom('__proto__')).toBe('')
  })

  it('every tile has a bundled mark, and ntfy approval buttons open the approvals view', () => {
    for (const item of HUB_TILES) expect(item.logo ? LOGO_NAMES : ICON_NAMES).toContain(item.logo || item.icon)
    expect(approvalLinkFor('http://ods.local:3001')).toBe('http://ods.local:3001/settings?section=portal-integrations&view=approvals')
  })
})

describe('the hub', () => {
  it('shows every integration with its logo and status, and no settings until one is opened', async () => {
    show(view({ approvals: [{ id: 'a', summary: 'x', createdAt: 1 }] }))
    const list = await screen.findByRole('list', { name: 'Integrations list' })
    const names = within(list).getAllByRole('button').map(button => button.getAttribute('aria-label').split(',')[0])
    expect(names).toEqual(HUB_TILES.map(item => item.title))
    for (const button of within(list).getAllByRole('button')) expect(button.querySelector('[data-mark]')).not.toBeNull()
    expect(within(list).getByRole('button', { name: /^Approvals, Actions waiting for you, 1 waiting$/ })).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Integrations' })).getByRole('note')).toHaveTextContent('Portal is waiting for your approval.')
    expect(screen.queryByLabelText('Send notifications through ntfy')).not.toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Telegram' })).toBeNull()
  })

  it('leaves out Telegram and Memory when the service has neither', async () => {
    show(view({ telegram: undefined, memory: undefined }))
    await tile('ntfy')
    expect(screen.queryByRole('button', { name: /^Telegram,/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Memory,/ })).toBeNull()
  })

  it('opens a tile as its own view in the address, and goes back to that tile', async () => {
    show()
    expect(await screen.findByText(/Connect Portal to the apps and services you use/)).toBeVisible()
    fireEvent.click(await tile('Telegram'))
    expect(address()).toBe('?section=portal-integrations&view=telegram')
    // Inside a view, the settings come first: no introduction above them.
    expect(screen.queryByText(/Connect Portal to the apps and services you use/)).toBeNull()
    expect(screen.getByRole('heading', { name: 'Telegram' })).toBeVisible()
    expect(screen.queryByRole('list', { name: 'Integrations list' })).toBeNull()
    expect(screen.getByLabelText('Send notifications through ntfy')).not.toBeVisible()
    const back = screen.getByRole('button', { name: 'All integrations' })
    expect(back).toHaveFocus()
    fireEvent.click(back)
    expect(address()).toBe('?section=portal-integrations')
    await waitFor(() => expect(screen.getByRole('button', { name: /^Telegram,/ })).toHaveFocus())
  })

  it('opens the view a link names, and the hub for one it does not know', async () => {
    show(view(), '/settings?section=portal-integrations&view=approvals')
    expect(await screen.findByText('Nothing is waiting.')).toBeVisible()
    expect(screen.getByRole('navigation', { name: 'Integration' })).toHaveTextContent('Approvals')
  })

  it('treats an unknown view in the address as the hub', async () => {
    show(view(), '/settings?section=portal-integrations&view=bogus')
    expect(await screen.findByRole('list', { name: 'Integrations list' })).toBeInTheDocument()
  })

  it('a link to Telegram on a service without it shows the hub', async () => {
    show(view({ telegram: undefined }), '/settings?section=portal-integrations&view=telegram')
    expect(await screen.findByRole('list', { name: 'Integrations list' })).toBeInTheDocument()
  })

  it('marks unsaved changes on the tile and in the bar, and keeps them across views', async () => {
    show()
    fireEvent.click(await tile('Obsidian'))
    fireEvent.change(screen.getByLabelText('Adding to notes'), { target: { value: 'allow' } })
    expect(screen.getByRole('navigation', { name: 'Integration' })).toHaveTextContent('Unsaved changes')
    fireEvent.click(screen.getByRole('button', { name: 'All integrations' }))
    expect(await tile('Obsidian')).toHaveAccessibleName('Obsidian, Your notes, Off, unsaved changes')
    fireEvent.click(await tile('Immich'))
    fireEvent.change(within(screen.getByRole('listitem', { name: 'Photos' })).getByLabelText('API key'), { target: { value: 'k' } })
    expect(screen.getByRole('region', { name: 'Unsaved changes' })).toHaveTextContent('You have unsaved changes in Obsidian, Immich.')
    fireEvent.click(screen.getByRole('button', { name: 'All integrations' }))
    fireEvent.click(await tile('Obsidian'))
    expect(screen.getByLabelText('Adding to notes')).toHaveValue('allow')
  })

  it('keeps typing in a view with its own save, such as Telegram, across a visit to another view', async () => {
    show()
    fireEvent.click(await tile('Telegram'))
    fireEvent.change(screen.getByLabelText('Your Telegram user ID'), { target: { value: '583920174' } })
    fireEvent.click(screen.getByRole('button', { name: 'All integrations' }))
    fireEvent.click(await tile('ntfy'))
    fireEvent.click(screen.getByRole('button', { name: 'All integrations' }))
    fireEvent.click(await tile('Telegram'))
    expect(screen.getByLabelText('Your Telegram user ID')).toHaveValue('583920174')
  })

  it('Review in the approvals note opens the approvals view', async () => {
    show(view({ approvals: [{ id: 'a', summary: 'Turn on the kitchen light', createdAt: 1 }, { id: 'b', summary: 'Add a note', createdAt: 2 }] }))
    expect(await within(await screen.findByRole('region', { name: 'Integrations' })).findByRole('note')).toHaveTextContent('Portal is waiting for 2 approvals.')
    fireEvent.click(screen.getByRole('button', { name: 'Review' }))
    expect(address()).toBe('?section=portal-integrations&view=approvals')
    expect(screen.getByText('Turn on the kitchen light')).toBeVisible()
  })
})

it('a link straight to a view moves focus there once the page has loaded', async () => {
  show(view(), '/settings?section=portal-integrations&view=approvals')
  const back = await screen.findByRole('button', { name: 'All integrations' })
  await waitFor(() => expect(document.activeElement).toBe(back))
})
