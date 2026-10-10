import { act, fireEvent, render, screen } from '@testing-library/react'
import HuggingFaceModelBrowser from './HuggingFaceModelBrowser'

const repo = { id: 'org/model', author: 'org', sha: 'c'.repeat(40) }
const artifact = { id: 'q4', label: 'model.gguf', sizeBytes: 1e9, files: [{ filename: 'model.gguf' }], importedModelId: 'hf-fixture' }
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })
let progress, post, onImportStarted
beforeEach(() => {
  vi.useFakeTimers()
  progress = { status: 'idle' }
  post = () => new Promise(() => {})
  onImportStarted = vi.fn()
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    if (options?.method === 'POST') return await post()
    if (url.includes('/search?')) return response({ models: [repo] })
    if (url.includes('/repositories/')) return response({ ...repo, artifacts: [artifact] })
    if (url.includes('/preflight/')) return response({
      ...repo, artifactId: artifact.id, modelKind: 'chat',
      header: { status: 'read', file: 'model.gguf' },
      artifacts: { [artifact.id]: { header: { status: 'read', file: 'model.gguf' }, refusal: null } },
    })
    if (url === '/api/models') return response({ models: [{ id: artifact.importedModelId, gguf: 'hf-model.gguf' }] })
    return response(progress)
  }))
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
async function open() {
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Choose file' })) })
}
async function begin() {
  render(<HuggingFaceModelBrowser onImportStarted={onImportStarted}/>)
  await act(async () => { await vi.advanceTimersByTimeAsync(350) })
  await open()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry', exact: true })) })
}
const posts = () => fetch.mock.calls.filter(([, options]) => options?.method === 'POST')

test('allows closing a stalled import and reconciles the exact download without replaying POST', async () => {
  await begin()
  fireEvent.click(screen.getByTitle('Close'))
  expect(screen.queryByRole('dialog')).toBeNull()
  await act(async () => { await vi.advanceTimersByTimeAsync(45000) })
  expect(screen.getByText(/The request timed out/)).toBeVisible()
  await open()
  expect(screen.getByRole('button', { name: 'Retry', exact: true })).toBeDisabled()
  fireEvent.click(screen.getByTitle('Close'))
  progress = { status: 'downloading', model: 'hf-model.gguf', bytesDownloaded: 20, updatedAt: new Date().toISOString() }
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Check download status' })) })
  expect(onImportStarted).toHaveBeenCalledWith({ modelId: artifact.importedModelId, status: 'downloading' })
  expect(posts()).toHaveLength(1)
  expect(screen.queryByRole('button', { name: 'Check download status' })).toBeNull()
})

test('does not treat another artifact or idle state as permission to duplicate an uncertain import', async () => {
  await begin()
  await act(async () => { await vi.advanceTimersByTimeAsync(45000) })
  progress = { status: 'complete', model: 'another.gguf', updatedAt: new Date().toISOString() }
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Check download status' })) })
  expect(screen.getByText(/not yet confirmed/)).toBeVisible()
  expect(screen.getByRole('button', { name: 'Retry', exact: true })).toBeDisabled()
  expect(posts()).toHaveLength(1)
  expect(onImportStarted).not.toHaveBeenCalled()
})

test('bounds stalled response bodies and ignores a late successful response', async () => {
  let finish
  post = async () => ({ ok: true, status: 200, json: () => new Promise(resolve => { finish = resolve }) })
  await begin()
  await act(async () => { await vi.advanceTimersByTimeAsync(45000) })
  await act(async () => { finish({ modelId: artifact.importedModelId }) })
  expect(onImportStarted).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'Check download status' })).toBeEnabled()
  expect(posts()).toHaveLength(1)
})

test('an older completion for the same artifact cannot resolve a new uncertain import', async () => {
  const previous = new Date(Date.now() - 60000).toISOString()
  await begin()
  await act(async () => { await vi.advanceTimersByTimeAsync(45000) })
  progress = { status: 'complete', model: 'hf-model.gguf', updatedAt: previous }
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Check download status' })) })
  expect(screen.getByText(/not yet confirmed/)).toBeVisible()
  expect(onImportStarted).not.toHaveBeenCalled()
  expect(posts()).toHaveLength(1)
})

test('a definitive refusal releases import controls and displays the server reason', async () => {
  post = async () => response({ detail: 'Private repository requires HF_TOKEN' }, 403)
  await begin()
  expect(screen.getByText('Private repository requires HF_TOKEN')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Retry', exact: true })).toBeEnabled()
  expect(screen.queryByRole('button', { name: 'Check download status' })).toBeNull()
})

test('a preparation failure marked not dispatched permits retry even on HTTP 500', async () => {
  post = async () => ({ ...response({ detail: 'No download was started; retry.' }, 500),
    headers: { get: key => key === 'X-ODS-Import-Started' ? 'false' : null } })
  await begin()
  expect(screen.getByText('No download was started; retry.')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Retry', exact: true })).toBeEnabled()
  expect(screen.queryByRole('button', { name: 'Check download status' })).toBeNull()
  expect(posts()).toHaveLength(1)
})

test('an uncertain server error still requires readback rather than replaying a download', async () => {
  post = async () => response({ detail: 'Upstream connection lost' }, 502)
  await begin()
  expect(screen.getByRole('button', { name: 'Retry', exact: true })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Check download status' })).toBeEnabled()
  expect(posts()).toHaveLength(1)
})

test.each(['failed', 'cancelled', 'canceled'])('resolves matching %s retained inside idle status', async status => {
  await begin()
  await act(async () => { await vi.advanceTimersByTimeAsync(45000) })
  progress = { status: 'idle', lastTerminalStatus: {
    status, model: 'hf-model.gguf', updatedAt: new Date().toISOString(),
  } }
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Check download status' })) })
  expect(onImportStarted).toHaveBeenCalledWith({ modelId: artifact.importedModelId, status })
  expect(screen.queryByRole('button', { name: 'Check download status' })).toBeNull()
  expect(posts()).toHaveLength(1)
})
