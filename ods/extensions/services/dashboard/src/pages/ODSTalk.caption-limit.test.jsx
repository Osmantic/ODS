import {fireEvent,render,screen} from '@testing-library/react'
import ODSTalk from './ODSTalk'

beforeEach(()=>vi.stubGlobal('fetch',vi.fn(url=>url==='/api/talk/status' ? Promise.resolve({ok:true,json:async()=>({capabilities:{text_chat:true}})}) : new Promise(()=>{}))))
afterEach(()=>vi.unstubAllGlobals())
it('retains an oversized caption and attachment without an invalid POST',async()=>{
  render(<ODSTalk/>);await screen.findByText('Ready')
  fireEvent.change(document.querySelector('input[type=file]'),{target:{files:[new File(['contents'],'keep.txt',{type:'text/plain'})]}})
  const field=screen.getByPlaceholderText('Message ODS')
  fireEvent.change(field,{target:{value:'x'.repeat(8001)}})
  expect(screen.getByRole('button',{name:'Send message'})).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('8,000')
  fireEvent.submit(field.closest('form'))
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(field.value).toHaveLength(8001)
  expect(screen.getByText('keep.txt')).toBeInTheDocument()
  fireEvent.change(field,{target:{value:'short caption'}})
  expect(screen.getByRole('button',{name:'Send message'})).toBeEnabled()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
it('counts Unicode code points like the Python API instead of UTF-16 units',async()=>{
  render(<ODSTalk/>);await screen.findByText('Ready')
  const text=String.fromCodePoint(0x1f680).repeat(8000)
  fireEvent.change(screen.getByPlaceholderText('Message ODS'),{target:{value:text}})
  expect(screen.getByRole('button',{name:'Send message'})).toBeEnabled()
  fireEvent.click(screen.getByRole('button',{name:'Send message'}))
  expect(JSON.parse(fetch.mock.calls[1][1].body).text).toBe(text)
})
