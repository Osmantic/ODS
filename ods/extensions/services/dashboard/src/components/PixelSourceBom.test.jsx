import {webcrypto, createHash} from 'node:crypto'
import {render, screen, fireEvent, waitFor} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks()})

it.each(['index.html','notes.txt'])('preserves the verified UTF-8 BOM in displayed and copied %s source', async path => {
  const source = '\uFEFFfirst\r\nlast\n'
  const bytes = new TextEncoder().encode(source)
  const sha256 = createHash('sha256').update(bytes).digest('hex')
  const writeText = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('navigator', {clipboard:{writeText}})
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,arrayBuffer:async () => bytes.buffer})))
  const {container} = render(<PixelPreviewSource preview={{siteId:'site-'+'a'.repeat(24),entrySha256:sha256}} file={{path,sha256,bytes:bytes.length}}/> )
  await waitFor(() => expect(screen.getByRole('button',{name:'Copy code'})).toBeEnabled())
  const shown = container.querySelector('pre code').textContent
  expect(shown.charCodeAt(0)).toBe(0xFEFF)
  expect(shown).toBe(source)
  fireEvent.click(screen.getByRole('button',{name:'Copy code'}))
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(source))
  expect([...new TextEncoder().encode(writeText.mock.calls[0][0])]).toEqual([...bytes])
})
