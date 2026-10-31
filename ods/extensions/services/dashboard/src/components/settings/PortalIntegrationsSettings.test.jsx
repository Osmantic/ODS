import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { createElement } from 'react'
import { render } from '../../test/test-utils'
import PortalIntegrationsSettings from './PortalIntegrationsSettings'
import { newServerId, savedTools, secretsFrom, typedForDraft } from './portalIntegrationsForm'

const TOOLS = [
  { name: 'GetLiveContext', title: '', description: 'Read device states', readOnly: true, destructive: false, inputSchema: {}, policy: 'allow' },
  { name: 'HassTurnOn', title: '', description: 'Turn a device on', readOnly: false, destructive: false, inputSchema: {}, policy: 'ask' },
]
const APPS = {
  documents: { enabled: false, url: 'http://127.0.0.1:7807' },
  photos: { enabled: false, url: 'http://127.0.0.1:2283' },
  watch: { enabled: false, url: 'http://127.0.0.1:11039', changes: 'ask' },
  news: { enabled: false, url: 'http://127.0.0.1:11029', username: '', digest: { enabled: false, time: '07:00', timezone: 'UTC' } },
}
const savedApps = (apps = APPS, keys = {}) => Object.fromEntries(Object.entries(apps).map(([id, app]) => [id, { ...app, hasKey: Boolean(keys[id]) }]))
const settings = (revision = 0, servers = [], apps = savedApps()) => ({
  schemaVersion: 1, revision,
  notifications: { enabled: false, url: 'http://127.0.0.1:8097', topic: 'portal', username: '', notifyApprovals: true, hasPassword: false,
    approvalLink: '', scheduledTasks: 'all' },
  notes: { enabled: false, vaultPath: '', write: 'ask' },
  mcp: { servers },
  apps,
})
const view = (overrides = {}) => ({ settings: settings(), approvals: [], activity: [], ...overrides })
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })

const WATCHES = { total: 2, watches: [
  { id: 'w1', title: 'Kettle', url: 'https://shop.example/kettle', lastChanged: 5, below: 50, lastPrice: 55 },
  { id: 'w2', title: '', url: 'https://shop.example/hours', lastChanged: 0, below: null, lastPrice: null },
] }

