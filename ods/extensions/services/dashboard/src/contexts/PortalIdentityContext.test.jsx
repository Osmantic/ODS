import { StrictMode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import { PortalIdentityProvider, usePortalIdentity } from './PortalIdentityContext'
import AssistantIdentitySettings from '../components/settings/AssistantIdentitySettings'
import Sidebar from '../components/Sidebar'
import Pixel from '../pages/Pixel'
import { ThemeProvider } from './ThemeContext'

const identity = (displayName = 'Portal', revision = 0) => ({ schemaVersion: 1, displayName, revision })
const response = value => new globalThis.Response(JSON.stringify(value), { status: 200 })
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
function Name() { return <output data-testid="name">{usePortalIdentity().displayName}</output> }
const editor = () => <PortalIdentityProvider><Name/><AssistantIdentitySettings/></PortalIdentityProvider>
const saved = () => screen.getByTestId('name').textContent
const saveButton = () => screen.getByRole('button', { name: 'Save name', exact: true })
const refresh = () => fireEvent.click(screen.getByRole('button', { name: 'Refresh saved name' }))
const edit = name => { fireEvent.change(screen.getByLabelText('Assistant display name'), { target: { value: name } }); fireEvent.click(saveButton()) }
const postCalls = () => fetch.mock.calls.filter(([, options]) => options.method === 'POST')
beforeEach(() => { vi.stubGlobal('fetch', vi.fn()); globalThis.localStorage.clear() })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

it('loads install identity before enabling edits and does not cache it in browser storage', async () => {
  const pending = deferred(); fetch.mockReturnValue(pending.promise)
  render(editor()); expect(saved()).toBe('Portal'); expect(saveButton()).toBeDisabled()
  const storage = () => Array.from({ length: globalThis.localStorage.length }, (_, i) => {
    const key = globalThis.localStorage.key(i); return [key, globalThis.localStorage.getItem(key)]
  })
  const before = storage() // The enclosing ThemeProvider persists its unrelated preference.
  await act(async () => pending.resolve(response(identity('Nova', 4))))
  expect(saved()).toBe('Nova'); expect(saveButton()).toBeEnabled()
  expect(storage()).toEqual(before)
  expect(fetch).toHaveBeenCalledWith('/api/pixel/identity', expect.objectContaining({ method: 'GET', cache: 'no-store' }))
})

it('requires exact persisted GET after POST before reporting save success', async () => {
  const readback = deferred()
  fetch.mockResolvedValueOnce(response(identity())).mockResolvedValueOnce(response(identity('Café', 1))).mockReturnValueOnce(readback.promise)
  render(editor()); await waitFor(() => expect(saveButton()).toBeEnabled())
  edit(' Cafe\u0301 '); await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3))
  expect(saved()).toBe('Portal'); expect(saveButton()).toBeDisabled(); expect(screen.queryByText('Assistant name saved.')).toBeNull()
  await act(async () => readback.resolve(response(identity('Café', 1))))
  expect(saved()).toBe('Café'); expect(screen.getByText('Assistant name saved.')).toBeInTheDocument()
  expect(JSON.parse(postCalls()[0][1].body)).toEqual({ expectedRevision: 0, displayName: 'Café' })
})

it('retains the prior name on conflict and requires explicit refresh without replaying POST', async () => {
  fetch.mockResolvedValueOnce(response(identity('Old', 2))).mockResolvedValueOnce(new globalThis.Response('private-sentinel', { status: 409 }))
    .mockResolvedValueOnce(response(identity('Elsewhere', 3)))
  render(editor()); await waitFor(() => expect(saveButton()).toBeEnabled()); edit('Mine')
  expect(await screen.findByRole('alert')).toHaveTextContent('changed elsewhere')
  expect(saved()).toBe('Old'); expect(saveButton()).toBeDisabled(); expect(fetch).toHaveBeenCalledTimes(2)
  expect(document.body).not.toHaveTextContent('private-sentinel')
  refresh(); await waitFor(() => expect(saved()).toBe('Elsewhere')); expect(postCalls()).toHaveLength(1)
})

