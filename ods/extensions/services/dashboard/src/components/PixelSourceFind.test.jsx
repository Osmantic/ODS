import {webcrypto, createHash} from 'node:crypto'
import {render, screen, fireEvent, waitFor} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

const source = 'first [a.*]\n<script>second</script>\nLAST [a.*]\n'
const digest = createHash('sha256').update(source).digest('hex')
const preview = {siteId:'site-'+'a'.repeat(24), entrySha256:digest}
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true, arrayBuffer:async () => new TextEncoder().encode(source).buffer})))
  globalThis.Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks(); delete globalThis.Element.prototype.scrollIntoView})

it('finds literal matching lines in verified source without altering its text', async () => {
  const {container} = render(<PixelPreviewSource preview={preview}/>)
  const input = await screen.findByRole('searchbox', {name:'Find in source'})
  const text = container.querySelector('pre').textContent
  fireEvent.change(input, {target:{value:'[A.*]'}})
  expect(screen.getByText('1 of 2 matching lines · Line 1')).toBeVisible()
  expect(container.querySelector('[data-source-find-current]')).toHaveAttribute('data-line', '1')
  fireEvent.click(screen.getByRole('button', {name:'Next matching line'}))
  expect(container.querySelector('[data-source-find-current]')).toHaveAttribute('data-line', '3')
  fireEvent.keyDown(input, {key:'Enter'})
  expect(container.querySelector('[data-source-find-current]')).toHaveAttribute('data-line', '1')
  fireEvent.keyDown(input, {key:'Enter', shiftKey:true})
  expect(container.querySelector('[data-source-find-current]')).toHaveAttribute('data-line', '3')
  expect(container.querySelector('pre').textContent).toBe(text)
  expect(container.querySelector('script')).toBeNull()
})

it('clears selection on empty/missing queries and resets it for a different snapshot', async () => {
  const {container, rerender} = render(<PixelPreviewSource preview={preview}/>)
  const input = await screen.findByRole('searchbox', {name:'Find in source'})
  fireEvent.change(input, {target:{value:'missing'}})
  expect(screen.getByText('No matching lines')).toBeVisible()
  expect(screen.getByRole('button', {name:'Next matching line'})).toBeDisabled()
  fireEvent.change(input, {target:{value:'first'}})
  fireEvent.keyDown(input, {key:'Escape'})
  expect(container.querySelector('[data-source-find-current]')).toBeNull()
  fireEvent.change(input, {target:{value:'first'}})
  rerender(<PixelPreviewSource preview={{...preview,siteId:'site-'+'b'.repeat(24)}}/>)
  await waitFor(() => expect(screen.getByRole('searchbox', {name:'Find in source'})).toHaveValue(''))
})

it('never exposes search over source that failed verification', async () => {
  render(<PixelPreviewSource preview={{...preview,entrySha256:'b'.repeat(64)}}/>)
  await screen.findByRole('alert')
  expect(screen.queryByRole('searchbox', {name:'Find in source'})).toBeNull()
})
