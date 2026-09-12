import {fireEvent,render,screen} from '@testing-library/react'
import ServiceMap from './ServiceMap'
afterEach(()=>vi.unstubAllGlobals())
it.each([false,true])('moves focus into details and Escape restores the service trigger (compact=%s)',async compact=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({services:[{id:'ape',name:'APE',status:'healthy',port:7890}]})}))
  render(<ServiceMap compact={compact}/>)
  const trigger=await screen.findByRole('button',{name:/APE/})
  trigger.focus()
  if(compact)fireEvent.click(trigger)
  else fireEvent.keyDown(trigger,{key:'Enter'})
  const close=screen.getByRole('button',{name:'Close service details'})
  expect(close).toHaveFocus()
  fireEvent.keyDown(close,{key:'Escape'})
  expect(screen.queryByRole('button',{name:'Close service details'})).toBeNull()
  expect(trigger).toHaveFocus()
})
