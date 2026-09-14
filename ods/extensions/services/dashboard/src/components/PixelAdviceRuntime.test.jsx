import { fireEvent, screen, waitFor, act } from '@testing-library/react'
import { createElement } from 'react'
import { render } from '../test/test-utils'
import PixelAdviceRuntime from './PixelAdviceRuntime.jsx'

const id = 'c2ac7cba-198d-4cb6-b4ca-722eb29d778f'
const storage = 'ods.pixel.advice.setup.v1'
const consent = /Allow a private runtime and dependency download/
const state = (status = 'missing') => ({ status, revision: status === 'ready' ? 1 : 0,
  runtimeId: status === 'ready' ? 'runtime-' + 'd'.repeat(32) : null, sourceSha256: 'a'.repeat(64), host: 'Tower2',
  candidates: [{ id: 'b'.repeat(64), path: '/usr/bin/python3.12', version: '3.12.3', canPrepare: true }], job: null })
const job = (status = 'running') => ({ jobId: id, expectedRevision: 0, runtimeId: 'runtime-' + id.replaceAll('-', ''),
  candidateId: 'b'.repeat(64), status, error: status === 'failed' ? 'setup-failed' : null })
const response = value => ({ ok: true, json: async () => value })

async function setup(handler) {
  const onReadyChange = vi.fn()
  vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(id)
  const fetchMock = vi.fn(handler || (async url => response(url.endsWith('advice-runtime') ? state() : job())))
  vi.stubGlobal('fetch', fetchMock)
  const view = render(createElement(PixelAdviceRuntime, { onReadyChange }))
  await screen.findByText(/Runtime: .* on Tower2/)
  return { ...view, fetchMock, onReadyChange }
}
beforeEach(() => localStorage.clear())
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })

it('requires explicit consent and submits only the server candidate ID once', async () => {
  const { fetchMock } = await setup()
  const prepare = screen.getByRole('button', { name: 'Prepare private runtime' })
  expect(prepare).toBeDisabled()
  expect(screen.getByLabelText('Python on Tower2')).toHaveValue('b'.repeat(64))
  fireEvent.click(screen.getByLabelText(consent)); fireEvent.click(prepare); fireEvent.click(prepare)
  await screen.findByText('Setup: running')
  const posts = fetchMock.mock.calls.filter(([url]) => url.endsWith('/prepare'))
  expect(posts).toHaveLength(1)
  expect(JSON.parse(posts[0][1].body)).toEqual({ requestId: id, expectedRevision: 0,
    sourceSha256: 'a'.repeat(64), candidateId: 'b'.repeat(64), confirmed: true })
  expect(posts[0][1].body).not.toContain('/usr/bin')
  expect(localStorage.getItem(storage)).toBe(id)
})

it('defers status reads and stop until the prepare receipt arrives', async () => {
  let resolvePrepare
  const pendingPrepare = new Promise(resolve => { resolvePrepare = resolve })
  const { fetchMock } = await setup(async url => {
    if (url.endsWith('/prepare')) return pendingPrepare
    return response(url.endsWith('advice-runtime') ? state() : job())
  })
  fireEvent.click(screen.getByLabelText(consent))
  fireEvent.click(screen.getByRole('button', { name: 'Prepare private runtime' }))
  await screen.findByText('Tracked setup: ' + id)
  fireEvent.click(screen.getByRole('button', { name: 'Check setup' }))
  const stop = screen.getByRole('button', { name: 'Stop setup' })
  expect(stop).toBeDisabled()
  fireEvent.click(stop)
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/status') || url.endsWith('/cancel'))).toHaveLength(0)
  await act(async () => resolvePrepare(response(job())))
  await screen.findByText('Setup: running')
  expect(screen.getByRole('button', { name: 'Stop setup' })).toBeEnabled()
  await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/status'))).toHaveLength(1))
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/prepare'))).toHaveLength(1)
})

it('reload adopts durable setup without automatically preparing again', async () => {
  const { fetchMock } = await setup(async url => response(url.endsWith('advice-runtime') ? { ...state(), job: job() } : job()))
  await screen.findByText('Setup: running')
  expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/prepare'))).toBe(false)
  expect(screen.getByLabelText(consent)).not.toBeChecked()
})

