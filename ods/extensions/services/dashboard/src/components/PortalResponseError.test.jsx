import {render,screen} from '@testing-library/react'
import PortalResponseError from './PortalResponseError'

it('explains a generic transport failure without claiming that the work succeeded',()=>{
  render(<PortalResponseError content="Request failed"/>);
  expect(screen.getByRole('status')).toHaveTextContent('The response could not be received.')
  expect(screen.getByRole('status')).toHaveTextContent('check the connection before continuing')
  expect(screen.queryByText('Request failed')).toBeNull()
})
it('names the cause of a generic failure when the latest model call failed',async()=>{
  // Fleet drills: a refused key looked like every other failure in Portal.
  const message='The model API refused the key ODS uses (HTTP 401). Connect it again with a current key in Settings > Remote model.'
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({cause:'key_refused',status:401,message})})))
  render(<PortalResponseError content="Portal could not complete the response. Check saved work before continuing."/>)
  expect(await screen.findByText(message)).toBeInTheDocument()
  expect(fetch).toHaveBeenCalledWith('/api/pixel/model-failure',expect.anything())
  expect(screen.getByRole('link',{name:/Get help on Discord/})).toBeVisible()
  vi.unstubAllGlobals()
})
it('asks nothing for a failure that already says what to do',async()=>{
  vi.stubGlobal('fetch',vi.fn())
  render(<PortalResponseError content="Could not save the request for recovery. No task was started. Check browser storage and try again."/>)
  await Promise.resolve()
  expect(fetch).not.toHaveBeenCalled()
  vi.unstubAllGlobals()
})
it('retains actionable runtime error text',()=>{
  render(<PortalResponseError content="Could not save the request for recovery. No task was started. Check browser storage and try again."/>);
  expect(screen.getByRole('status')).toHaveTextContent('Check browser storage and try again.')
})
