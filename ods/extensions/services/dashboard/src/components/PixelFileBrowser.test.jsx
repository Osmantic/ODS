import {render, screen, fireEvent} from '@testing-library/react'
import PixelTaskFiles from './PixelTaskFiles'

const files = [
  {path:'index.html', bytes:20, sha256:'a'.repeat(64)},
  {path:'assets/app10.js', bytes:100, sha256:'b'.repeat(64)},
  {path:'assets/app2.js', bytes:100, sha256:'c'.repeat(64)},
  {path:'assets/photo.PNG', bytes:200, sha256:'d'.repeat(64)},
  {path:'LICENSE', bytes:10, sha256:'e'.repeat(64)},
]
const preview = {siteId:'site-'+'a'.repeat(24),sha256:'a'.repeat(64),entrySha256:files[0].sha256,files:5,bytes:430}
beforeEach(() => vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,arrayBuffer:async () => new TextEncoder().encode(JSON.stringify({schemaVersion:1,...preview,files})).buffer}))))
afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks()})
const paths = () => screen.getAllByRole('listitem').map(item => item.querySelector('span').textContent)

it('combines type/path filters, reports visible bytes and clears filters without a refetch', async () => {
  render(<PixelTaskFiles preview={preview}/>)
  await screen.findByRole('button', {name:/index.html/})
  fireEvent.change(screen.getByLabelText('File type'), {target:{value:'js'}})
  expect(paths()).toEqual(['assets/app10.js', 'assets/app2.js'])
  expect(screen.getByText('Showing 2 of 5 files · 200 bytes')).toBeVisible()
  fireEvent.change(screen.getByLabelText('Filter task files'), {target:{value:'app2'}})
  expect(paths()).toEqual(['assets/app2.js'])
  fireEvent.click(screen.getByRole('button', {name:'Clear file filters'}))
  expect(paths()).toEqual(files.map(file => file.path))
  expect(fetch).toHaveBeenCalledOnce()
})

it('sorts numeric filenames and equal-size ties deterministically without mutating the manifest', async () => {
  render(<PixelTaskFiles preview={preview}/>)
  await screen.findByRole('button', {name:/index.html/})
  fireEvent.change(screen.getByLabelText('Sort task files'), {target:{value:'largest'}})
  expect(paths()).toEqual(['assets/photo.PNG','assets/app2.js','assets/app10.js','index.html','LICENSE'])
  fireEvent.change(screen.getByLabelText('Sort task files'), {target:{value:'smallest'}})
  expect(paths()[0]).toBe('LICENSE')
  fireEvent.change(screen.getByLabelText('Sort task files'), {target:{value:'name'}})
  expect(paths().slice(0,2)).toEqual(['assets/app2.js','assets/app10.js'])
  fireEvent.change(screen.getByLabelText('Sort task files'), {target:{value:'published'}})
  expect(paths()).toEqual(files.map(file => file.path))
})

it('supports extensionless files and keeps an absent filter visible after snapshot replacement', async () => {
  const {rerender} = render(<PixelTaskFiles preview={preview}/>)
  await screen.findByRole('button', {name:/index.html/})
  fireEvent.change(screen.getByLabelText('File type'), {target:{value:'(no extension)'}})
  expect(paths()).toEqual(['LICENSE'])
  const next = {...preview,siteId:'site-'+'b'.repeat(24),files:1,bytes:20}
  fetch.mockResolvedValue({ok:true,arrayBuffer:async () => new TextEncoder().encode(JSON.stringify({schemaVersion:1,...next,files:[files[0]]})).buffer})
  rerender(<PixelTaskFiles preview={next}/>)
  await screen.findByText('No files match your search.')
  expect(screen.getByLabelText('File type')).toHaveValue('(no extension)')
  fireEvent.click(screen.getByRole('button', {name:'Clear file filters'}))
  expect(paths()).toEqual(['index.html'])
})