// A fake service: save stores the settings, discover adds the tools.
function fakeService(initial = view(), { watches = WATCHES } = {}) {
  let current = initial
  const posts = []
  const fetchMock = vi.fn(async (url, options) => {
    if (options.method !== 'POST') return response(current)
    const action = url.split('/').pop()
    const body = JSON.parse(options.body)
    posts.push([action, body])
    if (action === 'save') {
      if (body.expectedRevision !== current.settings.revision) return response({ detail: 'These settings changed in another window.', code: 'stale-revision' }, 409)
      const servers = body.settings.mcp.servers.map(server => ({ ...server, hasToken: Boolean(body.secrets.mcp?.[server.id]),
        hasEnv: Object.fromEntries(server.envNames.map(name => [name, Boolean(body.secrets.mcpEnv?.[server.id]?.[name])])),
        tools: current.settings.mcp.servers.find(item => item.id === server.id)?.tools || [], discoveredAt: current.settings.mcp.servers.find(item => item.id === server.id)?.discoveredAt || null }))
      const keys = Object.fromEntries(Object.entries(current.settings.apps).map(([id, app]) => [id, app.hasKey || Boolean(body.secrets.apps?.[id])]))
      current = { ...current, settings: { ...settings(body.expectedRevision + 1, servers, savedApps(body.settings.apps, keys)),
        notifications: { ...body.settings.notifications, hasPassword: Boolean(body.secrets.ntfyPassword) }, notes: body.settings.notes } }
    } else if (action === 'discover') {
      const servers = current.settings.mcp.servers.map(server => server.id === body.serverId ? { ...server, tools: TOOLS, discoveredAt: 1 } : server)
      current = { ...current, settings: { ...current.settings, revision: current.settings.revision + 1, mcp: { servers } } }
    } else if (action === 'test') {
      return response({ ...current, notice: 'Echo answered with 1 tools.' })
    } else if (action === 'test-app') {
      if (body.app === 'documents') return response({ detail: 'Paperless-ngx refused the token or password.', code: 'app-refused' }, 400)
      return response({ ...current, notice: 'FreshRSS answered: 2 feeds.' })
    } else if (action === 'watch-list') {
      if (watches instanceof Error) return response({ detail: watches.message, code: 'app-unreachable' }, 400)
      return response({ ...current, watches })
    } else if (action === 'send-digest') {
      return response({ ...current, notice: 'Digest sent with 2 stories.' })
    } else if (action === 'clear-activity') {
      current = { ...current, activity: [] }
    } else if (action === 'decide') {
      current = { ...current, approvals: [] }
    }
    return response(current)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(createElement(PortalIntegrationsSettings))
  return { posts, fetchMock }
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('saves only what changed and never sends an empty password', async () => {
  const { posts } = fakeService()
  await screen.findByText('Notifications (ntfy)')
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  fireEvent.click(screen.getByLabelText('Send notifications through ntfy'))
  fireEvent.change(screen.getByLabelText('User (optional)'), { target: { value: 'portal' } })
  fireEvent.change(screen.getByLabelText('Password or access token'), { target: { value: 'pw' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await screen.findByText('Saved.')
  expect(posts).toEqual([['save', { expectedRevision: 0, secrets: { ntfyPassword: 'pw' }, settings: {
    notifications: { enabled: true, url: 'http://127.0.0.1:8097', topic: 'portal', username: 'portal', notifyApprovals: true,
      approvalLink: `${window.location.origin}/settings?section=portal-integrations`, scheduledTasks: 'all' },
    notes: { enabled: false, vaultPath: '', write: 'ask' }, mcp: { servers: [] }, apps: APPS } }]])
  expect(screen.getByLabelText('Password or access token')).toHaveValue('')
  expect(screen.getByLabelText('Password or access token')).toHaveAttribute('placeholder', 'Saved. Type to replace.')
})

it('shows a server’s tools and their effect before it can be turned on', async () => {
  const { posts } = fakeService()
  fireEvent.click(await screen.findByRole('button', { name: 'Add Home Assistant' }))
  const card = screen.getByRole('listitem', { name: 'MCP server Home Assistant' })
  expect(within(card).getByLabelText('Let Portal use this server')).toBeDisabled()
  fireEvent.change(within(card).getByLabelText('Long-lived access token'), { target: { value: 'token' } })
  fireEvent.click(within(card).getByRole('button', { name: 'Check tools' }))
  await screen.findByText(/Checked the tools of Home Assistant/)
  expect(posts.map(([action]) => action)).toEqual(['save', 'discover'])
  expect(posts[0][1].secrets).toEqual({ mcp: { 'home-assistant': 'token' } })
  expect(posts[1][1]).toEqual({ serverId: 'home-assistant' })
  const tools = screen.getByRole('list', { name: 'Tools of Home Assistant' })
  expect(within(tools).getByText(/Only reads/)).toBeInTheDocument()
  expect(within(tools).getByLabelText('Permission for HassTurnOn')).toHaveValue('ask')
  fireEvent.change(within(tools).getByLabelText('Permission for HassTurnOn'), { target: { value: 'allow' } })
  fireEvent.click(screen.getByLabelText('Let Portal use this server'))
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await screen.findByText('Saved.')
  expect(posts[2][1].settings.mcp.servers[0]).toMatchObject({ enabled: true, url: 'http://homeassistant.local:8123/api/mcp',
    policies: { GetLiveContext: 'allow', HassTurnOn: 'allow' } })
})

it('approves a waiting action and shows the service’s refusal sentences', async () => {
  const approval = { id: 'a1', kind: 'mcp', summary: 'Home Assistant: HassTurnOn {"name":"Kitchen"}', createdAt: 1, expiresAt: 2 }
  const activity = [{ at: 1, action: 'notes-search', target: 'deposit', outcome: '1 notes' }]
  const { posts } = fakeService(view({ approvals: [approval], settings: settings(3), activity }))
  expect(await screen.findByText(/Searched notes: deposit \(1 notes\)/)).toBeInTheDocument()
  fireEvent.click(await screen.findByRole('button', { name: 'Approve' }))
  await screen.findByText('Nothing is waiting.')
  expect(posts).toEqual([['decide', { approvalId: 'a1', decision: 'approve' }]])

  vi.stubGlobal('fetch', vi.fn(async (url, options) => options.method === 'POST'
    ? response({ detail: 'The vault folder must be an existing folder, given as a full path.', code: 'invalid-settings' }, 400)
    : response(view())))
  fireEvent.click(screen.getByLabelText('Let Portal search and read my notes'))
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('The vault folder must be an existing folder')
  await waitFor(() => expect(screen.getByLabelText('Let Portal search and read my notes')).toBeChecked())
})

it('keeps saved tools only while the address is unchanged and makes unique ids', () => {
  const saved = { kind: 'http', url: 'http://a/api/mcp', command: '', discoveredAt: 1, tools: TOOLS }
  expect(savedTools(saved, { kind: 'http', url: 'http://a/api/mcp', command: '' })).toBe(TOOLS)
  expect(savedTools(saved, { kind: 'http', url: 'http://b/api/mcp', command: '' })).toBeNull()
  expect(newServerId('Home Assistant', [{ id: 'home-assistant' }])).toBe('home-assistant-2')
  expect(newServerId('!!', [])).toBe('server')
  expect(secretsFrom({ ntfyPassword: '', mcp: { a: '', b: 't' } })).toEqual({ mcp: { b: 't' } })
})

it('keeps Save and Discard in reach while changes are unsaved, and warns before leaving', async () => {
  const { posts } = fakeService()
  await screen.findByText('Notes (Obsidian vault)')
  expect(screen.queryByRole('region', { name: 'Unsaved changes' })).toBeNull()
  const leaving = vi.spyOn(window, 'addEventListener')
  fireEvent.change(screen.getByLabelText('Adding to notes'), { target: { value: 'allow' } })
  const bar = screen.getByRole('region', { name: 'Unsaved changes' })
  expect(leaving).toHaveBeenCalledWith('beforeunload', expect.any(Function))
  fireEvent.click(within(bar).getByRole('button', { name: 'Discard' }))
  expect(screen.getByLabelText('Adding to notes')).toHaveValue('ask')
  expect(screen.queryByRole('region', { name: 'Unsaved changes' })).toBeNull()
  expect(posts).toEqual([])
  fireEvent.change(screen.getByLabelText('Adding to notes'), { target: { value: 'allow' } })
  fireEvent.click(within(screen.getByRole('region', { name: 'Unsaved changes' })).getByRole('button', { name: 'Save' }))
  await screen.findByText('Saved.')
  expect(posts[0][1].settings.notes.write).toBe('allow')
  expect(screen.queryByRole('region', { name: 'Unsaved changes' })).toBeNull()
})

it('sets up a local server with secret variables, shows its status and restarts or tests it', async () => {
  const saved = { id: 'echo', name: 'Echo', kind: 'stdio', preset: '', enabled: false, url: '', command: 'echo-server', workingFolder: '',
    envNames: ['API_KEY'], hasEnv: { API_KEY: true }, hasToken: false, discoveredAt: 1,
    tools: [{ ...TOOLS[0], name: 'echo', new: false }, { ...TOOLS[1], name: 'shout', policy: 'off', new: true }] }
  const processes = { echo: { running: false, lastError: 'Error: API_KEY is not valid' } }
  const { posts } = fakeService(view({ settings: settings(2, [saved]), processes }))
  const card = await screen.findByRole('listitem', { name: 'MCP server Echo' })
  expect(within(card).getByRole('note')).toHaveTextContent('runs on this computer with your permissions')
  expect(within(card).getByLabelText('Last error output')).toHaveTextContent('API_KEY is not valid')
  expect(within(card).getByRole('button', { name: 'Restart' })).toBeDisabled()
  expect(within(card).getByRole('status')).toHaveTextContent('1 new tool since the last check. New tools start Off')
  expect(within(card).getByText('New')).toBeInTheDocument()
  expect(within(card).getByLabelText('API_KEY')).toHaveAttribute('placeholder', 'Saved. Type to replace.')
  fireEvent.change(within(card).getByLabelText('API_KEY'), { target: { value: 'new-key' } })
  expect(within(card).getByRole('button', { name: 'Add variable' })).toBeDisabled()
  fireEvent.change(within(card).getByLabelText('New variable name'), { target: { value: 'PATH' } })
  expect(within(card).getByRole('button', { name: 'Add variable' })).toBeDisabled()
  fireEvent.change(within(card).getByLabelText('New variable name'), { target: { value: 'REGION' } })
  fireEvent.click(within(card).getByRole('button', { name: 'Add variable' }))
  fireEvent.change(within(card).getByLabelText('REGION'), { target: { value: 'eu' } })
  fireEvent.change(within(card).getByLabelText('Working folder (optional, full path)'), { target: { value: '/srv/echo' } })
  fireEvent.click(within(card).getByRole('button', { name: 'Test connection' }))
  await screen.findByText('Echo answered with 1 tools.')
  expect(posts.map(([action]) => action)).toEqual(['save', 'test'])
  expect(posts[0][1].secrets).toEqual({ mcpEnv: { echo: { API_KEY: 'new-key', REGION: 'eu' } } })
  expect(posts[0][1].settings.mcp.servers[0]).toMatchObject({ envNames: ['API_KEY', 'REGION'], workingFolder: '/srv/echo', command: 'echo-server' })
  expect(posts[1][1]).toEqual({ serverId: 'echo' })
})

it('restarts a running local server and clears the activity list', async () => {
  const saved = { id: 'echo', name: 'Echo', kind: 'stdio', preset: '', enabled: true, url: '', command: 'echo-server', workingFolder: '',
    envNames: [], hasEnv: {}, hasToken: false, discoveredAt: 1, tools: [] }
  const activity = [{ at: 1, action: 'mcp-restart', target: 'Echo', outcome: 'stopped; starts on next use' }]
  const { posts } = fakeService(view({ settings: settings(2, [saved]), processes: { echo: { running: true, lastError: '' } }, activity }))
  expect(await screen.findByText(/Restarted an MCP server: Echo/)).toBeInTheDocument()
  expect(screen.getByText(/Running\. It stops after 10 minutes/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Restart' }))
  await screen.findByText('Echo was stopped. It starts again when it is next used.')
  fireEvent.click(screen.getByRole('button', { name: 'Clear activity' }))
  await screen.findByText('No activity yet.')
  expect(posts).toEqual([['restart', { serverId: 'echo' }], ['clear-activity', {}]])
})

it('drops typed values of removed variables and servers', () => {
  const draft = { servers: [{ id: 'a', envNames: ['KEEP'] }] }
  const typed = { ntfyPassword: '', mcp: { a: 't', gone: 'x' }, mcpEnv: { a: { KEEP: '1', DROPPED: '2' }, gone: { X: '3' } } }
  expect(secretsFrom(typedForDraft(typed, draft))).toEqual({ mcp: { a: 't' }, mcpEnv: { a: { KEEP: '1' } } })
})

it('gives a new server the id of its name, with its secrets, and keeps saved ids', async () => {
  const saved = { id: 'home', name: 'Home', kind: 'http', preset: '', enabled: false, url: 'http://a/api/mcp', command: '', workingFolder: '',
    envNames: [], hasEnv: {}, hasToken: true, discoveredAt: null, tools: [] }
  const { posts } = fakeService(view({ settings: settings(1, [saved]) }))
  fireEvent.click(await screen.findByRole('button', { name: 'Add MCP server' }))
  fireEvent.change(screen.getByRole('listitem', { name: 'MCP server MCP server' }).querySelector('input'), { target: { value: 'Files' } })
  const card = screen.getByRole('listitem', { name: 'MCP server Files' })
  fireEvent.change(within(card).getByLabelText('Connection'), { target: { value: 'stdio' } })
  fireEvent.change(within(card).getByLabelText('Command', { exact: true }), { target: { value: 'npx files' } })
  fireEvent.change(within(card).getByLabelText('New variable name'), { target: { value: 'KEY' } })
  fireEvent.click(within(card).getByRole('button', { name: 'Add variable' }))
  fireEvent.change(within(card).getByLabelText('KEY'), { target: { value: 'v' } })
  fireEvent.change(screen.getByRole('listitem', { name: 'MCP server Home' }).querySelector('input'), { target: { value: 'Renamed' } })
  fireEvent.click(within(card).getByRole('button', { name: 'Check tools' }))
  await screen.findByText(/Checked the tools of Files/)
  expect(posts[0][1].settings.mcp.servers.map(server => server.id)).toEqual(['home', 'files'])
  expect(posts[0][1].secrets).toEqual({ mcpEnv: { files: { KEY: 'v' } } })
  expect(posts[1]).toEqual(['discover', { serverId: 'files' }])
})

it('connects an app with a secret that is never shown back, then tests it', async () => {
  const { posts } = fakeService()
  const card = await screen.findByRole('listitem', { name: 'News' })
  fireEvent.click(within(card).getByLabelText('Let Portal read my news feeds'))
  fireEvent.change(within(card).getByLabelText('FreshRSS user'), { target: { value: 'owner' } })
  fireEvent.change(within(card).getByLabelText('API password'), { target: { value: 'secret' } })
  fireEvent.click(within(card).getByRole('button', { name: 'Test FreshRSS' }))
  await screen.findByText('FreshRSS answered: 2 feeds.')
  expect(posts.map(([action]) => action)).toEqual(['save', 'test-app'])
  expect(posts[0][1].secrets).toEqual({ apps: { news: 'secret' } })
  expect(posts[0][1].settings.apps.news).toMatchObject({ enabled: true, username: 'owner' })
  expect(posts[1][1]).toEqual({ app: 'news' })
  // The answer also shows in the card, next to the button that asked.
  expect(within(card).getByRole('status')).toHaveTextContent('FreshRSS answered: 2 feeds.')
  expect(within(screen.getByRole('listitem', { name: 'Photos' })).queryByRole('status')).toBeNull()
  expect(within(card).getByLabelText('API password')).toHaveValue('')
  expect(within(card).getByLabelText('API password')).toHaveAttribute('placeholder', 'Saved. Type to replace.')
})

it('keeps the digest in the owner\'s time zone and asks before watching pages by default', async () => {
  const { posts } = fakeService()
  const news = await screen.findByRole('listitem', { name: 'News' })
  expect(within(news).getByLabelText(/Digest time/)).toBeDisabled()
  fireEvent.click(within(news).getByLabelText('Send me a daily news digest'))
  fireEvent.change(within(news).getByLabelText(/Digest time/), { target: { value: '06:45' } })
  const watch = screen.getByRole('listitem', { name: 'Watched pages' })
  expect(within(watch).getByLabelText('Adding and removing watched pages')).toHaveValue('ask')
  expect(within(watch).getByRole('button', { name: 'Test changedetection.io' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Notify me when a scheduled task'), { target: { value: 'failures' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await screen.findByText('Saved.')
  const saved = posts[0][1].settings
  expect(saved.apps.news.digest).toEqual({ enabled: true, time: '06:45', timezone: Intl.DateTimeFormat().resolvedOptions().timeZone })
  expect(saved.notifications.scheduledTasks).toBe('failures')
  expect(posts[0][1].secrets).toEqual({})
})

it('shows a refused app key as an error in that app\'s card', async () => {
  fakeService()
  const card = await screen.findByRole('listitem', { name: 'Documents' })
  fireEvent.change(within(card).getByLabelText('API token'), { target: { value: 'wrong' } })
  fireEvent.click(within(card).getByRole('button', { name: 'Test Paperless-ngx' }))
  expect(await within(card).findByRole('alert')).toHaveTextContent('Paperless-ngx refused the token or password.')
  // The token was saved before the test; nothing is left unsaved, and saving again is not stale.
  expect(screen.queryByRole('region', { name: 'Unsaved changes' })).toBeNull()
  expect(within(card).getByLabelText('API token')).toHaveAttribute('placeholder', 'Saved. Type to replace.')
})

it('never gives a new MCP server an id the apps use', () => {
  expect(newServerId('News', [])).toBe('news-2')
  expect(newServerId('Photos', [{ id: 'photos-2' }])).toBe('photos-3')
  expect(newServerId('Home', [])).toBe('home')
})

const withApps = (patch, keys, notificationsOn = false) => {
  const base = view({ settings: settings(0, [], savedApps({ ...APPS, ...patch }, keys)) })
  base.settings.notifications.enabled = notificationsOn
  return base
}

it('tests an app before it is turned on, and leaves it off', async () => {
  const { posts } = fakeService()
  const card = await screen.findByRole('listitem', { name: 'News' })
  fireEvent.change(within(card).getByLabelText('API password'), { target: { value: 'secret' } })
  fireEvent.click(within(card).getByRole('button', { name: 'Test FreshRSS' }))
  expect(await within(card).findByRole('status')).toHaveTextContent('FreshRSS answered: 2 feeds.')
  expect(posts.map(([action]) => action)).toEqual(['save', 'test-app'])
  expect(posts[0][1].settings.apps.news.enabled).toBe(false)
  expect(within(card).getByLabelText('Let Portal read my news feeds')).not.toBeChecked()
})

it('lists the watched pages with their price limits once watching is saved', async () => {
  const { posts } = fakeService(withApps({ watch: { ...APPS.watch, enabled: true } }, { watch: true }))
  const card = await screen.findByRole('listitem', { name: 'Watched pages' })
  const list = await within(card).findByLabelText('Watched pages list')
  expect(await within(list).findByRole('link', { name: 'Kettle' })).toHaveAttribute('href', 'https://shop.example/kettle')
  expect(within(list).getByText('Notifies at or under 50 · last seen at 55')).toBeInTheDocument()
  expect(within(list).getByRole('link', { name: 'https://shop.example/hours' })).toBeInTheDocument()
  expect(within(list).getByText('Notifies when the page changes')).toBeInTheDocument()
  expect(within(list).getByRole('heading', { name: 'Watched now (2)' })).toBeInTheDocument()
  fireEvent.click(within(list).getByRole('button', { name: 'Refresh' }))
  await waitFor(() => expect(posts.filter(([action]) => action === 'watch-list')).toHaveLength(2))
})

it('keeps unsaved edits while the watched pages load', async () => {
  fakeService(withApps({ watch: { ...APPS.watch, enabled: true } }, { watch: true }))
  const card = await screen.findByRole('listitem', { name: 'Watched pages' })
  fireEvent.change(within(card).getByLabelText('Adding and removing watched pages'), { target: { value: 'allow' } })
  fireEvent.click(within(card).getByRole('button', { name: 'Refresh' }))
  await within(card).findByRole('link', { name: 'Kettle' })
  expect(within(card).getByLabelText('Adding and removing watched pages')).toHaveValue('allow')
  expect(screen.getByRole('region', { name: 'Unsaved changes' })).toBeInTheDocument()
})

it('links only to web addresses in the watched pages list', async () => {
  const watches = { total: 2, watches: [
    { id: 'w1', title: 'Odd', url: 'javascript:alert(1)', lastChanged: 0, below: null, lastPrice: null },
    { id: 'w2', title: 'Shop', url: 'HTTPS://shop.example/', lastChanged: 0, below: null, lastPrice: null },
  ] }
  fakeService(withApps({ watch: { ...APPS.watch, enabled: true } }, { watch: true }), { watches })
  const list = await screen.findByLabelText('Watched pages list')
  expect(await within(list).findByRole('link', { name: 'Shop' })).toHaveAttribute('href', 'HTTPS://shop.example/')
  expect(within(list).queryByRole('link', { name: 'Odd' })).toBeNull()
  expect(within(list).getByText('Odd')).toBeInTheDocument()
})

it('says when no page is watched yet', async () => {
  fakeService(withApps({ watch: { ...APPS.watch, enabled: true } }, { watch: true }), { watches: { total: 0, watches: [] } })
  expect(await screen.findByText(/No pages are watched yet/)).toBeInTheDocument()
})

it('says how many watched pages are not shown', async () => {
  fakeService(withApps({ watch: { ...APPS.watch, enabled: true } }, { watch: true }), { watches: { ...WATCHES, total: 140 } })
  expect(await screen.findByText('Showing 2 of 140. See them all in changedetection.io.')).toBeInTheDocument()
})

it('shows why the watched pages could not be read', async () => {
  fakeService(withApps({ watch: { ...APPS.watch, enabled: true } }, { watch: true }),
    { watches: new Error('changedetection.io at http://127.0.0.1:11039 could not be reached.') })
  const card = await screen.findByRole('listitem', { name: 'Watched pages' })
  expect(await within(card).findByRole('alert')).toHaveTextContent('could not be reached')
})

it('asks for a saved key before listing watched pages', async () => {
  const { posts } = fakeService(withApps({ watch: { ...APPS.watch, enabled: true } }, {}))
  const card = await screen.findByRole('listitem', { name: 'Watched pages' })
  expect(within(card).getByText(/Turn this on with its API key and save/)).toBeInTheDocument()
  expect(posts.some(([action]) => action === 'watch-list')).toBe(false)
})

it('sends a digest now only with news and notifications on', async () => {
  const news = { ...APPS.news, enabled: true, username: 'owner' }
  const { posts } = fakeService(withApps({ news }, { news: true }, true))
  const card = await screen.findByRole('listitem', { name: 'News' })
  fireEvent.click(within(card).getByRole('button', { name: 'Send a digest now' }))
  expect(await within(card).findByRole('status')).toHaveTextContent('Digest sent with 2 stories.')
  expect(posts.map(([action]) => action)).toEqual(['send-digest'])
  // With unsaved edits, they are saved first so the digest uses them.
  fireEvent.change(within(card).getByLabelText('FreshRSS user'), { target: { value: 'me' } })
  fireEvent.click(within(card).getByRole('button', { name: 'Send a digest now' }))
  await waitFor(() => expect(posts.map(([action]) => action)).toEqual(['send-digest', 'save', 'send-digest']))
  expect(posts[1][1].settings.apps.news.username).toBe('me')
})

it('explains why a digest cannot be sent while notifications are off', async () => {
  fakeService(withApps({ news: { ...APPS.news, enabled: true, username: 'owner' } }, { news: true }, false))
  const card = await screen.findByRole('listitem', { name: 'News' })
  expect(within(card).getByRole('button', { name: 'Send a digest now' })).toBeDisabled()
  expect(within(card).getByText(/Turn on notifications through ntfy above/)).toBeInTheDocument()
})

it('drafts and saves every app field, and sends only typed app secrets', () => {
  expect(secretsFrom({ ntfyPassword: '', mcp: {}, mcpEnv: {}, apps: { documents: 'tok', photos: '' } })).toEqual({ apps: { documents: 'tok' } })
})
