import { fireEvent, screen, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import { render } from '../test/test-utils'
import PixelAdvice from './PixelAdvice.jsx'

const id = '5c292c25-9368-4d9a-83cd-1e14d34cb128'
const key = 'ods.pixel.advice.job.v1'
const config = (kind = 'ods-peer') => ({ schemaVersion: 1, revision: 3, enabled: true,
  providers: [{ id: 'advisor', label: 'Tower advisor', kind, baseUrl: 'https://tower.example/v1', model: 'glm',
    contextTokens: 32768, maxOutputTokens: 4096, supportsTools: true, supportsVision: false,
    reasoning: false, hasCredential: true, enabled: true }],
  roles: { leader: 'advisor', backups: [], advisor: 'advisor', handoff: null },
  policy: { allowCloud: kind === 'cloud', maxAttempts: 3, deadlineSeconds: 120 } })
const job = (status = 'completed') => ({ jobId: id, status, providerLabel: 'Tower advisor', model: 'glm', revision: 3,
  result: status === 'completed' ? { text: '<img src=x onerror=alert(1)> Advice', usage: { total_tokens: null } } : null })
const response = value => ({ ok: true, json: async () => value })

async function setup({ kind, post, onInsert = vi.fn() } = {}) {
  vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(id)
  const fetchMock = vi.fn(async (url, options) => {
    if (url === '/api/pixel/providers') return response({ configuration: config(kind) })
    if (url === '/api/pixel/advice-runtime') return response({ status: 'ready', revision: 1,
      runtimeId: 'runtime-' + 'c'.repeat(32), sourceSha256: 'a'.repeat(64), host: 'Tower2', candidates: [], job: null })
    if (post) return post(url, options)
    return response(job())
  })
  vi.stubGlobal('fetch', fetchMock)
  const view = render(createElement(PixelAdvice, { onInsert }))
  fireEvent.click(screen.getByRole('button', { name: /^Ask for advice/ }))
  if (!localStorage.getItem(key)) await screen.findByText(/saved revision 3/)
  return { fetchMock, onInsert, ...view }
}

beforeEach(() => localStorage.clear())
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })

it('sends only reviewed capsule and fixed selected revision once, stores only job ID', async () => {
  const { fetchMock, onInsert } = await setup()
  const baselineKeys = Object.keys(localStorage)
  fireEvent.change(screen.getByLabelText('Capsule to send'), { target: { value: 'Fictional capsule' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send reviewed capsule' }))
  await screen.findByText('Advisory answer — untrusted model output')
  const starts = fetchMock.mock.calls.filter(([url]) => url.endsWith('/start'))
  expect(starts).toHaveLength(1)
  expect(JSON.parse(starts[0][1].body)).toEqual({ requestId: id, expectedRevision: 3, providerId: 'advisor',
    capsule: 'Fictional capsule', allowCloud: false, acceptUnknownCost: false, maxOutputTokens: 1024, deadlineSeconds: 120 })
  expect(Object.keys(localStorage).filter(item => !baselineKeys.includes(item))).toEqual([key])
  expect(JSON.stringify(localStorage)).not.toContain('Fictional capsule')
  expect(localStorage.getItem(key)).toBe(id)
  expect(onInsert).not.toHaveBeenCalled()
  expect(document.querySelector('img')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Paste advice into composer (does not send)' }))
  expect(onInsert).toHaveBeenCalledOnce()
  expect(onInsert.mock.calls[0][0]).toContain('untrusted')
  expect(screen.getByRole('button', { name: /^Ask for advice/ })).toHaveFocus()
  expect(starts).toHaveLength(1)
})

it('requires both cloud approvals and resets approval when capsule changes', async () => {
  await setup({ kind: 'cloud' })
  fireEvent.change(screen.getByLabelText('Capsule to send'), { target: { value: 'Review this' } })
  const send = screen.getByRole('button', { name: 'Send reviewed capsule' })
  expect(send).toBeDisabled()
  fireEvent.click(screen.getByLabelText('Allow this cloud advisor to receive exactly this capsule'))
  expect(send).toBeDisabled()
  fireEvent.click(screen.getByLabelText('Accept unknown cost for this one bounded call'))
  expect(send).toBeEnabled()
  fireEvent.change(screen.getByLabelText('Capsule to send'), { target: { value: 'Changed scope' } })
  expect(send).toBeDisabled()
})

it('tracks ambiguous start and never automatically posts another start', async () => {
  const { fetchMock } = await setup({ post: async () => { throw new Error('Lost response') } })
  fireEvent.change(screen.getByLabelText('Capsule to send'), { target: { value: 'Review this' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send reviewed capsule' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('button', { name: 'Stop advice' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: 'Check job' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/status'))).toBe(true))
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/start'))).toHaveLength(1)
  expect(localStorage.getItem(key)).toBe(id)
})

it('reload recovers only opaque ID, checks status and can stop an active job', async () => {
  localStorage.setItem(key, id)
  const { fetchMock } = await setup({ post: async url => response(job(url.endsWith('/cancel') ? 'cancelled' : 'running')) })
  await screen.findByText('Advice: running')
  expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/start'))).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Stop advice' }))
  await screen.findByText('Advice: cancelled')
  expect(fetchMock.mock.calls.find(([url]) => url.endsWith('/cancel'))[1].body).toBe(JSON.stringify({ jobId: id }))
})

