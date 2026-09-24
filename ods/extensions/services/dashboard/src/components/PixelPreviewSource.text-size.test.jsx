import {createHash} from 'node:crypto'
import {readFileSync} from 'node:fs'
import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

const styles = readFileSync('src/components/pixel-file-changes.css', 'utf8') + readFileSync('src/components/pixel-source-find.css', 'utf8')
const source = '<p>Readable source</p>\r\nlast line\r\n'
function fixture(value, path = 'index.html') {
  const bytes = new TextEncoder().encode(value)
  fetch.mockResolvedValue({ok:true, arrayBuffer:async () => bytes.buffer})
  const sha256 = createHash('sha256').update(bytes).digest('hex')
  return {preview:{siteId:'site-' + 'a'.repeat(24), entrySha256:sha256}, file:{path, sha256, bytes:bytes.length}}
}
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
  Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:vi.fn().mockResolvedValue()}})
})
afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks(); delete navigator.clipboard})

it('enlarges verified highlighted source and restores its default without changing copied content', async () => {
  const {container} = render(<><style>{styles}</style><PixelPreviewSource {...fixture(source)}/></>)
  const size = await screen.findByRole('combobox', {name:'Source text size'})
  const line = container.querySelector('.code-line')
  const original = window.getComputedStyle(line).fontSize
  for (const [value, pixels] of [['comfortable','14px'], ['large','18px'], ['extra-large','22px']]) {
    fireEvent.change(size, {target:{value}})
    expect(window.getComputedStyle(container.querySelector('.code-line')).fontSize).toBe(pixels)
  }
  fireEvent.click(screen.getByRole('button', {name:'Wrap lines'}))
  expect(window.getComputedStyle(container.querySelector('.code-line-content')).whiteSpace).toBe('pre-wrap')
  expect(screen.getByLabelText('Code for index.html').textContent).toBe(source)
  fireEvent.click(screen.getByRole('button', {name:'Copy code'}))
  await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(source))
  fireEvent.change(size, {target:{value:'default'}})
  expect(window.getComputedStyle(container.querySelector('.code-line')).fontSize).toBe(original)
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('also enlarges the bounded plain-text fallback without adding line nodes', async () => {
  const large = 'x'.repeat(129 * 1024) + '\r\n'
  const {container} = render(<><style>{styles}</style><PixelPreviewSource {...fixture(large)}/></>)
  const size = await screen.findByRole('combobox', {name:'Source text size'})
  const code = container.querySelector('pre > code')
  fireEvent.change(size, {target:{value:'large'}})
  expect(window.getComputedStyle(code).fontSize).toBe('18px')
  expect(code.childElementCount).toBe(0)
  expect(code.textContent === large).toBe(true)
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('does not offer source typography for binary or unverified bytes', async () => {
  const {rerender} = render(<PixelPreviewSource {...fixture('binary bytes', 'asset.bin')}/>)
  await screen.findByText(/Binary asset/)
  expect(screen.queryByRole('combobox', {name:'Source text size'})).not.toBeInTheDocument()
  const props = fixture(source)
  props.file.sha256 = 'b'.repeat(64)
  rerender(<PixelPreviewSource {...props}/>)
  await screen.findByRole('alert')
  expect(screen.queryByRole('combobox', {name:'Source text size'})).not.toBeInTheDocument()
})