it('recovers a committed save with a lost response only through a new GET', async () => {
  let server = identity('Old', 2)
  fetch.mockImplementation(async (_url, options) => {
    if (options.method === 'POST') { server = identity('New', 3); throw new Error('private transport diagnostic') }
    return response(server)
  })
  render(editor()); await waitFor(() => expect(saveButton()).toBeEnabled()); edit('New')
  expect(await screen.findByRole('alert')).toHaveTextContent('could not be confirmed')
  expect(saved()).toBe('Old'); expect(saveButton()).toBeDisabled(); expect(postCalls()).toHaveLength(1)
  refresh(); await waitFor(() => expect(saved()).toBe('New')); expect(postCalls()).toHaveLength(1)
})

it('does not claim a save when another writer wins the readback', async () => {
  fetch.mockResolvedValueOnce(response(identity())).mockResolvedValueOnce(response(identity('Mine', 1)))
    .mockResolvedValueOnce(response(identity('Other', 2)))
  render(editor()); await waitFor(() => expect(saveButton()).toBeEnabled()); edit('Mine')
  await screen.findByRole('alert'); expect(saved()).toBe('Portal'); expect(saveButton()).toBeDisabled()
  expect(screen.queryByText('Assistant name saved.')).toBeNull(); expect(postCalls()).toHaveLength(1)
})

it('survives StrictMode cleanup and aborts outstanding reads on real unmount', async () => {
  const signals = []
  fetch.mockImplementation((_url, { signal }) => {
    signals.push(signal)
    if (signals.length === 2) return Promise.resolve(response(identity('Nova', 1)))
    return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new globalThis.DOMException('aborted', 'AbortError'))))
  })
  const view = render(<StrictMode>{editor()}</StrictMode>)
  await waitFor(() => expect(saved()).toBe('Nova')); expect(signals[0].aborted).toBe(true)
  refresh(); expect(signals).toHaveLength(3); view.unmount(); expect(signals[2].aborted).toBe(true)
})

it('keeps the deadline active while a successful response body is stalled', async () => {
  vi.useFakeTimers()
  let signal
  fetch.mockImplementation((_url, options) => {
    signal = options.signal
    return Promise.resolve(new globalThis.Response(new globalThis.ReadableStream({ start(controller) {
      signal.addEventListener('abort', () => controller.error(new globalThis.DOMException('aborted', 'AbortError')))
    } })))
  })
  render(editor()); await act(async () => { await Promise.resolve() })
  await act(async () => { await vi.advanceTimersByTimeAsync(15001) })
  expect(signal.aborted).toBe(true); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(saveButton()).toBeDisabled()
})

it('resets only after Save and independent remounts read the saved install name', async () => {
  let server = identity('Nova', 1)
  fetch.mockImplementation(async (_url, options) => {
    if (options.method === 'POST') server = identity(JSON.parse(options.body).displayName, server.revision + 1)
    return response(server)
  })
  let view = render(editor()); await waitFor(() => expect(saved()).toBe('Nova'))
  fireEvent.click(screen.getByRole('button', { name: 'Reset to Portal' })); expect(saved()).toBe('Nova'); expect(postCalls()).toHaveLength(0)
  fireEvent.click(saveButton()); await waitFor(() => expect(saved()).toBe('Portal'))
  view.unmount(); view = render(editor()); await waitFor(() => expect(saveButton()).toBeEnabled())
  expect(saved()).toBe('Portal'); expect(postCalls()).toHaveLength(1); view.unmount()
})

it('renders custom names as inert text in navigation and chat without changing routes', async () => {
  const name = '<img src=x onerror=alert(1)>'
  fetch.mockImplementation(async url => {
    if (url === '/api/pixel/identity') return response(identity(name, 1))
    if (url === '/api/pixel/status') return response({ available: true })
    if (url === '/api/external-links') return response([])
    return response({})
  })
  render(<PortalIdentityProvider><Sidebar status={{}}/><Pixel/></PortalIdentityProvider>, {
    wrapper: ({ children }) => <MemoryRouter initialEntries={['/pixel']}><ThemeProvider>{children}</ThemeProvider></MemoryRouter>,
  })
  expect(await screen.findByRole('heading', { name, exact: true })).toBeInTheDocument()
  expect(screen.getByRole('link', { name, exact: true })).toHaveAttribute('href', '/pixel')
  expect(await screen.findByPlaceholderText(`Message ${name}...`)).toBeEnabled()
  expect(document.querySelector('img[src="x"]')).toBeNull()
  expect(fetch.mock.calls.some(([url]) => url.includes(name))).toBe(false)
})