it('closing panel does not start, stop, or change the chat', async () => {
  const { fetchMock, onInsert } = await setup()
  fireEvent.change(screen.getByLabelText('Capsule to send'), { target: { value: 'Private draft' } })
  fireEvent.click(screen.getByRole('button', { name: 'Close advice' }))
  expect(onInsert).not.toHaveBeenCalled()
  expect(fetchMock.mock.calls.every(([, options]) => options.method !== 'POST')).toBe(true)
  expect(localStorage.getItem(key)).toBeNull()
  expect(JSON.stringify(localStorage)).not.toContain('Private draft')
})

it('keeps keyboard focus in the dialog and Escape returns it to the trigger', async () => {
  await setup()
  const close = screen.getByRole('button', { name: 'Close advice' })
  expect(close).toHaveFocus()
  fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })
  expect(screen.getByLabelText('Capsule to send')).toHaveFocus()
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Ask for advice' })).toHaveFocus()
})

it('ignores an old pending status response after forgetting and starting a new job', async () => {
  localStorage.setItem(key, id)
  let finishOldStatus
  const nextId = '0a15e657-bb03-4364-96e0-e9fb8aeb5b76'
  const { fetchMock } = await setup({ post: async url => {
    if (url.endsWith('/status')) return new Promise(resolve => { finishOldStatus = resolve })
    if (url.endsWith('/cancel')) throw new Error('Unconfirmed stop')
    return response({ ...job(), jobId: nextId, result: { text: 'Current advice' } })
  } })
  await waitFor(() => expect(finishOldStatus).toBeTypeOf('function'))
  fireEvent.click(screen.getByRole('button', { name: 'Stop advice' }))
  await screen.findByRole('alert')
  fireEvent.click(screen.getByRole('button', { name: 'Forget tracking (does not stop the request)' }))
  globalThis.crypto.randomUUID.mockReturnValue(nextId)
  await screen.findByLabelText('Capsule to send')
  fireEvent.change(screen.getByLabelText('Capsule to send'), { target: { value: 'New scope' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send reviewed capsule' }))
  await screen.findByText('Current advice')
  finishOldStatus(response(job()))
  await waitFor(() => expect(screen.getByText('Current advice')).toBeInTheDocument())
  expect(screen.queryByText('<img src=x onerror=alert(1)> Advice')).not.toBeInTheDocument()
  expect(localStorage.getItem(key)).toBe(nextId)
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/start'))).toHaveLength(1)
})
