import {webcrypto, createHash} from 'node:crypto'
import {render, screen, fireEvent, waitFor} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'
import {readFileSync} from 'node:fs'

// Vitest stubs CSS imports; read the actual stylesheet for cascade assertions.
const sourceStyles=readFileSync('src/components/pixel-file-changes.css','utf8')
const findStyles=readFileSync('src/components/pixel-source-find.css','utf8')
const getComputedStyle=element=>window.getComputedStyle(element)

const source = '<div>'+ 'long-unbroken-content'.repeat(80) + '</div>\r\nsecond line\r\n'
const digest = createHash('sha256').update(source).digest('hex')
const preview = {siteId:'site-'+'a'.repeat(24),entrySha256:digest}
beforeEach(()=>{
  vi.stubGlobal('crypto',webcrypto)
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,arrayBuffer:async()=>new TextEncoder().encode(source).buffer})))
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:vi.fn().mockResolvedValue(undefined)}})
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks();delete navigator.clipboard})

it('applies wrapping to plain code as well as highlighted line spans', () => {
  const {container,rerender}=render(<section className="pixel-preview-source" data-wrap-lines="true"><style>{sourceStyles + findStyles}</style><div className="pixel-code-block"><pre><code>unbroken-plain-source</code></pre></div></section>)
  const code=container.querySelector('code')
  expect(getComputedStyle(code).whiteSpace).toBe('pre-wrap')
  expect(getComputedStyle(code).overflowWrap).toBe('anywhere')
  expect(getComputedStyle(code).minWidth).toBe('0px')
  rerender(<section className="pixel-preview-source" data-wrap-lines="false"><style>{sourceStyles + findStyles}</style><div className="pixel-code-block"><pre><code>unbroken-plain-source</code></pre></div></section>)
  expect(getComputedStyle(code).whiteSpace).not.toBe('pre-wrap')
})

it('wraps verified source for reading without changing logical lines, copying or refetching',async()=>{
  const {container}=render(<PixelPreviewSource preview={preview}/>)
  const toggle=await screen.findByRole('button',{name:'Wrap lines'})
  const code=screen.getByLabelText('Code for index.html')
  expect(toggle).toHaveAttribute('aria-pressed','false')
  fireEvent.click(toggle)
  expect(toggle).toHaveAttribute('aria-pressed','true')
  expect(code.closest('section')).toHaveAttribute('data-wrap-lines','true')
  expect(code.textContent).toBe(source)
  expect(container.querySelectorAll('[data-line]')).toHaveLength(2)
  fireEvent.click(screen.getByRole('button',{name:'Copy code'}))
  await waitFor(()=>expect(navigator.clipboard.writeText).toHaveBeenCalledWith(source))
  fireEvent.click(toggle)
  expect(code.closest('section')).toHaveAttribute('data-wrap-lines','false')
  expect(code.textContent).toBe(source)
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('wraps the real large-source fallback while preserving exact bytes and bounded DOM', async () => {
  const large='x'.repeat(129*1024)+'\r\nlast line\r\n'
  const entrySha256=createHash('sha256').update(large).digest('hex')
  fetch.mockResolvedValue({ok:true,arrayBuffer:async()=>new TextEncoder().encode(large).buffer})
  const {container}=render(<><style>{sourceStyles + findStyles}</style><PixelPreviewSource preview={{...preview,entrySha256}}/></>)
  const toggle=await screen.findByRole('button',{name:'Wrap lines'})
  const code=container.querySelector('pre > code')
  expect(code.childElementCount).toBe(0)
  expect(code.textContent===large).toBe(true)
  fireEvent.click(toggle)
  expect(getComputedStyle(code).whiteSpace).toBe('pre-wrap')
  expect(getComputedStyle(code).overflowWrap).toBe('anywhere')
  expect(code.childElementCount).toBe(0)
  fireEvent.click(screen.getByRole('button',{name:'Copy code'}))
  await waitFor(()=>expect(navigator.clipboard.writeText).toHaveBeenCalledWith(large))
  fireEvent.click(toggle)
  expect(getComputedStyle(code).whiteSpace).not.toBe('pre-wrap')
  expect(code.textContent===large).toBe(true)
  expect(fetch).toHaveBeenCalledTimes(1)
})
