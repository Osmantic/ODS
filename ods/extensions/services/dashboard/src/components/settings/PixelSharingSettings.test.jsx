import { act, fireEvent, screen, waitFor } from '@testing-library/react'
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

it('uses the advertised sharing port and preserves an edited client URL on reload', async () => {
  const withPort = value => ({...value,transport:{...value.transport,port:4405}})
  setup(withPort(snapshot()), () => response(withPort(issued())))
  await createKey()
  expect(screen.getByLabelText('Laptop connection URL')).toHaveValue('http://127.0.0.1:4405/v1')
  fireEvent.click(screen.getByRole('button',{name:'Copy connection settings'}))
  await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledOnce())
  expect(JSON.parse(navigator.clipboard.writeText.mock.calls[0][0]).baseUrl).toBe('http://127.0.0.1:4405/v1')
  fireEvent.change(screen.getByLabelText('Laptop connection URL'),{target:{value:'https://my-ingress.example/v1'}})
  fireEvent.click(screen.getByRole('button',{name:'Reload sharing'}))
  await waitFor(() => expect(screen.getByRole('button',{name:'Reload sharing'})).toBeEnabled())
  // Issue again after reloading a snapshot without the retained one-time key.
  await createKey()
  expect(screen.getByLabelText('Laptop connection URL')).toHaveValue('https://my-ingress.example/v1')
  fireEvent.click(screen.getByRole('button',{name:'Copy connection settings'}))
  await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledTimes(2))
  expect(JSON.parse(navigator.clipboard.writeText.mock.calls[1][0])).toEqual(
    connectionBundle(withPort(issued()), 'https://my-ingress.example/v1'),
  )
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

it('expires the retained key and device admission at their deadlines without another request', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(now*1000)
  try {
    const first={...device(),expiresAt:now+2}
    const second={...device(),id:'device-'+'c'.repeat(16),label:'Second laptop',expiresAt:now+4}
    let view
    await act(async () => {view=setup(snapshot(),() => response({...issued(),configuration:{...issued().configuration,devices:[first,second]}}))})
    fireEvent.change(screen.getByLabelText('Device label'),{target:{value:'Laptop'}})
    await act(async () => fireEvent.click(screen.getByRole('button',{name:'Create device key'})))
    expect(screen.getByLabelText('Device API key')).toBeVisible()
    await act(async () => vi.advanceTimersByTime(2001))
    expect(screen.queryByLabelText('Device API key')).toBeNull()
    expect(screen.getByRole('button',{name:'Start sharing'})).toBeEnabled()
    expect(screen.getAllByText(/GLM · Expired/)).toHaveLength(1)
    await act(async () => vi.advanceTimersByTime(2000))
    expect(screen.getByRole('button',{name:'Start sharing'})).toBeDisabled()
    expect(screen.getAllByText(/GLM · Expired/)).toHaveLength(2)
    expect(view.fetchMock).toHaveBeenCalledTimes(2)
    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
  } finally {vi.useRealTimers()}
})

it.each(['url','dismiss'])('does not acknowledge an obsolete clipboard write after %s changes', async change => {
  setup()
  await createKey()
  let complete
  navigator.clipboard.writeText.mockImplementation(() => new Promise(resolve => {complete = resolve}))
  const button = screen.getByRole('button',{name:'Copy connection settings'})
  fireEvent.click(button)
  fireEvent.click(button)
  expect(navigator.clipboard.writeText).toHaveBeenCalledTimes(1)
  if (change === 'url') fireEvent.change(screen.getByLabelText('Laptop connection URL'),{target:{value:'https://new.example/v1'}})
  else fireEvent.click(screen.getByRole('button',{name:'Dismiss key'}))
  await act(async () => complete())
  expect(screen.queryByText(/Connection settings copied/)).toBeNull()
  if (change === 'url') {
    navigator.clipboard.writeText.mockResolvedValueOnce(undefined)
    fireEvent.click(screen.getByRole('button',{name:'Copy connection settings'}))
    expect(await screen.findByText(/Connection settings copied/)).toBeVisible()
    expect(JSON.parse(navigator.clipboard.writeText.mock.calls.at(-1)[0]).baseUrl).toBe('https://new.example/v1')
  }
})

it('clears a previous copy success before a failed copy of edited connection settings', async () => {
  setup()
  await createKey()
  fireEvent.click(screen.getByRole('button',{name:'Copy connection settings'}))
  await screen.findByText(/Connection settings copied/)
  fireEvent.change(screen.getByLabelText('Laptop connection URL'),{target:{value:'https://changed.example/v1'}})
  navigator.clipboard.writeText.mockRejectedValueOnce(new Error('denied'))
  fireEvent.click(screen.getByRole('button',{name:'Copy connection settings'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not copy')
  expect(screen.queryByText(/Connection settings copied/)).toBeNull()
})

it('discards a prior receipt when the connection is edited, even if its URL is later restored', async () => {
  setup()
  await createKey()
  fireEvent.click(screen.getByRole('button',{name:'Copy connection settings'}))
  await screen.findByText(/Connection settings copied/)
  const url = screen.getByLabelText('Laptop connection URL')
  fireEvent.change(url,{target:{value:'https://edited.example/v1'}})
  fireEvent.change(url,{target:{value:'http://127.0.0.1:4005/v1'}})
  expect(screen.queryByText(/Connection settings copied/)).toBeNull()
  expect(navigator.clipboard.writeText).toHaveBeenCalledTimes(1)
})

it('invalidates a pending sharing confirmation before a reload can replace its reviewed state', async () => {
  const {fetchMock} = setup(snapshot(1,[device()]))
  await screen.findByText('stopped')
  fireEvent.click(screen.getByRole('button',{name:'Start sharing'}))
  expect(screen.getByRole('dialog')).toBeVisible()
  let resolve
  fetchMock.mockImplementationOnce(() => new Promise(done => {resolve = done}))
  fireEvent.click(screen.getByRole('button',{name:'Reload sharing'}))
  expect(screen.queryByRole('dialog')).toBeNull()
  await act(async () => resolve(response({...snapshot(2,[device()]),transport:{mode:'loopback-only',defaultPort:4005,port:4405}})))
  expect(screen.queryByRole('button',{name:'Confirm start'})).toBeNull()
  expect(fetchMock.mock.calls.filter(([,options]) => options.method === 'POST')).toHaveLength(0)
  fireEvent.click(screen.getByRole('button',{name:'Start sharing'}))
  expect(screen.getByRole('dialog')).toHaveTextContent('127.0.0.1:4405')
  fetchMock.mockResolvedValueOnce(response(snapshot(3,[device()],'starting'),202))
  fireEvent.click(screen.getByRole('button',{name:'Confirm start'}))
  await screen.findByText('starting')
  expect(JSON.parse(fetchMock.mock.calls.at(-1)[1].body)).toEqual({expectedRevision:2})
})

it('requires renewed review after an intervening device mutation', async () => {
  setup(snapshot(1,[device()]), () => response(snapshot(2,[{...device(),revoked:true}])))
  await screen.findByText('stopped')
  fireEvent.click(screen.getByRole('button',{name:'Stop sharing'}))
  fireEvent.click(screen.getByRole('button',{name:'Revoke Laptop'}))
  await screen.findByText('GLM · Revoked')
  expect(screen.queryByRole('button',{name:'Confirm stop'})).toBeNull()
})
