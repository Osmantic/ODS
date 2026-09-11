import { fireEvent, screen, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import { render } from '../test/test-utils'
import PixelProviderScopes from './PixelProviderScopes.jsx'

const response = value => ({ ok: true, json: async () => value })
const taskId = '8d23bf56-9f23-4afd-9cd6-c24e6e2931b8'
const initial = () => ({ schemaVersion: 1, chatId: 'Chat_A', revision: 1, taskId,
  taskSelection: null, conversationSelection: null, defaultSnapshot: null, defaultSelection: null,
  effectiveScope: null, effectiveSelection: null, runtimeStatus: 'preference-only', checkpointApproval: 'required-each-handoff-run' })
async function setup({ kind = 'local', mutate, state = initial(), sending = false } = {}) {
  const config = { enabled: true, revision: 3, roles: { handoff: 'stronger' }, providers: [
    { id: 'stronger', label: 'Stronger', model: 'glm', baseUrl: 'https://tower.example/v1', kind, enabled: true }] }
  const fetchMock = vi.fn(async (url, options) => {
    if (url === '/api/pixel/providers') return response({ configuration: config })
    if (url.endsWith('/status')) return response(state)
    if (mutate) return mutate(url, options)
    return response({ ...state, revision: state.revision + 1 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const view = render(createElement(PixelProviderScopes, { chatId: 'Chat_A', sending }))
  fireEvent.click(screen.getByRole('button', { name: 'Handoff scope' }))
  await screen.findByLabelText('Preference scope')
  return { fetchMock, ...view }
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('only reads on open and reload; never begins or approves work implicitly', async () => {
  const { fetchMock } = await setup()
  expect(fetchMock.mock.calls.every(([url]) => url.endsWith('/status') || url === '/api/pixel/providers')).toBe(true)
  expect(screen.getByRole('button', { name: 'Save handoff preference' })).toBeDisabled()
  expect(screen.getByText(/not an active route/)).toBeInTheDocument()
})

it('saves exact owner task and provider revisions without a checkpoint approval', async () => {
  const { fetchMock } = await setup()
  fireEvent.click(screen.getByLabelText('I reviewed this recipient, duration and return behavior'))
  fireEvent.click(screen.getByRole('button', { name: 'Save handoff preference' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/select'))).toBe(true))
  const body = JSON.parse(fetchMock.mock.calls.find(([url]) => url.endsWith('/select'))[1].body)
  expect(body).toEqual({ chatId: 'Chat_A', taskId, expectedRevision: 1, scope: 'task',
    providerId: 'stronger', providerRevision: 3, allowCloud: false, acceptUnknownCost: false })
})

it('requires a separate explicit begin and keeps task scope disabled without it', async () => {
  const state = { ...initial(), taskId: null }
  const { fetchMock } = await setup({ state, mutate: async (_url, options) => response({ ...state, revision: 2, taskId: JSON.parse(options.body).taskId }) })
  fireEvent.click(screen.getByLabelText('I reviewed this recipient, duration and return behavior'))
  expect(screen.getByRole('button', { name: 'Save handoff preference' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Begin task' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'End task' })).toBeEnabled())
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/begin'))).toHaveLength(1)
})

it('cloud needs review and both consents; changing scope clears them', async () => {
  await setup({ kind: 'cloud' })
  fireEvent.click(screen.getByLabelText('I reviewed this recipient, duration and return behavior'))
  fireEvent.click(screen.getByLabelText(/Allow cloud conversation/))
  expect(screen.getByRole('button', { name: 'Save handoff preference' })).toBeDisabled()
  fireEvent.click(screen.getByLabelText(/Accept unknown provider cost/))
  expect(screen.getByRole('button', { name: 'Save handoff preference' })).toBeEnabled()
  fireEvent.change(screen.getByLabelText('Preference scope'), { target: { value: 'default' } })
  expect(screen.getByRole('button', { name: 'Save handoff preference' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Reset new-task default' })).toBeInTheDocument()
})

it('ambiguous mutation is not replayed and requires explicit reload', async () => {
  const { fetchMock } = await setup({ mutate: async () => { throw new Error('lost reply') } })
  fireEvent.click(screen.getByRole('button', { name: 'Return from selected scope' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('button', { name: 'Return from selected scope' })).toBeDisabled()
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/return'))).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: 'Reload preferences' }))
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  expect(screen.getByLabelText('I reviewed this recipient, duration and return behavior')).not.toBeChecked()
})

it('prevents UI changes while a run is active and closes on chat identity change', async () => {
  const { rerender } = await setup({ sending: true })
  expect(screen.getByRole('button', { name: 'End task' })).toBeDisabled()
  rerender(createElement(PixelProviderScopes, { chatId: 'Chat_B', sending: false }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
})
