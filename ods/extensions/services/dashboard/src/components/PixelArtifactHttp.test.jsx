import {createHash} from 'node:crypto'
import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'
import {loadArtifactBytes} from '../lib/pixelArtifacts'

const source = '<h1>Verified over LAN</h1>\n'
const bytes = new TextEncoder().encode(source)
const digest = value => createHash('sha256').update(value).digest('hex')
const preview = {siteId:'site-'+'a'.repeat(24), entrySha256:digest(bytes)}
beforeEach(() => {
  // HTTP LAN exposes crypto.getRandomValues, but no SubtleCrypto.
  vi.stubGlobal('crypto', {})
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true, arrayBuffer:async () => bytes.buffer})))
})
afterEach(() => {vi.restoreAllMocks(); vi.unstubAllGlobals()})

it('verifies source and downloads the original bytes without SubtleCrypto', async () => {
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:verified')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  render(<PixelPreviewSource preview={preview}/> )
  expect(await screen.findByLabelText('Code for index.html')).toHaveTextContent('Verified over LAN')
  fireEvent.click(screen.getByRole('button', {name:'Download index.html'}))
  await screen.findByText('Verified download started')
  expect(click).toHaveBeenCalledOnce()
  await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalled(), {timeout:2000})
})

it('still rejects tampered bytes without SubtleCrypto', async () => {
  render(<PixelPreviewSource preview={{...preview, entrySha256:'0'.repeat(64)}}/> )
  expect(await screen.findByRole('alert')).toHaveTextContent('could not be verified')
  expect(screen.queryByLabelText('Code for index.html')).toBeNull()
})

it.each([0, 1, 55, 56, 63, 64, 65, 1024, 4 * 1024 * 1024])('matches independently hashed artifact bytes at length %i', async length => {
  const data = Uint8Array.from({length}, (_, index) => index % 251)
  fetch.mockResolvedValue({ok:true, arrayBuffer:async () => data.buffer})
  const file = {path:'asset.bin', bytes:length, sha256:digest(data)}
  expect(await loadArtifactBytes(preview, file)).toBe(data.buffer)
})
