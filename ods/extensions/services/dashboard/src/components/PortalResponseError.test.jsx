import {render,screen} from '@testing-library/react'
import PortalResponseError from './PortalResponseError'

it('explains a generic transport failure without claiming that the work succeeded',()=>{
  render(<PortalResponseError content="Request failed"/>);
  expect(screen.getByRole('status')).toHaveTextContent('The response could not be received.')
  expect(screen.getByRole('status')).toHaveTextContent('check the connection before continuing')
  expect(screen.queryByText('Request failed')).toBeNull()
})
it('retains actionable runtime error text',()=>{
  render(<PortalResponseError content="Could not save the request for recovery. No task was started. Check browser storage and try again."/>);
  expect(screen.getByRole('status')).toHaveTextContent('Check browser storage and try again.')
})
it('renders untrusted error content as bounded plain text',()=>{
  render(<PortalResponseError content={`# Not a heading\n[Misleading link](https://example.test) ${'x'.repeat(600)}`}/>);
  const status = screen.getByRole('status')
  expect(status).toHaveTextContent('# Not a heading [Misleading link](https://example.test)')
  expect(status.querySelector('h1, a')).toBeNull()
  expect(status.textContent.length).toBeLessThanOrEqual(501)
})
