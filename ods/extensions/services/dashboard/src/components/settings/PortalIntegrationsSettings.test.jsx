import { act, fireEvent, render as renderBase, screen, waitFor, within } from '@testing-library/react'
import { createElement } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { ThemeProvider } from '../../contexts/ThemeContext'
import PortalIntegrationsSettings from './PortalIntegrationsSettings'
import { acknowledgeSecrets, draftFrom, newServerId, rebaseDraft, savedTools, secretsFrom, typedForDraft } from './portalIntegrationsForm'

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
const routers = []
function render(ui) {
  const router = createMemoryRouter([{ path: '*', element: createElement(ThemeProvider, null, ui) }],
    { initialEntries: ['/settings?section=portal-integrations'] })
  routers.push(router)
  return renderBase(createElement(RouterProvider, { router }))
}

const WATCHES = { total: 2, watches: [
  { id: 'w1', title: 'Kettle', url: 'https://shop.example/kettle', lastChanged: 5, below: 50, lastPrice: 55 },
  { id: 'w2', title: '', url: 'https://shop.example/hours', lastChanged: 0, below: null, lastPrice: null },
] }

// A fake service: save stores the settings, discover adds the tools.
function fakeService(initial = view(), { watches = WATCHES, approvalDetails = {} } = {}) {
  let current = initial
  const posts = []
  const failures = new Map()
  const fetchMock = vi.fn(async (url, options) => {
    if (options.method !== 'POST') return response(current)
    const action = url.split('/').pop()
    const body = JSON.parse(options.body)
    posts.push([action, body])
    if (failures.has(action)) { const message = failures.get(action); failures.delete(action); return response({ detail: message }, 400) }
    if (action === 'save') {
      if (body.expectedRevision !== current.settings.revision) return response({ detail: 'These settings changed in another window.', code: 'stale-revision' }, 409)
      const servers = body.settings.mcp.servers.map(server => ({ ...server, hasToken: Boolean(body.secrets.mcp?.[server.id]) || current.settings.mcp.servers.some(old => old.id === server.id && old.hasToken),
        hasEnv: Object.fromEntries(server.envNames.map(name => [name, Boolean(body.secrets.mcpEnv?.[server.id]?.[name]) || current.settings.mcp.servers.some(old => old.id === server.id && old.hasEnv?.[name])])),
        tools: current.settings.mcp.servers.find(item => item.id === server.id)?.tools || [], discoveredAt: current.settings.mcp.servers.find(item => item.id === server.id)?.discoveredAt || null }))
      const keys = Object.fromEntries(Object.entries(current.settings.apps).map(([id, app]) => [id, app.hasKey || Boolean(body.secrets.apps?.[id])]))
      current = { ...current, settings: { ...settings(body.expectedRevision + 1, servers, savedApps(body.settings.apps, keys)),
        notifications: { ...body.settings.notifications, hasPassword: Boolean(body.secrets.ntfyPassword) || current.settings.notifications.hasPassword }, notes: body.settings.notes } }
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
    } else if (action === 'retry-digest') {
      if (body.expectedRevision !== current.settings.revision) return response({ detail: 'These settings changed in another window.', code: 'stale-revision' }, 409)
      current = { ...current, digestRecovery: [] }
      return response({ ...current, notice: 'The saved digest was sent again.' })
    } else if (action === 'approval-detail') {
      const item = current.approvals.find(entry => entry.id === body.approvalId)
      if (!item) return response({detail: 'This request is no longer waiting for approval.'}, 400)
      return response({approval: {...item, available: Boolean(approvalDetails[item.id]), ...approvalDetails[item.id]}})
    } else if (action === 'clear-activity') {
      current = { ...current, activity: [] }
    } else if (action === 'decide') {
      current = { ...current, approvals: [] }
    }
    return response(current)
  })
  vi.stubGlobal('fetch', fetchMock)
  const rendered = render(createElement(PortalIntegrationsSettings))
  return { posts, fetchMock, unmount: rendered.unmount, failNext: (action, message) => failures.set(action, message) }
}

