import { StrictMode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '../../test/test-utils'
import PixelRuntimeSettings from './PixelRuntimeSettings'

// Persistence tests remain independent; actual composition is covered separately.
vi.mock('./PixelSettingsRuntime', () => ({ default: () => null }))

const doc = (preferences = {}, revision = 0) => ({
  configuration: { schemaVersion: 1, revision, preferences },
  runtime: { status: 'not-applied', reason: 'settings-runtime-not-integrated' },
})
const response = data => ({ ok: true, status: 200, json: async () => data })
const button = name => screen.getByRole('button', { name })
const save = () => button('Save assistant preferences')
const edit = (label, value) => fireEvent.change(screen.getByLabelText(label), { target: { value } })
const mount = async (fetchMock, strict = false) => {
  vi.stubGlobal('fetch', fetchMock)
  render(strict ? <StrictMode><PixelRuntimeSettings /></StrictMode> : <PixelRuntimeSettings />)
  await waitFor(() => expect(screen.getByLabelText('Reasoning visibility')).toBeEnabled())
}
const change = () => edit('Reasoning visibility', 'stream')
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

it('renders 20 accessible controls with automatic values and no write on a new install', async () => {
  const fetchMock = vi.fn(async () => response(doc()))
  await mount(fetchMock)
  expect(screen.getAllByRole('spinbutton').length + screen.getAllByRole('combobox').length).toBe(20)
  expect(screen.getByLabelText('Reasoning visibility')).toHaveValue('')
  expect(save()).toBeDisabled()
  expect(screen.getByText('Saving preferences does not apply them to the assistant.')).toBeVisible()
  expect(fetchMock.mock.calls.every(([, options]) => options.method !== 'POST')).toBe(true)
})

it('persists one sparse edit, then reads back explicit null reset', async () => {
  let current = doc({ verbosity: 'off' }, 4)
  const bodies = []
  const fetchMock = vi.fn(async (url, options) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body)
      bodies.push(body)
      current = doc({ ...current.configuration.preferences, ...body.changes }, body.expectedRevision + 1)
    }
    return response(current)
  })
  await mount(fetchMock)
  change(); fireEvent.click(save())
  expect(await screen.findByRole('status')).toHaveTextContent(/runtime.*unchanged/i)
  expect(bodies).toEqual([{ expectedRevision: 4, changes: { reasoningVisibility: 'stream' } }])
  edit('Reasoning visibility', ''); fireEvent.click(save())
  await waitFor(() => expect(bodies).toHaveLength(2))
  expect(bodies[1]).toEqual({ expectedRevision: 5, changes: { reasoningVisibility: null } })
  await waitFor(() => expect(save()).toBeDisabled())
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it.each(['conflict', 'network', 'revision', 'values', 'missing-reset', 'private'])('requires reload after %s even after Cancel', async kind => {
  const fetchMock = vi.fn(async (url, options) => {
    if (options.method !== 'POST') return response(doc({ reasoningVisibility: 'on' }))
    if (kind === 'conflict') return { ok: false, status: 409 }
    if (kind === 'network') throw new Error('disconnected')
    if (kind === 'revision') return response(doc({ reasoningVisibility: 'stream' }, 0))
    if (kind === 'values') return response(doc({ reasoningVisibility: 'off' }, 1))
    if (kind === 'missing-reset') return response(doc({}, 1))
    return response({ ...doc({ reasoningVisibility: 'stream' }, 1), private: true })
  })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  await mount(fetchMock)
  edit('Reasoning visibility', kind === 'missing-reset' ? '' : 'stream'); fireEvent.click(save())
  await screen.findByRole('alert')
  fireEvent.click(button('Cancel assistant edits'))
  change()
  expect(save()).toBeDisabled()
  fireEvent.click(button('Reload assistant preferences'))
  await waitFor(() => expect(screen.getByLabelText('Reasoning visibility')).toHaveValue('on'))
  change()
  expect(save()).toBeEnabled()
})

it('prevents duplicate writes and edits while a POST is pending', async () => {
  let resolvePost
  const pending = new Promise(resolve => { resolvePost = resolve })
  const fetchMock = vi.fn(async (url, options) => options.method === 'POST' ? pending : response(doc()))
  await mount(fetchMock)
  change(); fireEvent.click(save()); fireEvent.click(save())
  expect(screen.getByLabelText('Reasoning visibility')).toBeDisabled()
  expect(button('Cancel assistant edits')).toBeDisabled()
  expect(button('Reload assistant preferences')).toBeDisabled()
  expect(fetchMock.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1)
  await act(async () => resolvePost(response(doc({ reasoningVisibility: 'stream' }, 1))))
  expect(screen.getByRole('status')).toHaveTextContent(/runtime.*unchanged/i)
})

it('marks an aborted timed-out POST as uncertain and refuses another save', async () => {
  const fetchMock = vi.fn((url, options) => options.method !== 'POST' ? Promise.resolve(response(doc()))
    : new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new window.DOMException('Timed out', 'AbortError')))))
  await mount(fetchMock)
  vi.useFakeTimers()
  change(); fireEvent.click(save())
  await act(async () => vi.advanceTimersByTimeAsync(10000))
  expect(screen.getByRole('alert')).toHaveTextContent(/save failed/i)
  expect(save()).toBeDisabled()
  fireEvent.click(button('Cancel assistant edits')); change()
  expect(save()).toBeDisabled()
  expect(fetchMock.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1)
})

it('preserves edits when reload discard is declined and blocks saves after failed GET', async () => {
  let fail = false
  const fetchMock = vi.fn(async () => fail ? { ok: false, status: 503 } : response(doc()))
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  await mount(fetchMock)
  change(); fireEvent.click(button('Reload assistant preferences'))
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(screen.getByLabelText('Reasoning visibility')).toHaveValue('stream')
  window.confirm.mockReturnValue(true)
  fail = true
  fireEvent.click(button('Reload assistant preferences'))
  await screen.findByRole('alert')
  expect(save()).toBeDisabled()
})

it('ignores late first-mount GET after StrictMode reinitializes the request', async () => {
  let resolveOld
  const old = new Promise(resolve => { resolveOld = resolve })
  const fetchMock = vi.fn().mockImplementationOnce(() => old).mockResolvedValue(response(doc({ reasoningVisibility: 'on' }, 3)))
  await mount(fetchMock, true)
  await act(async () => resolveOld(response(doc({ reasoningVisibility: 'off' }, 1))))
  expect(screen.getByLabelText('Reasoning visibility')).toHaveValue('on')
})

it.each([null, {}, { configuration: { schemaVersion: 1, revision: 0, preferences: {} } }])('does not invent defaults for malformed initial GET', async data => {
  vi.stubGlobal('fetch', vi.fn(async () => response(data)))
  render(<PixelRuntimeSettings />)
  await screen.findByRole('alert')
  expect(save()).toBeDisabled()
})
