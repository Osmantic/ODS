import { fireEvent, screen, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import { render } from '../../test/test-utils'
import PixelSharingSettings from './PixelSharingSettings'
import { connectionBaseUrl, connectionBundle } from './pixelSharingForm'

const key = 'ods_infer_' + 'a'.repeat(64)
const now = Math.floor(Date.now() / 1000)
const device = () => ({ id: 'device-' + 'b'.repeat(16), label: 'Laptop', catalogId: 'glm', runtimeModelId: 'GLM',
  createdAt: now, expiresAt: now + 86400,
  revoked: false, maxConcurrent: 1, maxOutputTokens: 4096, deadlineSeconds: 120, requestsPerMinute: 60 })
const snapshot = (revision = 0, devices = [], status = 'stopped') => ({
  configuration: { schemaVersion: 1, revision, devices, enabled: false },
  activeRoute: { catalogId: 'glm', runtimeModelId: 'GLM', routeSeq: 4, contextLength: 32768,
    capabilities: { chat: true, tools: true, vision: false, agentViable: true } },
  transport: { mode: 'loopback-only', defaultPort: 4005, port: 4005 }, runtime: { status },
})
const issued = () => ({ ...snapshot(1, [device()]), credential: { id: device().id, key }, model: 'ods/shared' })
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })
function setup(initial = snapshot(), post = () => response(issued())) {
  const fetchMock = vi.fn(async (url, options) => options.method === 'POST' ? post(url, options) : response(initial))
  vi.stubGlobal('fetch', fetchMock)
  const rendered = render(createElement(PixelSharingSettings))
  return { fetchMock, ...rendered }
}
async function createKey() {
  await screen.findByText('GLM')
  fireEvent.change(screen.getByLabelText('Device label'), { target: { value: 'Laptop' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create device key' }))
  await screen.findByLabelText('Device API key')
}
beforeEach(() => vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } }))
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('keeps a pristine install stopped and prevents start without a device', async () => {
  setup()
  expect(await screen.findByText('stopped')).toBeInTheDocument()
  expect(screen.queryByText('ready')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Start sharing' })).toBeDisabled()
  expect(screen.queryByLabelText('Device API key')).not.toBeInTheDocument()
})

it('does not fabricate a usable snapshot from malformed or failed reads', async () => {
  setup({})
  expect(await screen.findByRole('alert')).toHaveTextContent('unavailable')
  expect(screen.queryByRole('button', { name: 'Start sharing' })).not.toBeInTheDocument()
})

it('copies the one-time key only on explicit action and never persists it in browser storage', async () => {
  const storage = vi.spyOn(window.Storage.prototype, 'setItem')
  const { fetchMock } = setup()
  await createKey()
  expect(navigator.clipboard.writeText).not.toHaveBeenCalled()
  expect(screen.getByLabelText('Device API key')).toHaveAttribute('type', 'password')
  fireEvent.click(screen.getByRole('button', { name: 'Copy connection settings' }))
  await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledTimes(1))
  const bundle = JSON.parse(navigator.clipboard.writeText.mock.calls[0][0])
  expect(bundle).toEqual(connectionBundle(issued(), 'http://127.0.0.1:4005/v1'))
  // The shared test wrapper persists its theme, not this component's keys.
  expect(storage.mock.calls.every(([name]) => name === 'ods-theme')).toBe(true)
  expect(JSON.stringify(storage.mock.calls)).not.toContain(key)
  expect(fetchMock.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss key' }))
  expect(screen.queryByLabelText('Device API key')).not.toBeInTheDocument()
})

it('rejects a plaintext remote URL without sending a key to the clipboard', async () => {
  setup()
  await createKey()
  fireEvent.change(screen.getByLabelText('Laptop connection URL'), { target: { value: 'http://tower.example/v1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Copy connection settings' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not copy')
  expect(navigator.clipboard.writeText).not.toHaveBeenCalled()
})

it('revocation removes the retained one-time key', async () => {
  setup(snapshot(), url => response(url.endsWith('/issue') ? issued() : snapshot(2, [{ ...device(), revoked: true }])))
  await createKey()
  fireEvent.click(screen.getByRole('button', { name: 'Revoke Laptop' }))
  await waitFor(() => expect(screen.queryByLabelText('Device API key')).not.toBeInTheDocument())
  expect(screen.getByRole('button', { name: 'Revoke Laptop' })).toBeDisabled()
})

it('start requires confirmation, passes exact revision and accepts HTTP 202', async () => {
  const { fetchMock } = setup(snapshot(1, [device()]), () => response(snapshot(2, [device()], 'starting'), 202))
  await screen.findByText('stopped')
  fireEvent.click(screen.getByRole('button', { name: 'Start sharing' }))
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: 'Confirm start' }))
  expect(await screen.findByText('starting')).toBeInTheDocument()
  const [url, options] = fetchMock.mock.calls[1]
  expect(url).toBe('/api/pixel/inference-sharing/start')
  expect(JSON.parse(options.body)).toEqual({ expectedRevision: 1 })
  expect(screen.getByRole('button', { name: 'Start sharing' })).toBeDisabled()
})

it('never retries ambiguous writes and requires a successful reload before another write', async () => {
  const { fetchMock } = setup(snapshot(), () => { throw new Error('private-sentinel') })
  await screen.findByText('stopped')
  fireEvent.change(screen.getByLabelText('Device label'), { target: { value: 'Laptop' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create device key' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Operation result is unknown')
  expect(screen.getByRole('alert')).not.toHaveTextContent('private-sentinel')
  expect(screen.getByRole('button', { name: 'Create device key' })).toBeDisabled()
  expect(fetchMock).toHaveBeenCalledTimes(2)
  fireEvent.click(screen.getByRole('button', { name: 'Reload sharing' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create device key' })).toBeEnabled())
  expect(fetchMock).toHaveBeenCalledTimes(3)
})

it('serializes duplicate clicks and aborts an outstanding request on unmount', async () => {
  let resolve
  const deferred = new Promise(done => { resolve = done })
  const { fetchMock, unmount } = setup(snapshot(), () => deferred)
  await screen.findByText('stopped')
  fireEvent.change(screen.getByLabelText('Device label'), { target: { value: 'Laptop' } })
  const button = screen.getByRole('button', { name: 'Create device key' })
  fireEvent.click(button)
  fireEvent.click(button)
  expect(fetchMock).toHaveBeenCalledTimes(2)
  unmount()
  expect(fetchMock.mock.calls[1][1].signal.aborted).toBe(true)
  resolve(response(issued()))
})

it.each(['http://tower/v1', 'https://user:secret@tower/v1', 'https://tower/v1?key=x', 'https://tower/v1#x', 'https://tower/admin', 'file:///v1'])('rejects unsafe connection URL %s', value => {
  expect(() => connectionBaseUrl(value)).toThrow()
})
it.each(['https://tower.example/v1', 'http://localhost:5000/v1', 'http://127.0.0.1:4005/v1', 'http://[::1]:4005/v1'])('accepts explicit secure/tunneled URL %s', value => {
  expect(connectionBaseUrl(value)).toBe(value)
})
