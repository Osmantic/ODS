import {webcrypto, createHash} from 'node:crypto'
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

const siteId = `site-${'a'.repeat(24)}`
const fileFor = (path, value) => ({path, sha256:createHash('sha256').update(value).digest('hex'), bytes:new TextEncoder().encode(value).byteLength})
const response = value => ({ok:true, arrayBuffer:async () => new TextEncoder().encode(value).buffer})
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:vi.fn().mockResolvedValue()}})
})
afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks()})

it('opens a clean rendered document and keeps exact verified Markdown available as source', async () => {
  const value = '# Project notes\r\n\r\nA **working** example.\r\n'
  const file = fileFor('docs/README.md', value)
  vi.stubGlobal('fetch', vi.fn(async () => response(value)))
  const {container} = render(<PixelPreviewSource workbench preview={{siteId,relativeDirectory:'Playground/notes'}} file={file}/>)
  expect(await screen.findByRole('heading', {name:'Project notes'})).toBeVisible()
  expect(screen.getByRole('navigation', {name:'File path'})).toHaveTextContent('PlaygroundnotesdocsREADME.md')
  expect(screen.getByRole('navigation', {name:'File path'})).toHaveAttribute('title','Playground/notes/docs/README.md')
  expect(fetch.mock.calls[0][0]).toBe(`/pixel-preview/${siteId}/docs/README.md`)
  expect(screen.queryByText(/SHA-256 verified/)).toBeNull()
  expect(screen.queryByRole('search')).toBeNull()
  expect(screen.queryByLabelText('Selected source excerpt')).toBeNull()
  fireEvent.click(screen.getByRole('button', {name:'View source'}))
  expect(container.querySelector('pre code').textContent).toBe(value)
  fireEvent.click(screen.getByRole('button', {name:'Copy code'}))
  await screen.findByRole('button', {name:'Copied code'})
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith(value)
  fireEvent.click(screen.getByRole('button', {name:'View rendered'}))
  expect(screen.getByRole('heading', {name:'Project notes'})).toBeVisible()
})

it('opens only safe relative paths and keeps Markdown HTML and images inert', async () => {
  const value = '# Local section\n[section](#local-section)\n[other](../src/main.js)\n[escape](../../outside.txt)\n[web](https://example.com/docs)\n[unsafe](javascript:alert%281%29)\n![remote](https://example.com/tracker.png)\n\n<script>alert(1)</script>\n<iframe src="https://example.com"></iframe>'
  const file = fileFor('docs/README.md', value)
  const open = vi.fn()
  vi.stubGlobal('fetch', vi.fn(async () => response(value)))
  const {container} = render(<PixelPreviewSource workbench preview={{siteId}} file={file} onOpenFile={open}/>)
  const heading = await screen.findByRole('heading', {name:'Local section'})
  expect(screen.getByRole('link', {name:'section'})).toHaveAttribute('href', `#${heading.id}`)
  fireEvent.click(screen.getByRole('button', {name:'other'}))
  expect(open).toHaveBeenCalledWith('src/main.js')
  expect(screen.queryByRole('button', {name:'escape'})).toBeNull()
  expect(screen.queryByRole('link', {name:'unsafe'})).toBeNull()
  expect(screen.getByRole('link', {name:'web'})).toHaveAttribute('rel', 'noopener noreferrer')
  expect(container.querySelector('script,iframe,img')).toBeNull()
  expect(fetch).toHaveBeenCalledOnce()
})

it('hides source utilities until requested and retains binary download support', async () => {
  const value = 'first\nsecond\n'
  vi.stubGlobal('fetch', vi.fn(async () => response(value)))
  const {rerender} = render(<PixelPreviewSource workbench preview={{siteId}} file={fileFor('notes.txt', value)}/>)
  await waitFor(() => expect(screen.getByRole('button', {name:'Copy code'})).toBeEnabled())
  expect(screen.queryByRole('search')).toBeNull()
  fireEvent.click(screen.getByLabelText('Source options'))
  fireEvent.click(screen.getByRole('button', {name:'Find in file'}))
  expect(screen.getByRole('search', {name:'Search verified source'})).toBeVisible()
  rerender(<PixelPreviewSource workbench preview={{siteId}} file={fileFor('asset.bin', value)}/>)
  expect(await screen.findByText('No text preview available')).toBeVisible()
  expect(screen.queryByRole('search')).toBeNull()
  expect(screen.getByRole('button', {name:'Download asset.bin'})).toBeEnabled()
})

it('never renders an unverified document and ignores stale clipboard completion after changing files', async () => {
  let finishCopy
  navigator.clipboard.writeText.mockReturnValue(new Promise(resolve => {finishCopy = resolve}))
  const value = '# First file'
  vi.stubGlobal('fetch', vi.fn(async () => response(value)))
  const {rerender} = render(<PixelPreviewSource workbench preview={{siteId}} file={fileFor('first.md', value)}/>)
  await screen.findByRole('heading', {name:'First file'})
  fireEvent.click(screen.getByRole('button', {name:'Copy code'}))
  rerender(<PixelPreviewSource workbench preview={{siteId}} file={fileFor('other.md', '# Other file')}/>)
  await act(async () => finishCopy())
  expect(await screen.findByRole('alert')).toHaveTextContent('could not be verified')
  expect(screen.queryByRole('heading')).toBeNull()
  expect(screen.queryByRole('button', {name:'Copied code'})).toBeNull()
  expect(screen.getByRole('button', {name:'Copy code'})).toBeDisabled()
})
