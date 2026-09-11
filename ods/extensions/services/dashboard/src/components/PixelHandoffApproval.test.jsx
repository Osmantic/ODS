import { fireEvent, screen, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import { render } from '../test/test-utils'
import PixelHandoffApproval from './PixelHandoffApproval.jsx'

const id = 'chatcmpl_5c292c25-9368-4d9a-83cd-1e14d34cb128'
const key = 'ods.pixel.handoff.run.v1'
const response = value => ({ ok: true, json: async () => value })
function preview(status = 'pending', kind = 'local') {
  const recipient = { id: 'stronger', label: 'Stronger', kind, baseUrl: 'https://tower.example/v1', model: 'glm', revision: 3, scope: 'run', previousProviderId: 'leader' }
  return { runId: id, checkpointDigest: 'a'.repeat(64), expiresAt: 1900000000, recipient, status,
    checkpointJson: JSON.stringify({ schemaVersion: 1, runId: id, sessionId: 'session', agentId: 'pixel', workspaceDir: '/tmp/workspace', recipient,
      dataScope: 'conversation-and-this-run-tool-results', returnAction: 'configured-leader-on-next-run', prompt: 'Continue', systemPrompt: 'System',
      messages: [{ role: 'user', content: '<img src=x onerror=alert(1)> private checkpoint' }] }) }
}

async function setup({ kind = 'local', decide, status } = {}) {
  localStorage.setItem(key, id)
  const fetchMock = vi.fn(async (url, options) => {
    if (url.endsWith('/list')) return response({ items: [], unavailableCount: 0 })
    if (url.endsWith('/status')) return response(status ? status() : preview('pending', kind))
    if (decide) return decide(url, options)
    return response(preview(JSON.parse(options.body).approved ? 'approved' : 'declined', kind))
  })
  vi.stubGlobal('fetch', fetchMock)
  const view = render(createElement(PixelHandoffApproval))
  fireEvent.click(screen.getByRole('button', { name: 'Review handoffs' }))
  await screen.findByLabelText('Complete checkpoint preview')
  return { fetchMock, ...view }
}

beforeEach(() => localStorage.clear())
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })

it('reload only recovers opaque ID and never automatically approves or publishes', async () => {
  const { fetchMock } = await setup()
  expect(fetchMock.mock.calls.every(([url]) => /\/(list|status)$/.test(url))).toBe(true)
  expect(screen.getByRole('button', { name: 'Approve this run' })).toBeDisabled()
  expect(JSON.stringify(localStorage)).not.toContain('checkpoint')
  expect(localStorage.getItem(key)).toBe(id)
  expect(document.querySelector('img')).toBeNull()
  expect(screen.getByLabelText('Complete checkpoint preview')).toHaveValue(preview().checkpointJson)
})

it('binds explicit approval to exact run/digest without sending transcript or keys', async () => {
  const { fetchMock } = await setup()
  fireEvent.click(screen.getByLabelText('I reviewed this recipient and checkpoint and approve the stated run scope'))
  fireEvent.click(screen.getByRole('button', { name: 'Approve this run' }))
  await screen.findByText(/Handoff: approved/)
  const calls = fetchMock.mock.calls.filter(([url]) => url.endsWith('/decide'))
  expect(calls).toHaveLength(1)
  expect(JSON.parse(calls[0][1].body)).toEqual({ runId: id, checkpointDigest: 'a'.repeat(64), approved: true, allowCloud: false, acceptUnknownCost: false })
})

it('requires both cloud consents in addition to reviewed scope', async () => {
  await setup({ kind: 'cloud' })
  fireEvent.click(screen.getByLabelText('I reviewed this recipient and checkpoint and approve the stated run scope'))
  expect(screen.getByRole('button', { name: 'Approve this run' })).toBeDisabled()
  fireEvent.click(screen.getByLabelText("Allow this cloud provider to receive the checkpoint and this run's new tool results"))
  expect(screen.getByRole('button', { name: 'Approve this run' })).toBeDisabled()
  fireEvent.click(screen.getByLabelText('Accept unknown provider cost for this bounded run'))
  expect(screen.getByRole('button', { name: 'Approve this run' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: 'Reload handoff' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Approve this run' })).toBeDisabled())
})

it('declines without transfer consent and never retries ambiguous decisions', async () => {
  const { fetchMock } = await setup({ decide: async () => { throw new Error('Lost response') } })
  fireEvent.click(screen.getByRole('button', { name: 'Decline handoff' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('button', { name: 'Decline handoff' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Reload handoff' }))
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/decide'))).toHaveLength(1)
})

it('changed checkpoint invalidates previously checked consent', async () => {
  let current = preview()
  await setup({ status: () => current })
  fireEvent.click(screen.getByLabelText('I reviewed this recipient and checkpoint and approve the stated run scope'))
  current = { ...current, checkpointDigest: 'b'.repeat(64) }
  fireEvent.click(screen.getByRole('button', { name: 'Reload handoff' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Approve this run' })).toBeDisabled())
})

it('closing via Escape returns focus without deciding or modifying chat', async () => {
  const { fetchMock } = await setup()
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('button', { name: 'Review handoffs' })).toHaveFocus()
  expect(fetchMock.mock.calls.every(([url]) => /\/(list|status)$/.test(url))).toBe(true)
})

it('terminal state cannot be approved on browser reload', async () => {
  await setup({ status: () => preview('interrupted') })
  expect(screen.getByRole('button', { name: 'Approve this run' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Decline handoff' })).toBeDisabled()
})
