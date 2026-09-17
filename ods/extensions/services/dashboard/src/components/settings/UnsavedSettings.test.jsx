import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../../test/test-utils'
import EnvEditor from './EnvEditor'
import PixelProviderSettings from './PixelProviderSettings'
import PixelRuntimeSettings from './PixelRuntimeSettings'

vi.mock('./PixelSettingsRuntime', () => ({ default: () => null }))
vi.mock('./PixelProviderRuntime', () => ({ default: () => null }))
vi.mock('./PixelConnectionImport', () => ({ default: () => null }))

const unloadBlocked = () => {
  const event = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(event)
  return event.defaultPrevented
}
const response = (configuration, runtime = { status: 'not-applied' }) => ({ ok: true, status: 200,
  json: async () => ({ configuration, runtime }) })
const providerDoc = () => ({ schemaVersion: 1, revision: 0, enabled: false, providers: [],
  roles: { leader: null, backups: [], advisor: null, handoff: null },
  policy: { allowCloud: false, maxAttempts: 3, deadlineSeconds: 120 } })
const envProps = { search: '', sections: [], fields: {}, values: {}, issues: [], issueMap: {}, revealedSecrets: {},
  onSearchChange: () => {}, onSectionChange: () => {}, dirty: false, saving: false }

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('environment drafts protect reload until saved, including a pending save', () => {
  const { rerender, unmount } = render(<EnvEditor {...envProps} />)
  expect(unloadBlocked()).toBe(false)
  rerender(<EnvEditor {...envProps} dirty />)
  expect(unloadBlocked()).toBe(true)
  rerender(<EnvEditor {...envProps} saving />)
  expect(unloadBlocked()).toBe(true)
  rerender(<EnvEditor {...envProps} />)
  expect(unloadBlocked()).toBe(false)
  rerender(<EnvEditor {...envProps} dirty />)
  unmount()
  expect(unloadBlocked()).toBe(false)
})

test('one clean editor cannot release another editor with an unsaved draft', () => {
  const first = render(<EnvEditor {...envProps} dirty />)
  const second = render(<EnvEditor {...envProps} dirty />)
  first.rerender(<EnvEditor {...envProps} />)
  expect(unloadBlocked()).toBe(true)
  second.unmount()
  expect(unloadBlocked()).toBe(false)
})

test('provider creation fields are protected before Add, and successful save releases the warning', async () => {
  let finishSave
  const pending = new Promise(resolve => { finishSave = resolve })
  const fetchMock = vi.fn((_url, options) => options.method === 'POST' ? pending : Promise.resolve(response(providerDoc())))
  vi.stubGlobal('fetch', fetchMock)
  const { unmount } = render(<PixelProviderSettings />)
  const id = await screen.findByLabelText('New provider ID')
  await waitFor(() => expect(id).toBeEnabled())
  expect(unloadBlocked()).toBe(false)
  fireEvent.change(id, { target: { value: 'new-peer' } })
  expect(unloadBlocked()).toBe(true)
  fireEvent.change(id, { target: { value: '' } })
  expect(unloadBlocked()).toBe(false)
  fireEvent.change(screen.getByLabelText('New provider label'), { target: { value: 'New peer' } })
  expect(unloadBlocked()).toBe(true)
  fireEvent.change(screen.getByLabelText('New provider label'), { target: { value: '' } })
  fireEvent.click(screen.getByLabelText('Allow cloud inference'))
  expect(unloadBlocked()).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Save providers' }))
  expect(unloadBlocked()).toBe(true)
  await act(async () => { finishSave(response({ ...providerDoc(), revision: 1,
    policy: { ...providerDoc().policy, allowCloud: true } })) })
  expect(unloadBlocked()).toBe(false)
  expect(fetchMock.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1)
  unmount()
  expect(unloadBlocked()).toBe(false)
})

test('Pixel preferences keep the warning after a failed save and release it after explicit Cancel', async () => {
  vi.stubGlobal('fetch', vi.fn(async (_url, options) => options.method === 'POST'
    ? { ok: false, status: 503 }
    : response({ schemaVersion: 1, revision: 0, preferences: {} },
      { status: 'not-applied', reason: 'settings-runtime-not-integrated' })))
  render(<PixelRuntimeSettings />)
  const field = await screen.findByLabelText('Reasoning visibility')
  await waitFor(() => expect(field).toBeEnabled())
  expect(unloadBlocked()).toBe(false)
  fireEvent.change(field, { target: { value: 'stream' } })
  expect(unloadBlocked()).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Save Portal preferences' }))
  await screen.findByRole('alert')
  expect(unloadBlocked()).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Cancel Portal edits' }))
  expect(unloadBlocked()).toBe(false)
})
