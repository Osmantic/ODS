import {createHash} from 'node:crypto'
import {fireEvent, render, screen} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

let click
beforeEach(() => {
  vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:excerpt')
  vi.spyOn(URL,'revokeObjectURL').mockImplementation(() => {})
  click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(() => {})
})
afterEach(() => {vi.restoreAllMocks();vi.unstubAllGlobals()})
async function show(source, valid=true) {
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,arrayBuffer:async()=>new TextEncoder().encode(source).buffer})))
  render(<PixelPreviewSource preview={{siteId:'site-'+'a'.repeat(24),entrySha256:valid ? createHash('sha256').update(source).digest('hex') : 'b'.repeat(64)}}/>)
  if(valid) fireEvent.click(await screen.findByText('Extract lines'))
}
it('downloads exactly the selected verified UTF-8 lines, preserving CRLF and final newline', async () => {
  await show('first\r\nViệt 🌱\r\n<script>inert</script>\nlast')
  fireEvent.change(screen.getByLabelText('Start line'),{target:{value:'2'}})
  fireEvent.change(screen.getByLabelText('End line'),{target:{value:'3'}})
  fireEvent.click(screen.getByRole('button',{name:'Download excerpt'}))
  const blob=URL.createObjectURL.mock.calls[0][0]
  const bytes=await new Promise(resolve=>{const reader=new globalThis.FileReader();reader.onload=()=>resolve(reader.result);reader.readAsArrayBuffer(blob)})
  expect(new TextDecoder().decode(bytes)).toBe('Việt 🌱\r\n<script>inert</script>\n')
  expect(click.mock.instances[0].download).toBe('ods-source-lines-2-3.txt')
  expect(click.mock.instances[0].isConnected).toBe(false)
  expect(screen.getByText('Excerpt download started.')).toBeVisible()
  expect(document.querySelector('script')).toBeNull()
})
it('retains the same byte and range limits as copying', async () => {
  await show('é'.repeat(32769)+'\nshort\n')
  expect(screen.getByRole('button',{name:'Download excerpt'})).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Start line'),{target:{value:'2'}})
  expect(screen.getByRole('button',{name:'Download excerpt'})).toBeEnabled()
  fireEvent.change(screen.getByLabelText('End line'),{target:{value:'1'}})
  expect(screen.getByRole('button',{name:'Download excerpt'})).toBeDisabled()
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})
it('does not expose a download after verification failure', async () => {
  await show('unverified',false)
  await screen.findByRole('alert')
  expect(screen.queryByRole('button',{name:'Download excerpt'})).toBeNull()
})
it('reports a browser failure, cleans up and supports retry', async () => {
  await show('verified')
  click.mockImplementationOnce(()=>{throw new Error('blocked')})
  fireEvent.click(screen.getByRole('button',{name:'Download excerpt'}))
  expect(screen.getByRole('alert')).toHaveTextContent('download could not start')
  expect(document.querySelector('a[download]')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'Download excerpt'}))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(click).toHaveBeenCalledTimes(2)
})
