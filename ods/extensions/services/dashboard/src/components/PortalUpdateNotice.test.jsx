import { fireEvent, render, screen } from '@testing-library/react'
import PortalUpdateNotice from './PortalUpdateNotice'

it('offers review and dismissal of a confirmed release without starting installation', () => {
  const onReview = vi.fn(), onDismiss = vi.fn()
  render(<PortalUpdateNotice version={{ current:'2.6.0', latest:'2.7.0', update_available:true, check_status:'checked' }} onReview={onReview} onDismiss={onDismiss} />)
  expect(screen.getByRole('status')).toHaveTextContent('ODS 2.7.0 is available')
  expect(screen.getByText('Installed version: 2.6.0')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', {name:'View update'}))
  expect(onReview).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByRole('button', {name:'Dismiss this update'}))
  expect(onDismiss).toHaveBeenCalledOnce()
})

it.each(['checking','stale','unavailable','current-unknown'])('does not announce an unverified %s release', check_status => {
  render(<PortalUpdateNotice version={{ current:'2.6.0', latest:'2.7.0', update_available:true, check_status }} />)
  expect(screen.queryByRole('status')).toBeNull()
})

it.each(['', '2.6.0'])('does not announce an empty or identical installed version during a rolling upgrade (%s)', current=>{
  render(<PortalUpdateNotice version={{current,latest:'2.6.0',update_available:true}}/>)
  expect(screen.queryByRole('status')).toBeNull()
})
