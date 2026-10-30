import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { createElement } from 'react'
import { render } from '../../test/test-utils'
import PortalIntegrationsSettings from './PortalIntegrationsSettings'
import { acknowledgeSecrets, draftFrom, newServerId, rebaseDraft, savedTools, secretsFrom, typedForDraft } from './portalIntegrationsForm'

const TOOLS = [
  { name: 'GetLiveContext', title: '', description: 'Read device states', readOnly: true, destructive: false, inputSchema: {}, policy: 'allow' },
  { name: 'HassTurnOn', title: '', description: 'Turn a device on', readOnly: false, destructive: false, inputSchema: {}, policy: 'ask' },
]
const settings = (revision = 0, servers = []) => ({
  schemaVersion: 1, revision,
  notifications: { enabled: false, url: 'http://127.0.0.1:8097', topic: 'portal', username: '', notifyApprovals: true, hasPassword: false },
  notes: { enabled: false, vaultPath: '', write: 'ask' },
  mcp: { servers },
})
const view = (overrides = {}) => ({ settings: settings(), approvals: [], activity: [], ...overrides })
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })

// A fake service: save stores the settings, discover adds the tools.
function fakeService(initial = view()) {
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
      current = { ...current, settings: { ...settings(body.expectedRevision + 1, servers), notifications: { ...body.settings.notifications, hasPassword: Boolean(body.secrets.ntfyPassword) || current.settings.notifications.hasPassword }, notes: body.settings.notes } }
    } else if (action === 'discover') {
      const servers = current.settings.mcp.servers.map(server => server.id === body.serverId ? { ...server, tools: TOOLS, discoveredAt: 1 } : server)
      current = { ...current, settings: { ...current.settings, revision: current.settings.revision + 1, mcp: { servers } } }
    } else if (action === 'test') {
      return response({ ...current, notice: 'Echo answered with 1 tools.' })
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
      approvalLink: `${window.location.origin}/settings?section=portal-integrations` },
    notes: { enabled: false, vaultPath: '', write: 'ask' }, mcp: { servers: [] } } }]])
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
    .toEqual({ ntfyPassword: 'new', mcp: {}, mcpEnv: { files: { KEY: 'new-key' } } })
})
