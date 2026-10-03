import { webcrypto, createHash } from 'node:crypto'
import { render, screen, waitFor } from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'
const source = '<!doctype html><button onclick="alert(1)">Click</button>\n```\n<script>bad()</script>'
const preview = { siteId:'site-'+'a'.repeat(24), entrySha256:createHash('sha256').update(source).digest('hex') }
beforeEach(() => { vi.stubGlobal('crypto', webcrypto) })
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
it('verifies exact snapshot bytes and renders HTML as inert highlighted code', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,arrayBuffer:async () => new TextEncoder().encode(source).buffer})))
  const {container} = render(<PixelPreviewSource preview={preview}/>)
  await waitFor(() => expect(screen.getByRole('button',{name:'Copy code'})).toBeEnabled())
  expect(container.querySelector('pre code').textContent).toContain(source)
  expect(container.querySelector('.pixel-code-block .code-language-badge')).toHaveTextContent('HTML')
  expect(container.querySelectorAll('.code-line')).toHaveLength(source.split('\n').length)
  expect(container.querySelectorAll('.code-line')[1]).toHaveAttribute('data-line','2')
  expect(container.querySelector('script')).toBeNull()
  expect(screen.queryByRole('button',{name:'Click'})).toBeNull()
  expect(fetch).toHaveBeenCalledWith(`/pixel-preview/${preview.siteId}/`,expect.objectContaining({cache:'no-store'}))
})
it('does not show mismatching or failed snapshot content', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,arrayBuffer:async () => new TextEncoder().encode('wrong content').buffer})))
  const {container} = render(<PixelPreviewSource preview={preview}/>)
  expect(await screen.findByRole('alert')).toHaveTextContent('could not be verified')
  expect(container.querySelector('pre')).toBeNull()
  expect(screen.getByRole('button',{name:'Copy code'})).toBeDisabled()
})
it('rejects arbitrary source destinations before fetching', async () => {
  vi.stubGlobal('fetch',vi.fn())
  render(<PixelPreviewSource preview={{...preview,siteId:'../../settings'}}/>)
  expect(await screen.findByRole('alert')).toBeVisible()
  expect(fetch).not.toHaveBeenCalled()
})

it.each(['oversized', 'failed'])('cancels an unread %s response when source verification rejects it', async reason => {
  const cancel = vi.fn().mockResolvedValue(undefined)
  const getReader = vi.fn()
  const arrayBuffer = vi.fn()
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: reason !== 'failed',
    headers: new globalThis.Headers({'Content-Length': String(reason === 'oversized' ? 4 * 1024 * 1024 + 1 : 512)}),
    body: {cancel, getReader}, arrayBuffer,
  })))
  render(<PixelPreviewSource preview={preview}/>)
  expect(await screen.findByRole('alert')).toHaveTextContent('could not be verified')
  expect(cancel).toHaveBeenCalledOnce()
  expect(getReader).not.toHaveBeenCalled()
  expect(arrayBuffer).not.toHaveBeenCalled()
  expect(screen.getByRole('button',{name:'Copy code'})).toBeDisabled()
})

it.each([
  ['index.html','<p>First</p>\n\n'],
  ['index.html','<p>First</p>\r\n<p>Second</p>\r\n'],
  ['notes.txt','First\n\nLast\n'],
  ['notes.txt','\n'],
])('preserves verified whitespace when manually selecting %s source',async (path,text) => {
  const bytes=new TextEncoder().encode(text)
  const digest=createHash('sha256').update(bytes).digest('hex')
  vi.stubGlobal('fetch',vi.fn(async () => ({ok:true,arrayBuffer:async () => bytes.buffer})))
  const {container}=render(<PixelPreviewSource preview={{...preview,entrySha256:digest}} file={{path,sha256:digest,bytes:bytes.byteLength}}/>)
  await waitFor(() => expect(screen.getByRole('button',{name:'Copy code'})).toBeEnabled())
  expect(container.querySelector('pre code').textContent).toBe(text)
})
