import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { createElement } from 'react'
import { render } from '../../test/test-utils'
import PortalIntegrationsSettings from './PortalIntegrationsSettings'
import { newServerId, savedTools, secretsFrom } from './portalIntegrationsForm'

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
        tools: current.settings.mcp.servers.find(item => item.id === server.id)?.tools || [], discoveredAt: current.settings.mcp.servers.find(item => item.id === server.id)?.discoveredAt || null }))
      current = { ...current, settings: { ...settings(body.expectedRevision + 1, servers), notifications: { ...body.settings.notifications, hasPassword: Boolean(body.secrets.ntfyPassword) }, notes: body.settings.notes } }
    } else if (action === 'discover') {
      const servers = current.settings.mcp.servers.map(server => server.id === body.serverId ? { ...server, tools: TOOLS, discoveredAt: 1 } : server)
      current = { ...current, settings: { ...current.settings, revision: current.settings.revision + 1, mcp: { servers } } }
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
    notifications: { enabled: true, url: 'http://127.0.0.1:8097', topic: 'portal', username: 'portal', notifyApprovals: true },
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
