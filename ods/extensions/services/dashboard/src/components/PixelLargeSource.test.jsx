import {webcrypto, createHash} from 'node:crypto'
import {render, screen, fireEvent, waitFor} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks()})

it.each(['x'.repeat(128 * 1024), 'line\n'.repeat(2001), '\n'.repeat(140000)])('renders large verified source as exact plain text with bounded DOM and truthful controls (%#)', async prefix => {
  const source = prefix + '\n<script>not executable</script>\r\n\n'
  const bytes = new TextEncoder().encode(source)
  const sha256 = createHash('sha256').update(bytes).digest('hex')
  vi.stubGlobal('crypto', webcrypto)
  const writeText = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal('navigator', {clipboard:{writeText}})
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,arrayBuffer:async()=>bytes.buffer})))
  const {container} = render(<PixelPreviewSource preview={{siteId:'site-'+'a'.repeat(24),entrySha256:sha256}}/> )
  await waitFor(() => expect(screen.getByRole('button',{name:'Copy code'})).toBeEnabled())
  expect(container.querySelector('pre code').textContent === source).toBe(true)
  expect(container.querySelector('pre code').childElementCount).toBe(0)
  expect(screen.queryByRole('searchbox',{name:'Find in source'})).toBeNull()
  expect(screen.getByText(/Large source is shown as plain text/)).toBeVisible()
  expect(container.querySelector('script')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'Copy code'}))
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(source))
  expect(screen.getByRole('button',{name:'Download index.html'})).toBeEnabled()
})
