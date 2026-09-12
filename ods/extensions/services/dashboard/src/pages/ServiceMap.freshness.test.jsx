import {fireEvent,render,screen,waitFor,within} from '@testing-library/react'
import ServiceMap from './ServiceMap'
const snapshot=status=>({services:[{id:'ape',name:'APE',status,port:7890}]})
afterEach(()=>vi.unstubAllGlobals())
it('refreshes selected details and removes them when the service disappears',async()=>{
  const fetch=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>snapshot('healthy')}).mockResolvedValueOnce({ok:true,json:async()=>snapshot('down')}).mockResolvedValue({ok:true,json:async()=>({services:[]})})
  vi.stubGlobal('fetch',fetch)
  render(<ServiceMap/>);
  fireEvent.click(await screen.findByRole('button',{name:'APE: healthy'}))
  fireEvent(document,new Event('visibilitychange'))
  await screen.findByRole('button',{name:'APE: down'})
  const detail=screen.getByRole('button',{name:'Close service details'}).parentElement.parentElement
  expect(within(detail).getByText('down')).toBeVisible()
  fireEvent(document,new Event('visibilitychange'))
  await waitFor(()=>expect(screen.queryByRole('button',{name:'Close service details'})).toBeNull())
})
it('marks the retained map stale after a refresh failure',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce({ok:true,json:async()=>snapshot('healthy')}).mockRejectedValue(new Error('offline')))
  render(<ServiceMap/>);await screen.findByRole('button',{name:'APE: healthy'})
  fireEvent(document,new Event('visibilitychange'))
  expect(await screen.findByRole('alert')).toHaveTextContent('last successful snapshot')
  expect(screen.getByRole('button',{name:'APE: healthy'})).toBeVisible()
})
