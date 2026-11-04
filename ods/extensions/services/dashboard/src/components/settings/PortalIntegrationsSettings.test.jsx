import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { createElement } from 'react'
import { render } from '../../test/test-utils'
import PortalIntegrationsSettings from './PortalIntegrationsSettings'
import { draftFrom, newServerId, rebaseDraft, savedTools, secretsFrom, typedForDraft } from './portalIntegrationsForm'

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
      current = { ...current, settings: { ...settings(body.expectedRevision + 1, servers), notifications: { ...body.settings.notifications, hasPassword: Boolean(body.secrets.ntfyPassword) }, notes: body.settings.notes } }
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
  render(createElement(PortalIntegrationsSettings))
  // Another window saving: the settings change and their revision goes up.
  const saveElsewhere = change => { current = { ...current, settings: change({ ...current.settings, revision: current.settings.revision + 1 }) } }
  return { posts, fetchMock, saveElsewhere, saved: () => current.settings }
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

it('never puts back a change saved in another window, even after the page refreshes', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    const start = settings(1)
    const { posts, saveElsewhere, saved } = fakeService(view({ settings: { ...start, notes: { ...start.notes, write: 'allow' } } }))
    await screen.findByText('Notes (Obsidian vault)')
    // A edits the ntfy user; B turns adding to notes off and saves.
    fireEvent.change(screen.getByLabelText('User (optional)'), { target: { value: 'a-user' } })
    saveElsewhere(current => ({ ...current, notes: { ...current.notes, write: 'off' } }))
    // A's page refreshes in the background: the edit stays, and the page says what happened.
    await vi.advanceTimersByTimeAsync(15000)
    expect(await screen.findByText(/saved in another window/)).toBeInTheDocument()
    expect(screen.getByLabelText('User (optional)')).toHaveValue('a-user')
    // A saves: refused, because A's edits were made on the older settings.
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('These settings changed in another window.')
    expect(posts.at(-1)[1].expectedRevision).toBe(1)
    expect(saved().notes.write).toBe('off')
    // Reload keeps A's edit on top of B's settings; saving then keeps both.
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }))
    expect(await screen.findByText(/Your unsaved changes are kept/)).toBeInTheDocument()
    expect(screen.getByLabelText('User (optional)')).toHaveValue('a-user')
    expect(screen.getByLabelText('Adding to notes')).toHaveValue('off')
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await screen.findByText('Saved.')
    expect(posts.at(-1)[1]).toMatchObject({ expectedRevision: 2, settings: { notifications: { username: 'a-user' }, notes: { write: 'off' } } })
    expect(saved().notes.write).toBe('off')
  } finally {
    vi.useRealTimers()
  }
})

it('a refresh without unsaved edits simply shows the newer settings', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    const { saveElsewhere } = fakeService()
    await screen.findByText('Notes (Obsidian vault)')
    saveElsewhere(current => ({ ...current, notes: { ...current.notes, write: 'off' } }))
    await vi.advanceTimersByTimeAsync(15000)
    await waitFor(() => expect(screen.getByLabelText('Adding to notes')).toHaveValue('off'))
    expect(screen.queryByText(/saved in another window/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  } finally {
    vi.useRealTimers()
  }
})

it('moves only the owner\'s own changes onto newer settings, server by server and tool by tool', () => {
  const tool = (name, policy) => ({ ...TOOLS[0], name, policy })
  const server = (id, tools, extra = {}) => ({ id, name: id, kind: 'http', preset: '', enabled: true, url: `http://${id}/mcp`, command: '',
    workingFolder: '', envNames: [], tools: tools.map(([name, policy]) => tool(name, policy)), discoveredAt: 1, ...extra })
  const base = draftFrom(settings(1, [server('ha', [['Read', 'allow'], ['TurnOn', 'allow']]), server('old', [])]))
  // Here: TurnOn asks first, "old" removed, "mine" added, ntfy user changed.
  const draft = { ...base, notifications: { ...base.notifications, username: 'me' },
    servers: [{ ...base.servers[0], policies: { Read: 'allow', TurnOn: 'ask' } }, draftFrom(settings(0, [server('mine', [])])).servers[0]] }
  // Elsewhere: Read turned off, ntfy topic changed, "theirs" added.
  const latestSettings = settings(2, [server('ha', [['Read', 'off'], ['TurnOn', 'allow']]), server('old', []), server('theirs', [])])
  const latest = draftFrom({ ...latestSettings, notifications: { ...latestSettings.notifications, topic: 'family' } })
  const merged = rebaseDraft(draft, base, latest)
  expect(merged.notifications).toMatchObject({ username: 'me', topic: 'family' })
  expect(merged.servers.map(item => item.id)).toEqual(['ha', 'theirs', 'mine'])
  expect(merged.servers[0].policies).toEqual({ Read: 'off', TurnOn: 'ask' })
  expect(rebaseDraft(base, base, latest)).toEqual(latest)
})

it('drops a typed credential if its address changed in another window and keeps unrelated edits', async () => {
  const server = { id: 'old', name: 'Old', kind: 'http', preset: '', url: 'https://old.example/mcp',
    command: '', workingFolder: '', envNames: [], enabled: false, tools: [] }
  const { posts, saveElsewhere } = fakeService(view({ settings: settings(1, [server]) }))
  await screen.findByText('Notes (Obsidian vault)')
  fireEvent.change(screen.getByLabelText('Password or access token'), { target: { value: 'for-old-host' } })
  fireEvent.change(screen.getByLabelText('Vault folder (full path)'), { target: { value: '/qa/notes' } })
  saveElsewhere(current => ({ ...current, notifications: { ...current.notifications, url: 'https://new.example' }, mcp: { servers: [] } }))
  fireEvent.click(screen.getByRole('button', { name: 'Reload' }))
  await waitFor(() => expect(screen.getByLabelText('ntfy address')).toHaveValue('https://new.example'))
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await screen.findByText('Saved.')
  expect(posts.at(-1)[1]).toMatchObject({ expectedRevision: 2, settings: { notes: { vaultPath: '/qa/notes' }, mcp: { servers: [] } } })
  expect(posts.at(-1)[1].secrets).toEqual({})
})

it('forgets a removed server token when a new server reuses the name', async () => {
  fakeService()
  const add = await screen.findByRole('button', { name: 'Add Home Assistant' })
  fireEvent.click(add)
  fireEvent.change(screen.getByLabelText('Long-lived access token'), { target: { value: 'for-removed-server' } })
  fireEvent.click(screen.getByRole('button', { name: 'Remove Home Assistant' }))
  fireEvent.click(add)
  expect(screen.getByLabelText('Long-lived access token')).toHaveValue('')
})