it('candidate/source/revision changes clear consent', async () => {
  let value = state()
  await setup(async () => response(value))
  fireEvent.click(screen.getByLabelText(consent))
  value = { ...state(), revision: 1, sourceSha256: 'c'.repeat(64) }
  fireEvent.click(screen.getByRole('button', { name: 'Refresh runtime readiness' }))
  await waitFor(() => expect(screen.getByLabelText(consent)).not.toBeChecked())
  expect(screen.getByRole('button', { name: 'Prepare private runtime' })).toBeDisabled()
})

it('unknown start is tracked without automatic retry', async () => {
  const { fetchMock } = await setup(async url => {
    if (url.endsWith('advice-runtime')) return response(state())
    throw new Error('Lost response')
  })
  fireEvent.click(screen.getByLabelText(consent))
  fireEvent.click(screen.getByRole('button', { name: 'Prepare private runtime' }))
  await screen.findByRole('alert')
  expect(localStorage.getItem(storage)).toBe(id)
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/prepare'))).toHaveLength(1)
  expect(screen.getByRole('button', { name: 'Prepare private runtime' })).toBeDisabled()
})

it('late stop reports completed publication without claiming rollback', async () => {
  localStorage.setItem(storage, id)
  let installed = false
  const { onReadyChange } = await setup(async url => {
    if (url.endsWith('/cancel')) { installed = true; return response(job('completed')) }
    if (url.endsWith('advice-runtime')) return response(state(installed ? 'ready' : 'missing'))
    return response(job())
  })
  await screen.findByText('Setup: running')
  fireEvent.click(screen.getByRole('button', { name: 'Stop setup' }))
  await screen.findByText('Setup: completed')
  await screen.findByText('Runtime: ready on Tower2')
  expect(onReadyChange).toHaveBeenLastCalledWith(true)
  expect(screen.queryByText('Setup: cancelled')).not.toBeInTheDocument()
})

it.each(['running', 'error'])('ignores a stale %s status read after a completed stop receipt', async late => {
  localStorage.setItem(storage, id)
  let resolveStatus
  let rejectStatus
  const delayedStatus = new Promise((resolve, reject) => { resolveStatus = resolve; rejectStatus = reject })
  const { fetchMock, onReadyChange } = await setup(async url => {
    if (url.endsWith('/status')) return delayedStatus
    if (url.endsWith('/cancel')) return response(job('completed'))
    return response(state('ready'))
  })
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/status'))).toBe(true))
  fireEvent.click(screen.getByRole('button', {name:'Stop setup'}))
  fireEvent.click(screen.getByRole('button', {name:'Stop setup'}))
  await screen.findByText('Setup: completed')
  await act(async () => {
    if (late === 'error') rejectStatus(new Error('Old read failed'))
    else resolveStatus(response(job('running')))
  })
  expect(screen.getByText('Setup: completed')).toBeVisible()
  expect(screen.queryByRole('button', {name:'Stop setup'})).toBeNull()
  expect(onReadyChange).toHaveBeenLastCalledWith(true)
  expect(screen.queryByRole('alert')).toBeNull()
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/cancel'))).toHaveLength(1)
})

it('unavailable venv shows operator remediation and cannot prepare', async () => {
  await setup(async () => response({ ...state(), candidates: [{ ...state().candidates[0], canPrepare: false }] }))
  expect(screen.getByText(/An operator must install Python/)).toBeInTheDocument()
  fireEvent.click(screen.getByLabelText(consent))
  expect(screen.getByRole('button', { name: 'Prepare private runtime' })).toBeDisabled()
})

it('blocks preparation while disabled without losing unchanged consent or status access', async () => {
  const { rerender, fetchMock, onReadyChange } = await setup()
  fireEvent.click(screen.getByLabelText(consent))
  const prepare = screen.getByRole('button', { name: 'Prepare private runtime' })
  expect(prepare).toBeEnabled()
  rerender(createElement(PixelAdviceRuntime, { onReadyChange, disabled: true }))
  expect(prepare).toBeDisabled()
  expect(screen.getByLabelText(consent)).toBeChecked()
  expect(screen.getByRole('button', { name: 'Refresh runtime readiness' })).toBeEnabled()
  fireEvent.click(prepare)
  expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/prepare'))).toBe(false)
  rerender(createElement(PixelAdviceRuntime, { onReadyChange, disabled: false }))
  expect(prepare).toBeEnabled()
})
