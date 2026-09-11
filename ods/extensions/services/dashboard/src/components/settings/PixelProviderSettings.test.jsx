import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../../test/test-utils'
import PixelProviderSettings from './PixelProviderSettings.jsx'
import { runtimeDoc } from './pixelProviderRuntimeFixtures'

const empty = () => ({ schemaVersion: 1, revision: 0, enabled: false, providers: [],
  roles: { leader: null, backups: [], advisor: null, handoff: null },
  policy: { allowCloud: false, maxAttempts: 3, deadlineSeconds: 120 } })
const tower = () => ({ ...empty(), providers: [{ id: 'tower', label: 'Tower', kind: 'ods-peer',
  baseUrl: 'https://tower.example/v1', model: 'glm', contextTokens: 32768, maxOutputTokens: 4096,
  supportsTools: true, supportsVision: false, reasoning: false, hasCredential: true, enabled: true }] })
const response = (configuration, status = 200) => ({ ok: status === 200, status,
  json: async () => ({ configuration, runtime: { status: 'not-applied' } }) })
const adviceReadiness = () => ({ status: 'not-configured', revision: 0, host: 'canary',
  sourceSha256: null, runtimeId: null, candidates: [], job: null })
function setup(doc, post = async () => response({ ...doc, revision: doc.revision + 1 })) {
  const fetchMock = vi.fn(async (url, options) => {
    if (url === '/api/pixel/providers/runtime') return { ok: true, status: 200, json: async () => runtimeDoc('unavailable') }
    if (url === '/api/pixel/providers/save') return post(options)
    if (url === '/api/pixel/advice-runtime') return { ok: true, status: 200, json: async () => adviceReadiness() }
    if (url === '/api/pixel/providers') return response(doc)
    throw new Error(`Unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<PixelProviderSettings />)
  return fetchMock
}
const loaded = () => screen.findByLabelText('Model')
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })

it('renders a pristine install without claiming runtime activation', async () => {
  setup(empty())
  expect(await screen.findByText('No providers configured.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save providers' })).toBeDisabled()
  expect(screen.getByText('Saving providers does not apply them. Inspect the current runtime below before making a change.')).toBeInTheDocument()
})

it('shows unavailable for failed or malformed GET instead of fabricated defaults', async () => {
  setup({})
  expect(await screen.findByRole('alert')).toHaveTextContent('unavailable')
  expect(screen.getByRole('button', { name: 'Save providers' })).toBeDisabled()
})

it('allows a local tower to be selected as leader', async () => {
  setup(tower())
  await loaded()
  const select = screen.getByRole('combobox', { name: 'Leader', exact: true })
  fireEvent.change(select, { target: { value: 'tower' } })
  expect(select).toHaveValue('tower')
})

it('clears the key before POST completes, prevents duplicate saves and preserves the public document', async () => {
  let resolve
  const pending = new Promise(done => { resolve = done })
  const doc = tower()
  const fetchMock = setup(doc, () => pending)
  await loaded()
  const key = screen.getByLabelText('API key (write-only)')
  fireEvent.change(key, { target: { value: 'synthetic-only-key' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save providers' }))
  expect(key).toHaveValue('')
  expect(key).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Saving providers…' }))
  const writes = fetchMock.mock.calls.filter(([url]) => url.endsWith('/save'))
  expect(writes).toHaveLength(1)
  const body = JSON.parse(writes[0][1].body)
  expect(body.credentialChanges).toEqual({ tower: { action: 'set', value: 'synthetic-only-key' } })
  expect(body.document).toEqual(doc)
  expect(JSON.stringify(body.document)).not.toContain('synthetic-only-key')
  resolve(response({ ...doc, revision: 1 }))
  expect(await screen.findByText('Settings saved. Pixel runtime has not been changed.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save providers' })).toBeDisabled()
})

it('requires a successful reload after conflict even if edits are cancelled', async () => {
  const fetchMock = setup(tower(), async () => response(null, 409))
  await loaded()
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'new-model' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save providers' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Settings changed elsewhere')
  fireEvent.click(screen.getByRole('button', { name: 'Cancel provider edits' }))
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'another-model' } })
  expect(screen.getByRole('button', { name: 'Save providers' })).toBeDisabled()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  fireEvent.click(screen.getByRole('button', { name: 'Reload providers' }))
  await waitFor(() => expect(screen.getByLabelText('Model')).toHaveValue('glm'))
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'after-reload' } })
  expect(screen.getByRole('button', { name: 'Save providers' })).toBeEnabled()
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/save'))).toHaveLength(1)
})

it('blocks sending an existing key to a changed endpoint without explicit replacement or removal', async () => {
  const fetchMock = setup(tower())
  await loaded()
  fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://new.example/v1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save providers' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('requires replacing or removing')
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/save'))).toHaveLength(0)
})

it('treats an ambiguous POST failure as reload-required without retrying or retaining the key', async () => {
  const fetchMock = setup(tower(), async () => { throw new Error('private transport detail') })
  await loaded()
  fireEvent.change(screen.getByLabelText('API key (write-only)'), { target: { value: 'synthetic-only-key' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save providers' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Save result is unknown')
  expect(screen.getByLabelText('API key (write-only)')).toHaveValue('')
  expect(screen.getByRole('button', { name: 'Save providers' })).toBeDisabled()
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/save'))).toHaveLength(1)
})
