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

it('jumps to a verified line, bounds line numbers, and gives search control back explicitly', async () => {
  const {container} = render(<PixelPreviewSource preview={preview}/>)
  const input = await screen.findByRole('searchbox', {name:'Find in source'})
  const text = container.querySelector('pre').textContent
  const line = screen.getByRole('spinbutton',{name:'Go to source line'})
  fireEvent.change(line,{target:{value:'2'}})
  fireEvent.click(screen.getByRole('button',{name:'Go to line'}))
  expect(container.querySelector('[data-source-find-current]')).toHaveAttribute('data-line','2')
  expect(screen.getByRole('status')).toHaveTextContent('Line 2 of 3')
  fireEvent.change(line,{target:{value:'999'}})
  expect(screen.getByRole('button',{name:'Go to line'})).toBeDisabled()
  expect(container.querySelector('[data-source-find-current]')).toHaveAttribute('data-line','2')
  fireEvent.change(input,{target:{value:'LAST'}})
  expect(container.querySelector('[data-source-find-current]')).toHaveAttribute('data-line','3')
  expect(container.querySelector('pre').textContent).toBe(text)
})

it('supports case-sensitive literal search and resets the active matching line when toggled', async () => {
  const {container} = render(<PixelPreviewSource preview={preview}/>)
  const input = await screen.findByRole('searchbox', {name:'Find in source'})
  fireEvent.change(input,{target:{value:'last'}})
  expect(container.querySelector('[data-source-find-current]')).toHaveAttribute('data-line','3')
  fireEvent.click(screen.getByRole('checkbox',{name:'Match case'}))
  expect(screen.getByText('No matching lines')).toBeVisible()
  fireEvent.change(input,{target:{value:'LAST'}})
  expect(container.querySelector('[data-source-find-current]')).toHaveAttribute('data-line','3')
})

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
