import {webcrypto, createHash} from 'node:crypto'
import {render, screen, fireEvent, waitFor} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

const source = '<div>'+ 'long-unbroken-content'.repeat(80) + '</div>\r\nsecond line\r\n'
const digest = createHash('sha256').update(source).digest('hex')
const preview = {siteId:'site-'+'a'.repeat(24),entrySha256:digest}
beforeEach(()=>{
  vi.stubGlobal('crypto',webcrypto)
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,arrayBuffer:async()=>new TextEncoder().encode(source).buffer})))
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:vi.fn().mockResolvedValue(undefined)}})
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks();delete navigator.clipboard})

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
