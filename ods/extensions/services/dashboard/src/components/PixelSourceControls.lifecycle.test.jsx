import {createHash, webcrypto} from 'node:crypto'
import {render, screen, fireEvent, waitFor} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

const digest = value => createHash('sha256').update(value).digest('hex')
const preview = {siteId:'site-'+'a'.repeat(24), entrySha256:digest('first\nsecond\n')}
beforeEach(() => vi.stubGlobal('crypto', webcrypto))
afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks()})

it('removes old excerpt controls when switching verified files', async () => {
  let source = 'first\nsecond\n'
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,arrayBuffer:async () => new TextEncoder().encode(source).buffer})))
  const {rerender} = render(<PixelPreviewSource preview={preview}/>)
  await screen.findByRole('searchbox', {name:'Find in source'})
  fireEvent.click(screen.getByText('Extract lines'))
  fireEvent.change(screen.getByLabelText('Start line'), {target:{value:'2'}})
  for (const [path, text] of [['other.txt', 'new text\n'], ['third.txt', 'third text\n']]) {
    source = text
    rerender(<PixelPreviewSource preview={preview} file={{path,sha256:digest(text)}}/>)
    await screen.findByRole('region', {name:`Source: ${path}`})
    await waitFor(() => expect(screen.getByRole('textbox', {name:'Selected source excerpt', hidden:true})).toHaveValue(text))
    expect(screen.getAllByText('Extract lines')).toHaveLength(1)
    expect(screen.getAllByLabelText('Start line')).toHaveLength(1)
  }
})

it('does not retain the previous excerpt when the new file fails verification', async () => {
  const source = 'first\nsecond\n'
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,arrayBuffer:async () => new TextEncoder().encode(source).buffer})))
  const {rerender} = render(<PixelPreviewSource preview={preview}/>)
  await screen.findByRole('searchbox', {name:'Find in source'})
  fireEvent.click(screen.getByText('Extract lines'))
  rerender(<PixelPreviewSource preview={preview} file={{path:'broken.txt',sha256:'b'.repeat(64)}}/>)
  await screen.findByRole('alert')
  expect(screen.queryByText('Extract lines')).toBeNull()
  expect(screen.queryByRole('textbox', {name:'Selected source excerpt',hidden:true})).toBeNull()
  expect(screen.queryByRole('searchbox')).toBeNull()
})