afterEach(() => { for (const router of routers.splice(0)) router.dispose(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

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

it('keeps scheduled-task notices off for older settings until the owner chooses otherwise', async () => {
  const initial = view()
  delete initial.settings.notifications.scheduledTasks
  initial.settings.notifications.enabled = true
  const { posts } = fakeService(initial)
  await screen.findByText('Notifications (ntfy)')
  expect(screen.getByLabelText('Notify me when a scheduled task')).toHaveValue('off')
  expect(posts).toHaveLength(0)
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Adding to notes'), { target: { value: 'off' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await screen.findByText('Saved.')
  expect(posts[0][1].settings.notifications.scheduledTasks).toBe('off')
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
  expect(within(tools).getByText(/Server reports: read-only/)).toBeInTheDocument()
  expect(screen.getByText(/Behavior labels are supplied by this server, not enforced by Portal/)).toBeInTheDocument()
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
  const { posts } = fakeService(view({ approvals: [approval], settings: settings(3), activity }), {
    approvalDetails: {a1: {request: {action: 'mcp-call', server: 'home-assistant', tool: 'HassTurnOn', arguments: {name: 'Kitchen'}}, redacted: false}},
  })
  expect(await screen.findByText(/Searched notes: deposit \(1 notes\)/)).toBeInTheDocument()
  expect(screen.queryByRole('button', {name: 'Approve'})).toBeNull()
  fireEvent.click(await screen.findByRole('button', {name: 'Review request'}))
  expect(await screen.findByText('Exact arguments')).toBeInTheDocument()
  fireEvent.click(await screen.findByRole('button', { name: 'Approve' }))
  await screen.findByText('Nothing is waiting.')
  expect(posts).toEqual([['approval-detail', {approvalId: 'a1'}], ['decide', { approvalId: 'a1', decision: 'approve' }]])

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

it('loads only the selected proposal and shows all exact note text before offering approval', async () => {
  const approval = {id: 'note-1', kind: 'note', summary: 'Add to Owned.md', createdAt: 1, expiresAt: 2}
  const text = 'Exact first line.\n' + Array.from({length: 20}, (_, i) => `${i + 1}. Unexpected additional text`).join('\n') + '\n<h1>Literal text</h1>'
  const {posts} = fakeService(view({approvals: [approval]}), {approvalDetails: {
    'note-1': {request: {action: 'notes-write', path: 'Owned.md', mode: 'append', text}, redacted: false},
  }})
  await screen.findByText('Add to Owned.md')
  expect(posts).toEqual([])
  expect(screen.queryByText('Exact text')).toBeNull()
  expect(screen.queryByRole('button', {name: 'Approve'})).toBeNull()
  fireEvent.click(screen.getByRole('button', {name: 'Review request'}))
  await screen.findByText('Exact text')
  const proposal = screen.getByLabelText('Proposed action')
  expect(proposal.querySelector('pre').textContent).toBe(text)
  expect(within(proposal).queryByRole('heading')).toBeNull()
  expect(screen.getByRole('button', {name: 'Approve'})).toBeEnabled()
  expect(posts).toEqual([['approval-detail', {approvalId: 'note-1'}]])
  fireEvent.click(screen.getByRole('button', {name: 'Decline'}))
  await screen.findByText('Nothing is waiting.')
  expect(posts.at(-1)).toEqual(['decide', {approvalId: 'note-1', decision: 'decline'}])
})

it.each([
  {available: false},
  {available: true, redacted: true, request: {action: 'mcp-call', server: 'owned', tool: 'change', arguments: {value: '[redacted]'}}},
])('keeps decline available when the exact proposal cannot be reviewed: %j', async detail => {
  const approval = {id: 'old-1', kind: 'mcp', summary: 'Old request', createdAt: 1, expiresAt: 2}
  const {posts} = fakeService(view({approvals: [approval]}), {approvalDetails: {'old-1': detail}})
  fireEvent.click(await screen.findByRole('button', {name: 'Review request'}))
  await waitFor(() => expect(screen.queryByRole('button', {name: 'Loading request…'})).toBeNull())
  expect(screen.queryByRole('button', {name: 'Approve'})).toBeNull()
  fireEvent.click(screen.getByRole('button', {name: 'Decline'}))
  await screen.findByText('Nothing is waiting.')
  expect(posts).toEqual([['approval-detail', {approvalId: 'old-1'}], ['decide', {approvalId: 'old-1', decision: 'decline'}]])
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

it('acknowledges a committed save when discovery fails and does not resend the saved token', async () => {
  const { posts, failNext } = fakeService()
  fireEvent.click(await screen.findByRole('button', { name: 'Add Home Assistant' }))
  fireEvent.change(screen.getByLabelText('Long-lived access token'), { target: { value: 'invented-token' } })
  failNext('discover', 'The invented MCP server is unavailable.')
  fireEvent.click(screen.getByRole('button', { name: 'Check tools' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('The invented MCP server is unavailable.')
  expect(screen.getByLabelText('Long-lived access token')).toHaveValue('')
  expect(screen.getByLabelText('Long-lived access token')).toHaveAttribute('placeholder', 'Saved. Type to replace.')
  expect(screen.queryByRole('region', { name: 'Unsaved changes' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Check tools' }))
  await screen.findByText(/Checked the tools of Home Assistant/)
  expect(posts.map(([action]) => action)).toEqual(['save', 'discover', 'discover'])
  fireEvent.change(screen.getByLabelText('Adding to notes'), { target: { value: 'off' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await screen.findByText('Saved.')
  expect(posts.at(-1)[1].expectedRevision).toBe(2)
  expect(posts.at(-1)[1].secrets).toEqual({})
})

it('acknowledges a committed save before a notification test fails', async () => {
  const { posts, failNext } = fakeService()
  fireEvent.click(await screen.findByLabelText('Send notifications through ntfy'))
  fireEvent.change(screen.getByLabelText('Password or access token'), { target: { value: 'invented-password' } })
  failNext('test-notification', 'The notification destination did not answer.')
  fireEvent.click(screen.getByRole('button', { name: 'Send test notification' }))
  await screen.findByRole('alert')
  expect(screen.getByLabelText('Password or access token')).toHaveValue('')
  expect(screen.queryByRole('region', { name: 'Unsaved changes' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Send test notification' }))
  await screen.findByText('Test notification sent.')
  expect(posts.map(([action]) => action)).toEqual(['save', 'test-notification', 'test-notification'])
})

const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

it('ignores a poll that finishes after Save and allows only one poll in flight', async () => {
  const intervals = vi.spyOn(globalThis, 'setInterval')
  const { posts, fetchMock } = fakeService()
  await screen.findByText('Notes (Obsidian vault)')
  const poll = intervals.mock.calls.find(([, delay]) => delay === 15000)[0]
  const read = deferred(), original = fetchMock.getMockImplementation()
  let reads = 0
  fetchMock.mockImplementation((url, options) => options.method === 'GET' ? (reads++, read.promise) : original(url, options))
  let pending
  act(() => { pending = poll() })
  await act(async () => { await poll() })
  expect(reads).toBe(1)
  fireEvent.change(screen.getByLabelText('Adding to notes'), { target: { value: 'allow' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await screen.findByText('Saved.')
  await act(async () => { read.resolve(response(view())); await pending })
  expect(screen.getByLabelText('Adding to notes')).toHaveValue('allow')
  expect(screen.queryByRole('region', { name: 'Unsaved changes' })).toBeNull()
  fireEvent.change(screen.getByLabelText('Adding to notes'), { target: { value: 'off' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await waitFor(() => expect(posts).toHaveLength(2))
  expect(posts[1][1].expectedRevision).toBe(1)
})

it('preserves edits and newly typed secrets while a background read returns newer settings', async () => {
  const intervals = vi.spyOn(globalThis, 'setInterval')
  const { fetchMock } = fakeService()
  await screen.findByText('Notes (Obsidian vault)')
  const poll = intervals.mock.calls.find(([, delay]) => delay === 15000)[0]
  const read = deferred()
  fetchMock.mockImplementation(() => read.promise)
  let pending
  act(() => { pending = poll() })
  fireEvent.change(screen.getByLabelText('Adding to notes'), { target: { value: 'allow' } })
  fireEvent.change(screen.getByLabelText('Password or access token'), { target: { value: 'new-unsaved-password' } })
  const received = settings(3); received.notifications.username = 'Changed in another window'
  await act(async () => { read.resolve(response(view({ settings: received }))); await pending })
  expect(screen.getByLabelText('Adding to notes')).toHaveValue('allow')
  expect(screen.getByLabelText('Password or access token')).toHaveValue('new-unsaved-password')
  expect(screen.getByLabelText('User (optional)')).toHaveValue('Changed in another window')
  expect(screen.getByRole('region', { name: 'Unsaved changes' })).toBeInTheDocument()
})

it('does not adopt an older revision from a later read and aborts reads when unmounted', async () => {
  const { posts, fetchMock, unmount } = fakeService(view({ settings: settings(4) }))
  await screen.findByText('Notes (Obsidian vault)')
  const original = fetchMock.getMockImplementation()
  fetchMock.mockImplementation((url, options) => options.method === 'GET' ? response(view()) : original(url, options))
  fireEvent.click(screen.getByRole('button', { name: 'Reload' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Reload' })).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Adding to notes'), { target: { value: 'off' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await screen.findByText('Saved.')
  expect(posts[0][1].expectedRevision).toBe(4)
  const read = deferred()
  fetchMock.mockImplementation(() => read.promise)
  fireEvent.click(screen.getByRole('button', { name: 'Reload' }))
  const signal = fetchMock.mock.calls.at(-1)[1].signal
  unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => { read.resolve(response(view())) })
})

it('invalidates shown tools when launch identity changes without treating environment ordering as a new launch', () => {
  const saved = { kind: 'stdio', url: '', command: 'fixture', workingFolder: '/old', envNames: ['A', 'B'], discoveredAt: 1, tools: TOOLS }
  expect(savedTools(saved, { ...saved, workingFolder: '/new' })).toBeNull()
  expect(savedTools(saved, { ...saved, envNames: ['A', 'C'] })).toBeNull()
  expect(savedTools(saved, { ...saved, envNames: ['B', 'A'] })).toBe(TOOLS)
})

it('turns a changed launch off in the draft so it can be saved and checked before re-enabling', async () => {
  const server = { id: 'echo', name: 'Echo', kind: 'stdio', preset: '', enabled: true, url: '', command: 'fixture',
    workingFolder: '/old', envNames: [], tools: TOOLS, discoveredAt: 1, hasEnv: {}, hasToken: false }
  const { posts } = fakeService(view({ settings: settings(2, [server]) }))
  const card = await screen.findByRole('listitem', { name: 'MCP server Echo' })
  fireEvent.change(within(card).getByLabelText('Working folder (optional, full path)'), { target: { value: '/new' } })
  expect(within(card).getByLabelText('Let Portal use this server')).not.toBeChecked()
  expect(within(card).getByLabelText('Let Portal use this server')).toBeDisabled()
  expect(within(card).queryByRole('list', { name: 'Tools of Echo' })).toBeNull()
  fireEvent.click(within(card).getByRole('button', { name: 'Check tools' }))
  await screen.findByText(/Checked the tools of Echo/)
  expect(posts[0][1].settings.mcp.servers[0]).toMatchObject({ workingFolder: '/new', enabled: false })
  expect(within(card).getByLabelText('Let Portal use this server')).not.toBeChecked()
  expect(within(card).getByLabelText('Let Portal use this server')).toBeEnabled()
})

for (const kind of ['http', 'stdio']) it(`turns a server off before saving a replacement ${kind === 'http' ? 'token' : 'secret environment value'}`, async () => {
  const server = { id: 'fixture', name: 'Fixture', kind, preset: '', enabled: true,
    url: kind === 'http' ? 'http://fixture.invalid/mcp' : '', command: kind === 'stdio' ? 'fixture' : '',
    workingFolder: '', envNames: kind === 'stdio' ? ['KEY'] : [], hasEnv: { KEY: true }, hasToken: true, tools: TOOLS, discoveredAt: 1 }
  const { posts } = fakeService(view({ settings: settings(3, [server]) }))
  const card = await screen.findByRole('listitem', { name: 'MCP server Fixture' })
  fireEvent.change(within(card).getByLabelText(kind === 'http' ? 'Access token (optional)' : 'KEY'), { target: { value: 'replacement-secret' } })
  expect(within(card).getByLabelText('Let Portal use this server')).not.toBeChecked()
  expect(within(card).queryByRole('list', { name: 'Tools of Fixture' })).toBeNull()
  fireEvent.click(within(card).getByRole('button', { name: 'Check tools' }))
  await screen.findByText(/Checked the tools of Fixture/)
  expect(posts[0][1].settings.mcp.servers[0].enabled).toBe(false)
  expect(posts[0][1].secrets).toEqual(kind === 'http'
    ? { mcp: { fixture: 'replacement-secret' } } : { mcpEnv: { fixture: { KEY: 'replacement-secret' } } })
  expect(within(card).getByLabelText('Let Portal use this server')).not.toBeChecked()
  expect(within(card).getByLabelText('Let Portal use this server')).toBeEnabled()
})

it('rebases newer edits and clears only the exact acknowledged secrets across a first-save server rename', () => {
  const initial = draftFrom(settings(0)), base = { ...initial, servers: [{ id: 'temporary', name: 'Files', policies: {}, envNames: ['KEY'] }] }
  const current = { ...base, notes: { ...base.notes, write: 'allow' }, servers: [{ ...base.servers[0], name: 'New name' }] }
  const saved = { ...base, servers: [{ ...base.servers[0], id: 'files', policies: { read: 'off' } }] }
  const rebased = rebaseDraft(base, current, saved, { temporary: 'files' })
  expect(rebased.notes.write).toBe('allow')
  expect(rebased.servers[0]).toMatchObject({ id: 'files', name: 'New name', policies: { read: 'off' } })
  const typed = { ntfyPassword: 'new', mcp: { temporary: 'sent' }, mcpEnv: { temporary: { KEY: 'new-key' } } }
  expect(acknowledgeSecrets(typed, { ntfyPassword: 'old', mcp: { files: 'sent' }, mcpEnv: { files: { KEY: 'old-key' } } }, { temporary: 'files' }))
    .toEqual({ ntfyPassword: 'new', mcp: {}, mcpEnv: { files: { KEY: 'new-key' } }, apps: {} })
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

it('keeps ordinary custom server ids separate from built-in app: ids', () => {
  expect(newServerId('News', [])).toBe('news')
  expect(newServerId('Photos', [{ id: 'photos-2' }])).toBe('photos')
  expect(newServerId('News', [{ id: 'news' }])).toBe('news-2')
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

it.each([
  [{ error: 'The provider could not extract a price.' }, 'Last check failed: The provider could not extract a price.'],
  [{ paused: true }, 'Paused in changedetection.io.'],
  [{ lastChecked: 0 }, 'Waiting for the first page check.'],
])('shows unavailable watch health instead of implying a successful page check', async (health, message) => {
  const { posts } = fakeService(withApps({ watch: { ...APPS.watch, enabled: true } }, { watch: true }),
    { watches: { total: 1, watches: [{ ...WATCHES.watches[0], ...health }] } })
  const card = await screen.findByRole('listitem', { name: 'Watched pages' })
  expect(await within(card).findByText(message)).toBeInTheDocument()
  expect(posts.map(([action]) => action)).toEqual(['watch-list'])
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

it('shows why a save failed in the bar where Save was pressed', async () => {
  fakeService()
  const card = await screen.findByRole('listitem', { name: 'Photos' })
  vi.stubGlobal('fetch', vi.fn(async (url, options) => options.method === 'POST'
    ? response({ detail: 'Give the Immich key, or turn it off.', code: 'invalid-settings' }, 400)
    : response(view())))
  fireEvent.click(within(card).getByLabelText('Let Portal search my photos'))
  const bar = screen.getByRole('region', { name: 'Unsaved changes' })
  fireEvent.click(within(bar).getByRole('button', { name: 'Save' }))
  expect(await within(bar).findByRole('alert')).toHaveTextContent('Give the Immich key, or turn it off.')
  expect(screen.getAllByRole('alert')).toHaveLength(1)  // Not also at the top, out of sight.
  fireEvent.click(within(bar).getByRole('button', { name: 'Discard' }))
  expect(screen.queryByRole('alert')).toBeNull()
})

it('says the digest waits until News is turned on', async () => {
  fakeService()
  const news = await screen.findByRole('listitem', { name: 'News' })
  fireEvent.click(within(news).getByLabelText('Send me a daily news digest'))
  expect(within(news).getByText(/The digest starts once you turn on/)).toBeInTheDocument()
  fireEvent.click(within(news).getByLabelText('Let Portal read my news feeds'))
  expect(within(news).queryByText(/The digest starts once you turn on/)).toBeNull()
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

it('rebases app fields and nested digest edits independently of newer saved settings', () => {
  const base = draftFrom(settings())
  const current = { ...base, apps: { ...base.apps,
    documents: { ...base.apps.documents, url: 'http://documents-owner-edit.example' },
    news: { ...base.apps.news, digest: { ...base.apps.news.digest, time: '08:45' } },
  } }
  const saved = { ...base, apps: { ...base.apps,
    photos: { ...base.apps.photos, enabled: true },
    news: { ...base.apps.news, digest: { ...base.apps.news.digest, timezone: 'America/New_York' } },
  } }
  const rebased = rebaseDraft(base, current, saved)
  expect(rebased.apps.documents.url).toBe('http://documents-owner-edit.example')
  expect(rebased.apps.photos.enabled).toBe(true)
  expect(rebased.apps.news.digest).toEqual({ enabled: false, time: '08:45', timezone: 'America/New_York' })
  expect(acknowledgeSecrets({ ...NO_TYPED_SECRETS, apps: { documents: 'sent', news: 'new typing' } },
    { apps: { documents: 'sent', news: 'old sent value' } })).toMatchObject({ apps: { news: 'new typing' } })
})

const NO_TYPED_SECRETS = { ntfyPassword: '', mcp: {}, mcpEnv: {} }

it('keeps app edits and newly typed app keys when a poll returns another saved revision', async () => {
  const intervals = vi.spyOn(globalThis, 'setInterval')
  const { fetchMock } = fakeService()
  const documents = await screen.findByRole('listitem', { name: 'Documents' })
  const news = screen.getByRole('listitem', { name: 'News' })
  const poll = intervals.mock.calls.find(([, delay]) => delay === 15000)[0]
  const read = deferred()
  fetchMock.mockImplementation(() => read.promise)
  let pending
  act(() => { pending = poll() })
  fireEvent.change(within(documents).getByLabelText('Paperless-ngx address'), { target: { value: 'http://documents-owner-edit.example' } })
  fireEvent.change(within(documents).getByLabelText('API token'), { target: { value: 'new-unsaved-key' } })
  fireEvent.click(within(news).getByLabelText('Send me a daily news digest'))
  fireEvent.change(within(news).getByLabelText(/Digest time/), { target: { value: '08:45' } })
  const received = settings(3)
  received.apps.photos.url = 'http://photos-other-window.example'
  await act(async () => { read.resolve(response(view({ settings: received }))); await pending })
  expect(within(documents).getByLabelText('Paperless-ngx address')).toHaveValue('http://documents-owner-edit.example')
  expect(within(documents).getByLabelText('API token')).toHaveValue('new-unsaved-key')
  expect(within(news).getByLabelText(/Digest time/)).toHaveValue('08:45')
  expect(within(screen.getByRole('listitem', { name: 'Photos' })).getByLabelText('Immich address')).toHaveValue('http://photos-other-window.example')
  expect(screen.getByRole('region', { name: 'Unsaved changes' })).toBeInTheDocument()
})

it('does not replace a current watch list with a response for an earlier app revision', async () => {
  const { fetchMock } = fakeService(withApps({ watch: { ...APPS.watch, enabled: true } }, { watch: true }))
  const first = deferred(), original = fetchMock.getMockImplementation()
  let watchReads = 0, firstSignal
  fetchMock.mockImplementation((url, options) => {
    if (url.endsWith('/watch-list') && watchReads++ === 0) { firstSignal = options.signal; return first.promise }
    return original(url, options)
  })
  const card = await screen.findByRole('listitem', { name: 'Watched pages' })
  await within(card).findByRole('button', { name: 'Loading…' })
  fireEvent.change(within(card).getByLabelText('changedetection.io address'), { target: { value: 'http://watch-new.example' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await within(card).findByRole('link', { name: 'Kettle' })
  expect(firstSignal.aborted).toBe(true)
  await act(async () => first.resolve(response({ ...view(), watches: { total: 1, watches: [
    { id: 'old', title: 'Obsolete watch', url: 'https://old.example', below: null, lastPrice: null },
  ] } })))
  expect(within(card).queryByRole('link', { name: 'Obsolete watch' })).toBeNull()
  expect(within(card).getByRole('link', { name: 'Kettle' })).toBeInTheDocument()
})

it('aborts an in-flight watch-list read when the Settings view unmounts', async () => {
  const { fetchMock, unmount } = fakeService(withApps({ watch: { ...APPS.watch, enabled: true } }, { watch: true }))
  const read = deferred(), original = fetchMock.getMockImplementation()
  let signal
  fetchMock.mockImplementation((url, options) => {
    if (url.endsWith('/watch-list')) { signal = options.signal; return read.promise }
    return original(url, options)
  })
  await screen.findByRole('button', { name: 'Loading…' })
  expect(signal.aborted).toBe(false)
  unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => read.resolve(response({ ...view(), watches: WATCHES })))
})

const RECOVERY = { attemptId: '0123456789abcdef0123456789abcdef', attemptedAt: 1793640000000, storyCount: 2,
  messagePreview: 'Invented news\nFirst saved story.\nSecond saved story.', canRetry: true,
  notificationDestination: 'http://127.0.0.1:8097/portal' }
const recoveryView = (attempts = [RECOVERY], revision = 4) => {
  const initial = withApps({ news: { ...APPS.news, enabled: true, username: 'fixture' } }, { news: true }, true)
  initial.settings.revision = revision
  return { ...initial, digestRecovery: attempts }
}

it('requires acknowledgement before retrying the exact saved digest and submits a double-click once', async () => {
  const { fetchMock } = fakeService(recoveryView())
  const recovery = await screen.findByRole('region', { name: 'Digest delivery recovery' })
  expect(within(recovery).getByText(/It may already have arrived/)).toBeInTheDocument()
  expect(within(recovery).getByText('Current notification destination: http://127.0.0.1:8097/portal')).toBeInTheDocument()
  expect(within(recovery).getByText(RECOVERY.messagePreview, { normalizer: text => text })).toBeInTheDocument()
  const retry = within(recovery).getByRole('button', { name: 'Retry digest' })
  expect(retry).toBeDisabled()
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/retry-digest'))).toHaveLength(0)
  const pending = deferred(), original = fetchMock.getMockImplementation()
  fetchMock.mockImplementation((url, options) => url.endsWith('/retry-digest') ? pending.promise : original(url, options))
  fireEvent.click(within(recovery).getByLabelText('I understand this may send a duplicate notification'))
  fireEvent.click(retry); fireEvent.click(retry)
  const calls = fetchMock.mock.calls.filter(([url]) => url.endsWith('/retry-digest'))
  expect(calls).toHaveLength(1)
  expect(JSON.parse(calls[0][1].body)).toEqual({ attemptId: RECOVERY.attemptId, expectedRevision: 4 })
  await act(async () => pending.resolve(response({ ...recoveryView([]), notice: 'The saved digest was sent again.' })))
  expect(screen.queryByRole('region', { name: 'Digest delivery recovery' })).toBeNull()
  expect(within(screen.getByRole('listitem', { name: 'News' })).getByRole('status')).toHaveTextContent('The saved digest was sent again.')
})

it('blocks digest recovery with unsaved edits and resets the duplicate acknowledgement after discard', async () => {
  const { posts } = fakeService(recoveryView())
  const recovery = await screen.findByRole('region', { name: 'Digest delivery recovery' })
  fireEvent.click(within(recovery).getByLabelText('I understand this may send a duplicate notification'))
  fireEvent.change(screen.getByLabelText('User (optional)'), { target: { value: 'unsaved change' } })
  expect(within(recovery).getByRole('button', { name: 'Retry digest' })).toBeDisabled()
  expect(within(recovery).getByText('Save or discard your changes before retrying this digest.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
  expect(within(recovery).getByLabelText('I understand this may send a duplicate notification')).not.toBeChecked()
  expect(within(recovery).getByRole('button', { name: 'Retry digest' })).toBeDisabled()
  expect(posts).toHaveLength(0)
})

it('refreshes an uncertain retry without resending, retaining the error and the newly recorded attempt', async () => {
  const { fetchMock } = fakeService(recoveryView())
  const recovery = await screen.findByRole('region', { name: 'Digest delivery recovery' })
  const child = { ...RECOVERY, attemptId: 'fedcba9876543210fedcba9876543210', attemptedAt: RECOVERY.attemptedAt + 1 }
  const refreshed = recoveryView([child])
  fetchMock.mockImplementation((url, options) => options.method === 'POST'
    ? response({ detail: 'Delivery could not be confirmed; check the saved attempt before retrying.', code: 'digest-delivery-unknown' }, 502)
    : response(refreshed))
  fireEvent.click(within(recovery).getByLabelText('I understand this may send a duplicate notification'))
  fireEvent.click(within(recovery).getByRole('button', { name: 'Retry digest' }))
  await waitFor(() => expect(within(recovery).getByLabelText('I understand this may send a duplicate notification')).not.toBeChecked())
  expect(within(recovery).getAllByRole('button', { name: 'Retry digest' })).toHaveLength(1)
  expect(within(screen.getByRole('listitem', { name: 'News' })).getByRole('alert')).toHaveTextContent('Delivery could not be confirmed')
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/retry-digest'))).toHaveLength(1)
  expect(within(recovery).getAllByLabelText('I understand this may send a duplicate notification').every(input => !input.checked)).toBe(true)
  expect(within(recovery).getAllByRole('button', { name: 'Retry digest' }).every(button => button.disabled)).toBe(true)
})

it('reconciles a stale revision refusal and never retries it automatically', async () => {
  const { fetchMock } = fakeService(recoveryView())
  const recovery = await screen.findByRole('region', { name: 'Digest delivery recovery' })
  fetchMock.mockImplementation((url, options) => options.method === 'POST'
    ? response({ detail: 'These settings changed in another window.', code: 'stale-revision' }, 409)
    : response(recoveryView([{ ...RECOVERY, canRetry: false, reason: 'The configured source has changed.' }], 5)))
  fireEvent.click(within(recovery).getByLabelText('I understand this may send a duplicate notification'))
  fireEvent.click(within(recovery).getByRole('button', { name: 'Retry digest' }))
  await within(recovery).findByText('The configured source has changed.')
  expect(within(screen.getByRole('listitem', { name: 'News' })).getByRole('alert')).toHaveTextContent('These settings changed')
  expect(within(recovery).getByRole('button', { name: 'Retry digest' })).toBeDisabled()
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/retry-digest'))).toHaveLength(1)
})
