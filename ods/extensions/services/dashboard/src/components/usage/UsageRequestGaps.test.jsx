import {fireEvent, render, screen, within} from '@testing-library/react'
import UsageView from './UsageView' // eslint-disable-line no-unused-vars

function show(middle, available) {
  render(<UsageView report={{
    source:{status:'ok',local_runtime:{request_count_available:available}},
    summary:{requests:6},daily:[
      {date:'2026-05-01',requests:2},
      {date:'2026-05-02',requests:middle},
      {date:'2026-05-03',requests:4},
    ],
  }} readiness={{status:'ready'}} range={{start:'2026-05-01'}} />)
  return screen.getByRole('region',{name:'Requests per day'})
}

test.each([[0,false],[null,true]])('does not plot an unavailable counter (%s, source=%s) as zero', (middle, available) => {
  const chart=show(middle,available)
  const path=within(chart).getByRole('img').querySelector('path')
  expect(path.getAttribute('d').match(/[ML]/g)).toEqual(['M','M'])
  const day=within(chart).getByRole('button',{name:'May 2: requests Unavailable'})
  fireEvent.focus(day)
  expect(within(chart).getByText('May 2 \u00b7 requests: Unavailable')).toBeVisible()
  expect(within(chart).queryByRole('button',{name:'May 2: requests 0'})).not.toBeInTheDocument()
})

test('continues plotting a measured zero and reports it exactly', () => {
  const chart=show(0,true)
  const path=within(chart).getByRole('img').querySelector('path')
  expect(path.getAttribute('d').match(/[ML]/g)).toEqual(['M','L','L'])
  const day=within(chart).getByRole('button',{name:'May 2: requests 0'})
  fireEvent.focus(day)
  expect(within(chart).getByText('May 2 \u00b7 requests: 0')).toBeVisible()
})
