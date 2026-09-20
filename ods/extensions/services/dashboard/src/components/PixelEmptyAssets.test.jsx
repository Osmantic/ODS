import { webcrypto, createHash } from 'node:crypto'
import { Buffer } from 'node:buffer'
import { render, screen, fireEvent } from '@testing-library/react'
import PixelTaskFiles from './PixelTaskFiles'

const index = Buffer.from('<h1>Preview</h1>')
const empty = Buffer.alloc(0)
const digest = bytes => createHash('sha256').update(bytes).digest('hex')
const files = [
  {path:'index.html', bytes:index.length, sha256:digest(index)},
  {path:'style.css', bytes:0, sha256:digest(empty)},
]
const preview = {
  siteId:'site-'+'a'.repeat(24), sha256:'a'.repeat(64),
  entrySha256:files[0].sha256, files:2, bytes:index.length,
}
const manifestResponse = (receipt = preview, entries = files) => ({
  ok:true, arrayBuffer:async () => new TextEncoder().encode(JSON.stringify({
    schemaVersion:1, ...receipt, files:entries,
  })).buffer,
})

beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('fetch', vi.fn(async url => url.endsWith('__ods_manifest__.json')
    ? manifestResponse()
    : {ok:true, arrayBuffer:async () => empty.buffer}))
})
afterEach(() => {vi.restoreAllMocks(); vi.unstubAllGlobals()})

it('lists, verifies and downloads an empty support asset', async () => {
  const createUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:empty-asset')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  render(<PixelTaskFiles preview={preview}/>)
  fireEvent.click(await screen.findByRole('button', {name:/style.css/}))
  expect(await screen.findByText('Published snapshot · SHA-256 verified')).toBeVisible()
  expect(screen.getByLabelText('Code for style.css').textContent).toBe('')
  fireEvent.click(screen.getByRole('button', {name:'Download style.css'}))
  await screen.findByText('Verified download started')
  expect(createUrl.mock.calls[0][0].size).toBe(0)
  expect(click).toHaveBeenCalledOnce()
})

it('continues to reject an empty entry document', async () => {
  const receipt = {...preview, bytes:0, entrySha256:digest(empty)}
  fetch.mockResolvedValue(manifestResponse(receipt, [
    {...files[0], bytes:0, sha256:digest(empty)}, files[1],
  ]))
  render(<PixelTaskFiles preview={receipt}/>)
  await screen.findByText(/Task files could not be verified/)
  expect(screen.queryByRole('button', {name:/style.css/})).toBeNull()
})
