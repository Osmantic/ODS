import { webcrypto, createHash } from 'node:crypto'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import PixelTaskFiles from './PixelTaskFiles'
import { loadSnapshotFiles, readBoundedBytes } from '../lib/pixelArtifacts'
const hash = value => createHash('sha256').update(value).digest('hex')
const html = '<script src="assets/app.js"></script>'
const js = 'console.log("original source")'
const files = [{path:'assets/app.js', bytes:js.length, sha256:hash(js)}, {path:'index.html', bytes:html.length, sha256:hash(html)}]
const preview = {siteId:'site-'+'a'.repeat(24),sha256:'a'.repeat(64),entrySha256:hash(html),files:2,bytes:html.length+js.length,relativeDirectory:'public'}
const manifest = {schemaVersion:1,siteId:preview.siteId,sha256:preview.sha256,bytes:preview.bytes,files}
const response = body => ({ok:true,arrayBuffer:async () => new TextEncoder().encode(body).buffer})
beforeEach(() => { vi.stubGlobal('crypto',webcrypto) })
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
it('filters all published files, opens verified nested source and returns to the list', async () => {
  vi.stubGlobal('fetch',vi.fn(async url => response(url.endsWith('json') ? JSON.stringify(manifest) : js)))
  const {container} = render(<PixelTaskFiles preview={preview}/>)
  await screen.findByRole('button',{name:/assets\/app.js/})
  fireEvent.change(screen.getByRole('searchbox', {name:'Filter task files'}),{target:{value:'app.js'}})
  expect(screen.queryByRole('button',{name:/index.html/})).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:/assets\/app.js/}))
  await waitFor(() => expect(screen.getByRole('button',{name:'Copy code'})).toBeEnabled())
  expect(container.querySelector('pre').textContent).toContain(js)
  expect(screen.getByRole('button',{name:/^assets\/app.js/})).toHaveAttribute('aria-current','true')
  expect(screen.getByRole('searchbox', {name:'Filter task files'})).toHaveValue('app.js')

  expect(fetch).toHaveBeenLastCalledWith(`/pixel-preview/${preview.siteId}/assets/app.js`,expect.objectContaining({cache:'no-store'}))
  fireEvent.click(screen.getByRole('button',{name:'All files'}))
  expect(screen.getByRole('searchbox', {name:'Filter task files'})).toHaveValue('app.js')
})
it.each([
  {...manifest,sha256:'b'.repeat(64)},
  {...manifest,files:[{...files[0],path:'../secret'},files[1]]},
  {...manifest,files:[files[1],files[1]]},
  {...manifest,files:[files[0],{...files[1],sha256:'b'.repeat(64)}]},
  {...manifest,files:[{...files[0],bytes:999},files[1]]},
])('rejects mismatching, escaping, duplicate or invalid manifest metadata', async value => {
  vi.stubGlobal('fetch',vi.fn(async () => response(JSON.stringify(value))))
  await expect(loadSnapshotFiles(preview)).rejects.toThrow()
})
it('shows a retry state without invented files when the host is unavailable', async () => {
  vi.stubGlobal('fetch',vi.fn(async () => {throw new Error('offline')}))
  render(<PixelTaskFiles preview={preview}/>)
  expect(await screen.findByRole('alert')).toHaveTextContent('could not be verified')
  expect(screen.queryByRole('button',{name:/index.html/})).toBeNull()
  fetch.mockResolvedValue(response(JSON.stringify(manifest)))
  fireEvent.click(screen.getByRole('button',{name:'Try again'}))
  expect(await screen.findByRole('button',{name:/index.html/})).toBeVisible()
})
it('cancels a response as soon as its byte limit is exceeded', async () => {
  const cancel = vi.fn(), releaseLock = vi.fn()
  const stream = {ok:true,body:{getReader:() => ({read:async () => ({value:new Uint8Array(20),done:false}),cancel,releaseLock})}}
  await expect(readBoundedBytes(stream,10)).rejects.toThrow('Oversized')
  expect(cancel).toHaveBeenCalledOnce(); expect(releaseLock).toHaveBeenCalledOnce()
})

it('opens host-published files nested deeper than the project-directory selector limit', async () => {
  const path = Array.from({length:14}, (_, i) => `level${i}`).join('/') + '/app.js'
  const deepManifest = {...manifest, files:[{...files[0], path}, files[1]]}
  vi.stubGlobal('fetch', vi.fn(async url => response(url.endsWith('__ods_manifest__.json')
    ? JSON.stringify(deepManifest) : js)))
  render(<PixelTaskFiles preview={preview}/>)
  fireEvent.click(await screen.findByRole('button', {name:name => name.includes(path)}))
  await waitFor(() => expect(screen.getByRole('button', {name:'Copy code'})).toBeEnabled())
  expect(screen.getByLabelText(`Code for ${path}`).textContent).toContain(js)
  expect(fetch).toHaveBeenLastCalledWith(`/pixel-preview/${preview.siteId}/${path}`,
    expect.objectContaining({cache:'no-store'}))
})

it.each(['level/../app.js', '/level/app.js', 'level//app.js', 'level/%2e%2e/app.js'])(
  'still rejects unsafe manifest paths: %s', async path => {
    vi.stubGlobal('fetch', vi.fn(async () => response(JSON.stringify({
      ...manifest, files:[{...files[0], path}, files[1]],
    }))))
    await expect(loadSnapshotFiles(preview)).rejects.toThrow()
  })
