import {webcrypto,createHash} from 'node:crypto'
import {fireEvent,render,screen} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

const text='first\r\n<script>inert</script>\r\nlast\n'
beforeEach(()=>{
  vi.stubGlobal('crypto',webcrypto)
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,arrayBuffer:async()=>new TextEncoder().encode(text).buffer})))
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:vi.fn().mockResolvedValue()}})
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})
function setup(){return render(<PixelPreviewSource preview={{siteId:'site-'+'a'.repeat(24),entrySha256:createHash('sha256').update(text).digest('hex')}}/>)}
it('copies only the chosen verified lines with original line endings',async()=>{
  const {container}=setup()
  fireEvent.click(await screen.findByText('Extract lines'))
  fireEvent.change(screen.getByLabelText('Start line'),{target:{value:'2'}})
  fireEvent.change(screen.getByLabelText('End line'),{target:{value:'3'}})
  fireEvent.click(screen.getByRole('button',{name:'Copy excerpt'}))
  await screen.findByText('Excerpt copied.')
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith('<script>inert</script>\r\nlast\n')
  expect(container.querySelector('script')).toBeNull()
})
it('rejects invalid ranges and offers manual selection when clipboard fails',async()=>{
  setup()
  fireEvent.click(await screen.findByText('Extract lines'))
  fireEvent.change(screen.getByLabelText('Start line'),{target:{value:'9'}})
  expect(screen.getByRole('button',{name:'Copy excerpt'})).toBeDisabled()
  expect(navigator.clipboard.writeText).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Start line'),{target:{value:'1'}})
  navigator.clipboard.writeText.mockRejectedValue(new Error('denied'))
  fireEvent.click(screen.getByRole('button',{name:'Copy excerpt'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('Select the excerpt')
  expect(screen.getByLabelText('Selected source excerpt')).toHaveValue(text.replaceAll('\r\n','\n'))
})
