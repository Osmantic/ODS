import {webcrypto,createHash} from 'node:crypto'
import {act,fireEvent,render,screen} from '@testing-library/react'
import PixelPreviewSource from './PixelPreviewSource'

const text='first\r\n<script>inert</script>\r\nlast\n'
beforeEach(()=>{
  vi.stubGlobal('crypto',webcrypto)
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,arrayBuffer:async()=>new TextEncoder().encode(text).buffer})))
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:vi.fn().mockResolvedValue()}})
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})
function setup(value=text){return render(<PixelPreviewSource preview={{siteId:'site-'+'a'.repeat(24),entrySha256:createHash('sha256').update(value).digest('hex')}}/>)}
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

it.each([32768,32769])('enforces the 64 KiB limit in UTF-8 bytes (%s multibyte characters)',async count=>{
  const value='?'.repeat(count)
  fetch.mockResolvedValue({ok:true,arrayBuffer:async()=>new TextEncoder().encode(value).buffer})
  setup(value)
  fireEvent.click(await screen.findByText('Extract lines'))
  const copy=screen.getByRole('button',{name:'Copy excerpt'})
  if(count===32768){
    expect(copy).toBeEnabled()
    fireEvent.click(copy)
    await screen.findByText('Excerpt copied.')
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(value)
  }else{
    expect(copy).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('within 64 KiB')
    expect(screen.queryByLabelText('Selected source excerpt')).toBeNull()
  }
})
it('does not confirm an old clipboard request after the selected range changes',async()=>{
  let resolve
  navigator.clipboard.writeText.mockReturnValue(new Promise(done=>{resolve=done}))
  setup();fireEvent.click(await screen.findByText('Extract lines'))
  fireEvent.click(screen.getByRole('button',{name:'Copy excerpt'}))
  fireEvent.change(screen.getByLabelText('End line'),{target:{value:'1'}})
  await act(async()=>resolve())
  expect(screen.queryByText('Excerpt copied.')).toBeNull()
  expect(screen.getByLabelText('Selected source excerpt')).toHaveValue('first\n')
})
