import {createHash} from 'node:crypto'
import {Buffer} from 'node:buffer'
import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

// Valid 1x1 RGB PNG, generated with standard PNG chunks and CRCs.
const png = Uint8Array.from(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgaPgPAAIDAYAkYfWXAAAAAElFTkSuQmCC', 'base64'))
const hash = data => createHash('sha256').update(data).digest('hex')
const preview = {siteId:'site-'+'a'.repeat(24),entrySha256:'b'.repeat(64)}
const file = {path:'assets/logo.png',sha256:hash(png),bytes:png.length}
let fetched, blobs
beforeEach(() => {
  blobs = []
  fetched = png
  vi.stubGlobal('fetch',vi.fn(async () => ({ok:true,arrayBuffer:async () => fetched.buffer})))
  vi.stubGlobal('URL', Object.assign(URL, {createObjectURL:vi.fn(blob => {blobs.push(blob); return `blob:verified-${blobs.length}`}),revokeObjectURL:vi.fn()}))
})
afterEach(() => {vi.restoreAllMocks();vi.unstubAllGlobals()})
test('displays only verified bytes after an explicit action, then releases its URL on hide', async () => {
  render(<PixelPreviewSource preview={preview} file={file}/>)
  const show = await screen.findByRole('button', {name:'Show verified image'})
  expect(screen.queryByRole('img')).toBeNull()
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  fireEvent.click(show)
  const image = await screen.findByRole('img', {name:'Published image: assets/logo.png'})
  expect(image).toHaveAttribute('src','blob:verified-1')
  expect(blobs[0].type).toBe('image/png')
  expect(blobs[0].size).toBe(png.length)
  expect(fetch).toHaveBeenCalledTimes(1)
  Object.defineProperties(image, {naturalWidth:{value:1},naturalHeight:{value:1}})
  fireEvent.load(image)
  expect(screen.getByText('1 × 1 pixels · verified published image')).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Hide image preview'}))
  expect(screen.queryByRole('img')).toBeNull()
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:verified-1')
})
test('never constructs image URLs for hash-mismatched bytes or executable SVG source', async () => {
  fetched = new Uint8Array([0,1])
  const view = render(<PixelPreviewSource preview={preview} file={file}/>)
  await screen.findByRole('alert')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  expect(screen.queryByRole('button',{name:'Show verified image'})).toBeNull()
  fetched = new TextEncoder().encode('<svg onload="bad()"></svg>')
  view.rerender(<PixelPreviewSource preview={preview} file={{path:'logo.svg',sha256:hash(fetched),bytes:fetched.length}}/>)
  await waitFor(() => expect(screen.getByRole('button',{name:'Copy code'})).toBeEnabled())
  expect(screen.queryByRole('img')).toBeNull()
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})
test('reports decoder failure separately from byte verification and retains file download', async () => {
  render(<PixelPreviewSource preview={preview} file={file}/>)
  fireEvent.click(await screen.findByRole('button',{name:'Show verified image'}))
  fireEvent.error(await screen.findByRole('img'))
  expect(screen.getByRole('alert')).toHaveTextContent('bytes are verified')
  expect(screen.getByRole('button',{name:'Download assets/logo.png'})).toBeEnabled()
  fireEvent.click(screen.getByRole('button',{name:'Hide image preview'}))
  fireEvent.click(screen.getByRole('button',{name:'Show verified image'}))
  expect(await screen.findByRole('img')).toHaveAttribute('src','blob:verified-2')
})
test('releases the displayed image on file changes and waits for the newly selected file', async () => {
  const view = render(<PixelPreviewSource preview={preview} file={file}/>)
  fireEvent.click(await screen.findByRole('button',{name:'Show verified image'}))
  await screen.findByRole('img')
  fetched = new TextEncoder().encode('plain source')
  view.rerender(<PixelPreviewSource preview={preview} file={{path:'notes.txt',sha256:hash(fetched),bytes:fetched.length}}/>)
  await waitFor(() => expect(screen.getByRole('button',{name:'Copy code'})).toBeEnabled())
  expect(screen.queryByRole('img')).toBeNull()
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:verified-1')
})
