import {act,fireEvent,render,screen,waitFor} from '@testing-library/react'
import ODSTalk from './ODSTalk'
const ready=()=>({ok:true,status:200,json:async()=>({capabilities:{text_chat:true}})})
afterEach(()=>{vi.unstubAllGlobals();vi.useRealTimers()})
it.each([true,false])('keeps the newest session verdict when older response resolves (expired=%s)',async expired=>{
  let finish
  vi.stubGlobal('fetch',vi.fn().mockImplementationOnce(()=>new Promise(r=>{finish=r})).mockResolvedValueOnce(expired?{status:401}:ready()))
  render(<ODSTalk/>)
  fireEvent.click(screen.getByRole('button',{name:'Refresh ODS Talk status'}))
  await waitFor(()=>expect(screen.getByPlaceholderText('Message ODS').disabled).toBe(expired))
  await act(async()=>finish(expired?ready():{status:401}))
  expect(screen.getByPlaceholderText('Message ODS').disabled).toBe(expired)
  if(!expired)expect(screen.getByText('Ready')).toBeInTheDocument()
})
it('settles a stuck status JSON body and permits a later refresh',async()=>{
  vi.useFakeTimers()
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce({ok:true,status:200,json:()=>new Promise(()=>{})}).mockResolvedValue(ready()))
  render(<ODSTalk/>)
  await act(async()=>{await vi.advanceTimersByTimeAsync(30000)})
  expect(screen.getAllByText(/status request timed out/i).length).toBeGreaterThan(0)
  fireEvent.click(screen.getByRole('button',{name:'Refresh ODS Talk status'}))
  await act(async()=>{})
  expect(screen.getByText('Ready')).toBeInTheDocument()
})
