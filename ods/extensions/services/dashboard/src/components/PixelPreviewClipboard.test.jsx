import {webcrypto, createHash} from 'node:crypto'
import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

const source = '<!doctype html><h1>Exact clipboard content</h1>'
const preview = {siteId:'site-'+'a'.repeat(24),entrySha256:createHash('sha256').update(source).digest('hex')}
beforeEach(() => vi.stubGlobal('crypto', webcrypto))
afterEach(() => {vi.unstubAllGlobals();vi.restoreAllMocks()})

it('clears a clipboard refusal after a successful retry without refetching source', async () => {
  const writeText = vi.fn().mockRejectedValueOnce(new Error('Clipboard denied')).mockResolvedValueOnce(undefined)
  vi.stubGlobal('navigator', {clipboard: {writeText}})
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,arrayBuffer:async () => new TextEncoder().encode(source).buffer})))
  render(<PixelPreviewSource preview={preview}/>)
  const copy = screen.getByRole('button',{name:'Copy code'})
  await waitFor(() => expect(copy).toBeEnabled())
  fireEvent.click(copy)
  expect(await screen.findByRole('alert')).toHaveTextContent('Clipboard access failed')
  fireEvent.click(copy)
  await waitFor(() => expect(copy).toHaveTextContent('Copied'))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(writeText).toHaveBeenNthCalledWith(2, source)
  expect(fetch).toHaveBeenCalledOnce()
})

it('does not retain Copied feedback when a later clipboard write fails', async () => {
  const writeText = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('Clipboard denied'))
  vi.stubGlobal('navigator', {clipboard: {writeText}})
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,arrayBuffer:async () => new TextEncoder().encode(source).buffer})))
  render(<PixelPreviewSource preview={preview}/>)
  const copy = screen.getByRole('button',{name:'Copy code'})
  await waitFor(() => expect(copy).toBeEnabled())
  fireEvent.click(copy)
  await waitFor(() => expect(copy).toHaveTextContent('Copied'))
  fireEvent.click(copy)
  await screen.findByRole('alert')
  expect(copy).not.toHaveTextContent('Copied')
  expect(copy).toBeEnabled()
})
