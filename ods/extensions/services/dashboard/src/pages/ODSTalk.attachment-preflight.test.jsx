import {fireEvent,screen} from '@testing-library/react'
import {render} from '../test/test-utils'
import ODSTalk from './ODSTalk'
beforeEach(()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({capabilities:{text_chat:true}})})))
  URL.createObjectURL=vi.fn(()=> 'blob:photo');URL.revokeObjectURL=vi.fn()
})
afterEach(()=>vi.unstubAllGlobals())
it.each([['big.png','image/png',10*1024*1024+1],['big.txt','text/plain',5*1024*1024+1],['document.pdf','application/pdf',20]])('rejects %s before clearing the caption or allocating a preview',async(name,type,size)=>{
  render(<ODSTalk/>);await screen.findByText('Ready')
  fireEvent.change(screen.getByPlaceholderText('Message ODS'),{target:{value:'Keep this caption'}})
  const file=new File(['x'],name,{type});Object.defineProperty(file,'size',{value:size})
  fireEvent.change(screen.getByLabelText('Attach image or file',{selector:'input'}),{target:{files:[file]}})
  await screen.findByRole('alert')
  expect(screen.getByPlaceholderText('Message ODS')).toHaveValue('Keep this caption')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  expect(fetch).toHaveBeenCalledTimes(1)
})
it('recognizes an octet-stream camera photo by its extension',async()=>{
  render(<ODSTalk/>);await screen.findByText('Ready')
  fireEvent.change(screen.getByLabelText('Attach image or file',{selector:'input'}),{target:{files:[new File(['photo'],'CAMERA.JPG',{type:'application/octet-stream'})]}})
  expect(screen.getByAltText('Attachment preview')).toHaveAttribute('src','blob:photo')
})
