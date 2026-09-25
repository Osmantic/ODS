import { webcrypto, createHash } from 'node:crypto'
import { Buffer } from 'node:buffer'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

// Node 20 WebCrypto requires a buffer from its own realm, like fetch supplies.
const data = Buffer.alloc(5)
data.set([0, 255, 13, 10, 42])
const file = { path: 'assets/result.bin', bytes: data.length, sha256: createHash('sha256').update(data).digest('hex') }
const preview = { siteId: 'site-' + 'a'.repeat(24) }
let blobs, clicks
beforeEach(() => {
  blobs = []; clicks = []
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true, arrayBuffer:async () => data.buffer})))
  vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => { blobs.push(blob); return 'blob:download' })
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () { clicks.push({name:this.download, href:this.href}) })
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('downloads original verified binary bytes from the public source control', async () => {
  render(<PixelPreviewSource preview={preview} file={file}/>)
  fireEvent.click(screen.getByRole('button', {name:'Download assets/result.bin'}))
  await screen.findByText('Verified download started')
  expect(clicks).toEqual([{name:'result.bin', href:'blob:download'}])
  expect(blobs[0].type).toBe('application/octet-stream')
  const bytes = await new Promise(resolve => { const reader = new globalThis.FileReader(); reader.onload = () => resolve(new Uint8Array(reader.result)); reader.readAsArrayBuffer(blobs[0]) })
  expect([...bytes]).toEqual([...data])
  expect(document.querySelector('a[download]')).toBeNull()
  await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:download'), {timeout:2000})
})

it('rejects a tampered file and allows an explicit retry', async () => {
  fetch.mockResolvedValue({ok:true, arrayBuffer:async () => new Uint8Array([1]).buffer})
  render(<PixelPreviewSource preview={preview} file={file}/>)
  fireEvent.click(screen.getByRole('button', {name:'Download assets/result.bin'}))
  await screen.findByText('Download could not be verified. Try again.')
  expect(clicks).toEqual([])
  fetch.mockResolvedValue({ok:true, arrayBuffer:async () => data.buffer})
  fireEvent.click(screen.getByRole('button', {name:'Download assets/result.bin'}))
  await screen.findByText('Verified download started')
  expect(clicks).toHaveLength(1)
})

it('deduplicates clicks and discards a download when its source is replaced', async () => {
  let finish
  fetch.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const {rerender} = render(<PixelPreviewSource preview={preview} file={file}/>)
  const button = screen.getByRole('button', {name:'Download assets/result.bin'})
  fireEvent.click(button); fireEvent.click(button)
  expect(fetch).toHaveBeenCalledTimes(2) // source inspection + one download
  const resolveDownload = finish
  rerender(<PixelPreviewSource preview={{siteId:'site-'+'b'.repeat(24)}} file={file}/>)
  resolveDownload({ok:true, arrayBuffer:async () => data.buffer})
  await waitFor(() => expect(screen.getByRole('button', {name:'Download assets/result.bin'})).toBeEnabled())
  expect(clicks).toEqual([])
})
