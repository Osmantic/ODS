// Opening a shipped example from the sidebar shows its game with no model run.
// The origin handshake has its own tests (previewOrigin.test.jsx).
vi.mock('../lib/useVerifiedPreview', () => ({default: (_preview, access) => access}))
vi.mock('../lib/pixelArtifacts', async importOriginal => ({
  ...await importOriginal(),
  loadSnapshotFiles: vi.fn(async preview => [{path: 'index.html', bytes: preview.bytes, sha256: preview.entrySha256}]),
}))
import {render} from '../test/test-utils'
import {act, screen} from '@testing-library/react'
import Pixel from './Pixel'
import {EXAMPLE_OPEN_EVENT, EXAMPLE_REFRESH_EVENT} from '../lib/exampleProjects'
import {ODS_HELP_DISCORD_URL} from '../lib/support'
import {saveConversation, SELECT_EVENT} from '../lib/pixelConversations'

const sha256 = 'c'.repeat(64), siteId = `site-${sha256.slice(0, 24)}`
const preview = {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', relativeDirectory: 'Playground/elf-of-doom', siteId, sha256,
  entrySha256: 'f'.repeat(64), files: 9, bytes: 243500, port: 9437, url: `http://${siteId}.localhost:9437/${siteId}/`}

function host(open) {
  return vi.fn(async url => url === '/api/pixel/example-projects/open'
    ? open()
    : {ok: true, status: 200, json: async () => ({available: true, model: 'pixel/default'})})
}
const reply = (status, body) => () => ({ok: status === 200, status, json: async () => body})
const openExample = () => act(async () => { window.dispatchEvent(new CustomEvent(EXAMPLE_OPEN_EVENT, {detail: {name: 'elf-of-doom'}})) })

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear() })

it('opens the example in the Workspace preview without sending anything to the model', async () => {
  localStorage.clear()
  vi.stubGlobal('fetch', host(reply(200, {schemaVersion: 1, preview})))
  render(<Pixel/>)
  await screen.findByText('Available')
  await openExample()
  expect(await screen.findByTitle('Interactive Portal preview')).toHaveAttribute('src', `/pixel-preview/${siteId}/__ods_view__.html`)
  const opened = fetch.mock.calls.find(([url]) => url === '/api/pixel/example-projects/open')
  expect(JSON.parse(opened[1].body)).toEqual({name: 'elf-of-doom'})
  expect(fetch.mock.calls.some(([url]) => url === '/api/pixel/chat/stream')).toBe(false)
  expect(screen.queryByRole('alert')).toBeNull()
})

it('refuses a receipt for any other folder, exactly like a model-run receipt', async () => {
  localStorage.clear()
  vi.stubGlobal('fetch', host(reply(200, {schemaVersion: 1, preview: {...preview, relativeDirectory: 'Playground/other'}})))
  render(<Pixel/>)
  await screen.findByText('Available')
  await openExample()
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('Portal could not open elf-of-doom: its preview was not published. Check that Portal is ready, then click the project again.')
  expect(screen.queryByTitle('Interactive Portal preview')).toBeNull()
})

it('a deleted folder explains itself, refreshes the list and links to help', async () => {
  localStorage.clear()
  vi.stubGlobal('fetch', host(reply(404, {detail: 'This example project is no longer in your Portal workspace.'})))
  const refreshed = vi.fn()
  window.addEventListener(EXAMPLE_REFRESH_EVENT, refreshed)
  render(<Pixel/>)
  await screen.findByText('Available')
  // Portal becoming available already asked for one check.
  refreshed.mockClear()
  await openExample()
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('elf-of-doom is no longer in your Portal workspace, so it cannot open. If you renamed or moved it, ask Portal to open it by its new folder name.')
  expect(alert).not.toHaveTextContent('fresh copy')
  expect(alert.querySelector('a')).toHaveAttribute('href', ODS_HELP_DISCORD_URL)
  expect(refreshed).toHaveBeenCalledTimes(1)
  expect(fetch.mock.calls.some(([url]) => url === '/api/pixel/chat/stream')).toBe(false)
  window.removeEventListener(EXAMPLE_REFRESH_EVENT, refreshed)
})

it('files the host refuses get their own cause and a way to fix them', async () => {
  localStorage.clear()
  vi.stubGlobal('fetch', host(reply(409, {detail: "Some files in this example project's folder cannot be previewed."})))
  render(<Pixel/>)
  await screen.findByText('Available')
  await openExample()
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('Portal could not open elf-of-doom: some files in Playground/elf-of-doom cannot be previewed. To fix them, ask Portal: "In elf-of-doom, fix the preview".')
  expect(alert).not.toHaveTextContent('click the project again')
  expect(alert.querySelector('a')).toHaveAttribute('href', ODS_HELP_DISCORD_URL)
})

it('the example error leaves with its task: a new task or another conversation clears it', async () => {
  localStorage.clear()
  saveConversation({schema: 1, chatId: 'snake', messages: [{role: 'user', content: 'Build a snake game'}, {role: 'assistant', content: 'Done.', status: 'done'}]})
  vi.stubGlobal('fetch', host(reply(404, {detail: 'gone'})))
  render(<Pixel/>)
  await screen.findByText('Available')
  await openExample()
  expect(await screen.findByRole('alert')).toHaveTextContent('elf-of-doom is no longer in your Portal workspace')
  await act(async () => { window.dispatchEvent(new Event('ods:pixel-new-task')) })
  expect(screen.queryByRole('alert')).toBeNull()
  await openExample()
  expect(await screen.findByRole('alert')).toHaveTextContent('elf-of-doom is no longer in your Portal workspace')
  await act(async () => { window.dispatchEvent(new CustomEvent(SELECT_EVENT, {detail: 'snake'})) })
  expect(await screen.findByText('Build a snake game')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
})

it('Portal becoming available asks the sidebar to check its example list again', async () => {
  localStorage.clear()
  vi.stubGlobal('fetch', host(reply(200, {schemaVersion: 1, preview})))
  const refreshed = vi.fn()
  window.addEventListener(EXAMPLE_REFRESH_EVENT, refreshed)
  render(<Pixel/>)
  await screen.findByText('Available')
  expect(refreshed).toHaveBeenCalled()
  window.removeEventListener(EXAMPLE_REFRESH_EVENT, refreshed)
})
